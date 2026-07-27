"""API Gateway HTTP API handler for public /v1/runs routes."""

from __future__ import annotations

import logging
import re
from typing import Any

from platform.deployment_fargate.control_plane.contracts.contracts import (
    AgentRun,
    AgentRunRepository,
    AgentRunSource,
)
from platform.deployment_fargate.http_lambda import (
    ClientRequestError,
    json_body,
    method_and_path,
    organization_id_pattern,
    response,
    run_id_pattern,
)
from platform.deployment_fargate.public_api.authorizer import AuthorizedTenant

logger = logging.getLogger(__name__)

_MAX_PROMPT_LENGTH = 100_000
_MAX_SOURCE_EVENT_ID_LENGTH = 256
_RUN_PATH = re.compile(r"^/v1/runs/(?P<run_id>[A-Za-z0-9-]{1,128})$")
_ORGANIZATION_ID_PATTERN = organization_id_pattern()
_RUN_ID_PATTERN = run_id_pattern()


class PublicApiHandler:
    """Dependency-injected Lambda application for bearer-protected run routes."""

    def __init__(self, *, runs: AgentRunRepository) -> None:
        self._runs = runs

    def handle(self, event: dict[str, Any], _context: object = None) -> dict[str, Any]:
        """Dispatch an HTTP proxy request for /v1/runs routes."""

        try:
            method, path = method_and_path(event)
            if path == "/v1/runs" or _RUN_PATH.fullmatch(path):
                return self._handle_run(event, method, path)
            return response(404, {"error": "not_found"})
        except ClientRequestError as error:
            return response(error.status_code, {"error": error.code})
        except Exception as error:  # noqa: BLE001 - Lambda boundary returns generic failures
            logger.error("Public API request failed (%s)", type(error).__name__)
            return response(500, {"error": "internal_error"})

    def _handle_run(
        self,
        event: dict[str, Any],
        method: str,
        path: str,
    ) -> dict[str, Any]:
        tenant = _authorized_tenant_context(event)
        if tenant is None:
            return response(401, {"error": "unauthorized"})

        if path == "/v1/runs":
            if method != "POST":
                return response(405, {"error": "method_not_allowed"})
            payload = json_body(event)
            prompt, source_event_id = _parse_run_payload(payload)
            run = self._runs.enqueue_run(
                organization_id=tenant.organization_id,
                source=AgentRunSource.API,
                prompt=prompt,
                source_event_id=source_event_id,
            )
            return response(202, {"run": _public_run(run)})

        run_match = _RUN_PATH.fullmatch(path)
        if run_match is None:
            return response(404, {"error": "not_found"})
        if method != "GET":
            return response(405, {"error": "method_not_allowed"})
        run_id = run_match.group("run_id")
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return response(404, {"error": "not_found"})
        found_run = self._runs.get_run(run_id)
        if found_run is None or found_run.organization_id != tenant.organization_id:
            return response(404, {"error": "not_found"})
        return response(200, {"run": _public_run(found_run)})


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
