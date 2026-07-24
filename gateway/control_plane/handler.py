"""API Gateway HTTP API handler for the multi-tenant Fargate control plane."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from gateway.control_plane.authorizer import (
    AuthorizedTenant,
    BearerAuthorizer,
    iam_principal_is_allowed,
)
from gateway.control_plane.contracts import (
    AgentRun,
    AgentRunRepository,
    AgentRunSource,
    SizeProfile,
    TenantDeployment,
)
from gateway.control_plane.lifecycle import (
    LifecycleService,
    ProvisionGatewayResult,
    RotatedApiCredential,
)

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 1 * 1024 * 1024
_MAX_PROMPT_LENGTH = 100_000
_MAX_SOURCE_EVENT_ID_LENGTH = 256
_ORGANIZATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_LIFECYCLE_PATH = re.compile(
    r"^/v1/organizations/(?P<organization_id>[A-Za-z0-9][A-Za-z0-9_-]{0,127})"
    r"/gateway(?P<operation>/start|/stop)?$"
)
_ROTATE_PATH = re.compile(
    r"^/v1/organizations/(?P<organization_id>[A-Za-z0-9][A-Za-z0-9_-]{0,127})"
    r"/api-credential/rotate$"
)
_RUN_PATH = re.compile(r"^/v1/runs/(?P<run_id>[A-Za-z0-9-]{1,128})$")


class ControlPlaneApi:
    """Dependency-injected Lambda application with no import-time AWS calls."""

    def __init__(
        self,
        *,
        lifecycle: LifecycleService,
        runs: AgentRunRepository,
        bearer_authorizer: BearerAuthorizer,
        allowed_lifecycle_role_arns: frozenset[str],
    ) -> None:
        self._lifecycle = lifecycle
        self._runs = runs
        self._bearer_authorizer = bearer_authorizer
        self._allowed_lifecycle_role_arns = allowed_lifecycle_role_arns

    def handle(self, event: dict[str, Any], _context: object = None) -> dict[str, Any]:
        """Dispatch an HTTP proxy request or an HTTP API Lambda-authorizer request."""

        if _is_authorizer_event(event):
            return self.authorize_run_route(event)
        try:
            method, path = _method_and_path(event)
            if path.startswith("/v1/organizations/"):
                return self._handle_lifecycle(event, method, path)
            if path == "/v1/runs" or _RUN_PATH.fullmatch(path):
                return self._handle_run(event, method, path)
            return _response(404, {"error": "not_found"})
        except ClientRequestError as error:
            return _response(error.status_code, {"error": error.code})
        except Exception as error:  # noqa: BLE001 - Lambda boundary returns generic failures
            logger.error("Control-plane request failed (%s)", type(error).__name__)
            return _response(500, {"error": "internal_error"})

    def authorize_run_route(self, event: dict[str, Any]) -> dict[str, Any]:
        """Return an HTTP API v2 simple authorizer response."""

        authorization = _header(event, "authorization")
        tenant = self._bearer_authorizer.authorize(authorization)
        if tenant is None:
            return {"isAuthorized": False}
        return {
            "isAuthorized": True,
            "context": {
                "organization_id": tenant.organization_id,
                "key_id": tenant.key_id,
            },
        }

    def _handle_lifecycle(
        self,
        event: dict[str, Any],
        method: str,
        path: str,
    ) -> dict[str, Any]:
        if not iam_principal_is_allowed(event, self._allowed_lifecycle_role_arns):
            return _response(403, {"error": "forbidden"})

        lifecycle_match = _LIFECYCLE_PATH.fullmatch(path)
        if lifecycle_match is not None:
            organization_id = lifecycle_match.group("organization_id")
            operation = lifecycle_match.group("operation")
            if operation is None and method == "PUT":
                payload = _json_body(event)
                size_profile = _parse_size_profile(payload)
                result = self._lifecycle.provision_gateway(organization_id, size_profile)
                return _response(200, _provision_payload(result))
            if operation is None and method == "GET":
                deployment = self._lifecycle.get_gateway(organization_id)
                if deployment is None:
                    return _response(404, {"error": "not_found"})
                return _response(200, {"deployment": _jsonable(deployment)})
            if operation == "/start" and method == "POST":
                return _deployment_response(self._lifecycle.start_gateway(organization_id))
            if operation == "/stop" and method == "POST":
                return _deployment_response(self._lifecycle.stop_gateway(organization_id))
            if operation is None and method == "DELETE":
                return _deployment_response(self._lifecycle.delete_gateway(organization_id))
            return _response(405, {"error": "method_not_allowed"})

        rotate_match = _ROTATE_PATH.fullmatch(path)
        if rotate_match is not None:
            if method != "POST":
                return _response(405, {"error": "method_not_allowed"})
            rotated_result = self._lifecycle.rotate_api_credential(
                rotate_match.group("organization_id")
            )
            return _response(200, _rotated_credential_payload(rotated_result))
        return _response(404, {"error": "not_found"})

    def _handle_run(
        self,
        event: dict[str, Any],
        method: str,
        path: str,
    ) -> dict[str, Any]:
        tenant = _authorized_tenant_context(event)
        if tenant is None:
            return _response(401, {"error": "unauthorized"})

        if path == "/v1/runs":
            if method != "POST":
                return _response(405, {"error": "method_not_allowed"})
            payload = _json_body(event)
            prompt, source_event_id = _parse_run_payload(payload)
            run = self._runs.enqueue_run(
                organization_id=tenant.organization_id,
                source=AgentRunSource.API,
                prompt=prompt,
                source_event_id=source_event_id,
            )
            return _response(202, {"run": _public_run(run)})

        run_match = _RUN_PATH.fullmatch(path)
        if run_match is None:
            return _response(404, {"error": "not_found"})
        if method != "GET":
            return _response(405, {"error": "method_not_allowed"})
        run_id = run_match.group("run_id")
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return _response(404, {"error": "not_found"})
        found_run = self._runs.get_run(run_id)
        if found_run is None or found_run.organization_id != tenant.organization_id:
            return _response(404, {"error": "not_found"})
        return _response(200, {"run": _public_run(found_run)})


class ClientRequestError(ValueError):
    """A client-controlled parse or validation failure."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def _method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    request_context = event.get("requestContext")
    http = request_context.get("http") if isinstance(request_context, dict) else None
    method = http.get("method") if isinstance(http, dict) else event.get("httpMethod")
    path = event.get("rawPath") or event.get("path")
    if not isinstance(method, str) or not isinstance(path, str):
        raise ClientRequestError(400, "invalid_request")
    return method.upper(), path.rstrip("/") or "/"


def _is_authorizer_event(event: dict[str, Any]) -> bool:
    return event.get("type") == "REQUEST" and isinstance(event.get("routeArn"), str)


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None
    expected = name.lower()
    for raw_name, value in headers.items():
        if str(raw_name).lower() == expected and isinstance(value, str):
            return value
    return None


def _authorized_tenant_context(event: dict[str, Any]) -> AuthorizedTenant | None:
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return None
    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return None
    lambda_context = authorizer.get("lambda")
    if not isinstance(lambda_context, dict):
        return None
    organization_id = lambda_context.get("organization_id")
    key_id = lambda_context.get("key_id")
    if (
        not isinstance(organization_id, str)
        or _ORGANIZATION_ID_PATTERN.fullmatch(organization_id) is None
        or not isinstance(key_id, str)
        or not key_id
    ):
        return None
    return AuthorizedTenant(organization_id=organization_id, key_id=key_id)


def _json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body")
    if not isinstance(raw_body, str):
        raise ClientRequestError(400, "invalid_json")
    try:
        body = (
            base64.b64decode(raw_body, validate=True)
            if event.get("isBase64Encoded")
            else raw_body.encode()
        )
    except (ValueError, TypeError):
        raise ClientRequestError(400, "invalid_json") from None
    if len(body) > _MAX_BODY_BYTES:
        raise ClientRequestError(413, "payload_too_large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ClientRequestError(400, "invalid_json") from None
    if not isinstance(payload, dict):
        raise ClientRequestError(400, "invalid_request")
    return payload


def _parse_size_profile(payload: dict[str, Any]) -> SizeProfile:
    unexpected = set(payload) - {"size_profile"}
    if unexpected:
        raise ClientRequestError(400, "invalid_request")
    raw_profile = payload.get("size_profile", SizeProfile.SMALL.value)
    try:
        return SizeProfile(raw_profile)
    except (TypeError, ValueError):
        raise ClientRequestError(400, "invalid_size_profile") from None


def _parse_run_payload(payload: dict[str, Any]) -> tuple[str, str | None]:
    if "organization_id" in payload:
        raise ClientRequestError(400, "organization_not_allowed")
    if set(payload) - {"prompt", "source_event_id"}:
        raise ClientRequestError(400, "invalid_request")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ClientRequestError(400, "invalid_prompt")
    if len(prompt) > _MAX_PROMPT_LENGTH:
        raise ClientRequestError(413, "prompt_too_large")
    source_event_id = payload.get("source_event_id")
    if source_event_id is not None and (
        not isinstance(source_event_id, str)
        or not source_event_id
        or len(source_event_id) > _MAX_SOURCE_EVENT_ID_LENGTH
    ):
        raise ClientRequestError(400, "invalid_source_event_id")
    return prompt, source_event_id


def _public_run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source": run.source.value,
        "status": run.status.value,
        "attempt_count": run.attempt_count,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "result": run.result,
        "error_code": run.error_code,
    }


def _deployment_response(deployment: TenantDeployment) -> dict[str, Any]:
    return _response(200, {"deployment": _jsonable(deployment)})


def _provision_payload(result: ProvisionGatewayResult) -> dict[str, Any]:
    payload = {"deployment": _jsonable(result.deployment)}
    if result.api_credential is not None:
        payload["api_credential"] = result.api_credential
    return payload


def _rotated_credential_payload(result: RotatedApiCredential) -> dict[str, Any]:
    return {
        "key_id": result.key_id,
        "api_credential": result.api_credential,
    }


def _jsonable(value: object) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(payload, separators=(",", ":"), sort_keys=True),
        "isBase64Encoded": False,
    }
