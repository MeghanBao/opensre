"""API Gateway HTTP API handler for tenant Gateway lifecycle routes."""

from __future__ import annotations

import logging
import re
from typing import Any

from platform.deployment_fargate.api_control_plane.api.iam_auth import iam_principal_is_allowed
from platform.deployment_fargate.api_control_plane.contracts.contracts import (
    SizeProfile,
    TenantDeployment,
)
from platform.deployment_fargate.api_control_plane.contracts.lifecycle_service import (
    LifecycleService,
)
from platform.deployment_fargate.api_control_plane.reconciler.results import (
    ProvisionGatewayResult,
    RotatedApiCredential,
)
from platform.deployment_fargate.http_lambda import (
    ClientRequestError,
    json_body,
    jsonable,
    method_and_path,
    response,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_PATH = re.compile(
    r"^/v1/organizations/(?P<organization_id>[A-Za-z0-9][A-Za-z0-9_-]{0,127})"
    r"/gateway(?P<operation>/start|/stop)?$"
)
_ROTATE_PATH = re.compile(
    r"^/v1/organizations/(?P<organization_id>[A-Za-z0-9][A-Za-z0-9_-]{0,127})"
    r"/api-credential/rotate$"
)


class ControlPlaneApi:
    """Dependency-injected Lambda application for IAM-protected lifecycle routes."""

    def __init__(
        self,
        *,
        lifecycle: LifecycleService,
        allowed_lifecycle_role_arns: frozenset[str],
    ) -> None:
        self._lifecycle = lifecycle
        self._allowed_lifecycle_role_arns = allowed_lifecycle_role_arns

    def handle(self, event: dict[str, Any], _context: object = None) -> dict[str, Any]:
        """Dispatch an HTTP proxy request for lifecycle routes."""

        try:
            method, path = method_and_path(event)
            if not path.startswith("/v1/organizations/"):
                return response(404, {"error": "not_found"})
            return self._handle_lifecycle(event, method, path)
        except ClientRequestError as error:
            return response(error.status_code, {"error": error.code})
        except Exception as error:  # noqa: BLE001 - Lambda boundary returns generic failures
            logger.error("Control-plane request failed (%s)", type(error).__name__)
            return response(500, {"error": "internal_error"})

    def _handle_lifecycle(
        self,
        event: dict[str, Any],
        method: str,
        path: str,
    ) -> dict[str, Any]:
        if not iam_principal_is_allowed(event, self._allowed_lifecycle_role_arns):
            return response(403, {"error": "forbidden"})

        lifecycle_match = _LIFECYCLE_PATH.fullmatch(path)
        if lifecycle_match is not None:
            organization_id = lifecycle_match.group("organization_id")
            operation = lifecycle_match.group("operation")
            if operation is None and method == "PUT":
                payload = json_body(event)
                size_profile = _parse_size_profile(payload)
                result = self._lifecycle.provision_gateway(organization_id, size_profile)
                return response(200, _provision_payload(result))
            if operation is None and method == "GET":
                deployment = self._lifecycle.get_gateway(organization_id)
                if deployment is None:
                    return response(404, {"error": "not_found"})
                return response(200, {"deployment": jsonable(deployment)})
            if operation == "/start" and method == "POST":
                return _deployment_response(self._lifecycle.start_gateway(organization_id))
            if operation == "/stop" and method == "POST":
                return _deployment_response(self._lifecycle.stop_gateway(organization_id))
            if operation is None and method == "DELETE":
                return _deployment_response(self._lifecycle.delete_gateway(organization_id))
            return response(405, {"error": "method_not_allowed"})

        rotate_match = _ROTATE_PATH.fullmatch(path)
        if rotate_match is not None:
            if method != "POST":
                return response(405, {"error": "method_not_allowed"})
            rotated_result = self._lifecycle.rotate_api_credential(
                rotate_match.group("organization_id")
            )
            return response(200, _rotated_credential_payload(rotated_result))
        return response(404, {"error": "not_found"})


def _parse_size_profile(payload: dict[str, Any]) -> SizeProfile:
    unexpected = set(payload) - {"size_profile"}
    if unexpected:
        raise ClientRequestError(400, "invalid_request")
    raw_profile = payload.get("size_profile", SizeProfile.SMALL.value)
    try:
        return SizeProfile(raw_profile)
    except (TypeError, ValueError):
        raise ClientRequestError(400, "invalid_size_profile") from None


def _deployment_response(deployment: TenantDeployment) -> dict[str, Any]:
    return response(200, {"deployment": jsonable(deployment)})


def _provision_payload(result: ProvisionGatewayResult) -> dict[str, Any]:
    payload = {"deployment": jsonable(result.deployment)}
    if result.api_credential is not None:
        payload["api_credential"] = result.api_credential
    return payload


def _rotated_credential_payload(result: RotatedApiCredential) -> dict[str, Any]:
    return {
        "key_id": result.key_id,
        "api_credential": result.api_credential,
    }
