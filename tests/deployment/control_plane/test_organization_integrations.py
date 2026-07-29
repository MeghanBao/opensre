"""Lifecycle tests for organization integration vault mutations."""

from __future__ import annotations

import json

from platform.deployment_multi_tenant.lambda_control_plane.aws.ecs import FargateServiceState
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import SizeProfile
from tests.deployment.control_plane.test_lifecycle import (
    INTEGRATIONS_SECRET_ARN,
    SERVICE_ARN,
    TASK_DEFINITION_ARN,
    _dependencies,
    _lifecycle,
)

_RECORD = {
    "id": "int-1",
    "service": "github",
    "status": "active",
    "instances": [
        {
            "name": "default",
            "tags": {},
            "credentials": {"auth_token": "ghp_test"},
        }
    ],
}


def test_put_integration_updates_secret_and_forces_redeploy() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)
    secrets.get_current_secret_string.return_value = '{"version":2,"integrations":[]}'
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )

    result = lifecycle.put_organization_integration("org-a", "github", _RECORD)

    assert result == {"service": "github", "changed": True, "integration_count": 1}
    written = secrets.put_current_secret_string.call_args.args[1]
    assert "ghp_test" in written
    assert json.loads(written)["integrations"][0]["service"] == "github"
    ecs.update_service.assert_called()
    assert "ghp_test" not in repr(result)


def test_put_integration_skips_noop_write() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)
    secrets.get_current_secret_string.return_value = json.dumps(
        {"version": 2, "integrations": [_RECORD]},
        sort_keys=True,
        separators=(",", ":"),
    )
    ecs.update_service.reset_mock()

    result = lifecycle.put_organization_integration("org-a", "github", _RECORD)

    assert result["changed"] is False
    secrets.put_current_secret_string.assert_not_called()
    ecs.update_service.assert_not_called()


def test_delete_integration_removes_service() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)
    secrets.get_current_secret_string.return_value = json.dumps(
        {"version": 2, "integrations": [_RECORD]}
    )
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )

    result = lifecycle.delete_organization_integration("org-a", "github")

    assert result == {"service": "github", "changed": True, "integration_count": 0}
    written = secrets.put_current_secret_string.call_args.args[1]
    assert json.loads(written) == {"version": 2, "integrations": []}
    assert secrets.put_current_secret_string.call_args.args[0] == INTEGRATIONS_SECRET_ARN
