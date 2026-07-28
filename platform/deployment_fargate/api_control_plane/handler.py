"""API Gateway HTTP API handler for tenant Gateway lifecycle routes."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from typing import Any

from platform.deployment_fargate.api_control_plane.auth.iam_auth import iam_principal_is_allowed
from platform.deployment_fargate.api_control_plane.methods.lifecycle_errors import (
    LifecycleCapacityError,
    LifecycleError,
    LifecycleNotFoundError,
    LifecycleValidationError,
)
from platform.deployment_fargate.api_control_plane.methods.method_delete_fargate_container import (
    method_delete_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_get_fargate_container import (
    method_get_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_rotate_api_credential import (
    method_post_rotate_api_credential,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_start_fargate_container import (
    method_post_start_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_stop_fargate_container import (
    method_post_stop_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_put_fargate_container import (
    method_put_fargate_container,
)
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import (
    ORGANIZATION_ID_SEGMENT,
    ClientRequestError,
    method_and_path,
    response,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_PATH = re.compile(
    rf"^/v1/organizations/(?P<organization_id>{ORGANIZATION_ID_SEGMENT})"
    r"/gateway(?P<operation>/start|/stop)?$",
)
_ROTATE_PATH = re.compile(
    rf"^/v1/organizations/(?P<organization_id>{ORGANIZATION_ID_SEGMENT})"
    r"/api-credential/rotate$",
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
        except LifecycleNotFoundError as error:
            return response(HTTPStatus.NOT_FOUND, {"error": error.code})
        except LifecycleValidationError as error:
            return response(HTTPStatus.BAD_REQUEST, {"error": error.code})
        except LifecycleCapacityError as error:
            return response(HTTPStatus.CONFLICT, {"error": error.code})
        except LifecycleError as error:
            return response(HTTPStatus.BAD_GATEWAY, {"error": error.code})
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
                return method_put_fargate_container(event, self._lifecycle, organization_id)
            if operation is None and method == "GET":
                return method_get_fargate_container(event, self._lifecycle, organization_id)
            if operation == "/start" and method == "POST":
                return method_post_start_fargate_container(
                    event,
                    self._lifecycle,
                    organization_id,
                )
            if operation == "/stop" and method == "POST":
                return method_post_stop_fargate_container(
                    event,
                    self._lifecycle,
                    organization_id,
                )
            if operation is None and method == "DELETE":
                return method_delete_fargate_container(
                    event,
                    self._lifecycle,
                    organization_id,
                )
            return response(405, {"error": "method_not_allowed"})

        rotate_match = _ROTATE_PATH.fullmatch(path)
        if rotate_match is not None:
            if method != "POST":
                return response(405, {"error": "method_not_allowed"})
            return method_post_rotate_api_credential(
                event,
                self._lifecycle,
                rotate_match.group("organization_id"),
            )
        return response(404, {"error": "not_found"})
