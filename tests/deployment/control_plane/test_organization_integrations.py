"""Lifecycle tests for organization integration vault mutations."""

from __future__ import annotations

import json

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.aws.ecs import FargateServiceState
from platform.deployment_multi_tenant.lambda_control_plane.methods import (
    method_put_organization_integration as put_integration_module,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import SizeProfile
from platform.deployment_multi_tenant.lambda_control_plane.utils.slack_team import (
    SlackTeamResolutionError,
)
from platform.deployment_multi_tenant.utils.http_lambda import ClientRequestError
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

_SLACK_RECORD = {
    "id": "int-slack",
    "service": "slack",
    "status": "active",
    "instances": [
        {
            "name": "default",
            "tags": {},
            "credentials": {"bot_token": "xoxb-test", "app_token": "xapp-test"},
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


def test_put_slack_integration_maps_workspace_to_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    resolved_tokens: list[str] = []

    def fake_resolve(bot_token: str) -> str:
        resolved_tokens.append(bot_token)
        return "T06TEST"

    monkeypatch.setattr(put_integration_module, "resolve_slack_team_id", fake_resolve)

    result = lifecycle.put_organization_integration("org-a", "slack", _SLACK_RECORD)

    assert result["changed"] is True
    assert resolved_tokens == ["xoxb-test"]
    assert repository.slack_team_installs == {"T06TEST": "org-a"}
    # The bot token goes to the dedicated secret, never the integrations vault.
    secret_write = secrets.put_slack_bot_token_secret.call_args.kwargs
    assert secret_write["bot_token"] == "xoxb-test"
    assert "slack-bot-token" in secret_write["secret_name"]
    vault_write = secrets.put_current_secret_string.call_args.args[1]
    assert "xoxb-test" not in vault_write
    assert "xapp-test" in vault_write
    ecs.update_service.assert_called()


def test_put_slack_integration_backfills_mapping_on_noop_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)
    # The vault already holds the stripped record: bot tokens live only in the
    # dedicated secret, so a re-PUT of the same payload is a vault noop.
    stripped = {
        **_SLACK_RECORD,
        "instances": [{"name": "default", "tags": {}, "credentials": {"app_token": "xapp-test"}}],
    }
    secrets.get_current_secret_string.return_value = json.dumps(
        {"version": 2, "integrations": [stripped]},
        sort_keys=True,
        separators=(",", ":"),
    )
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    monkeypatch.setattr(
        put_integration_module,
        "resolve_slack_team_id",
        lambda _token: "T06TEST",
    )

    result = lifecycle.put_organization_integration("org-a", "slack", _SLACK_RECORD)

    assert result["changed"] is False
    secrets.put_current_secret_string.assert_not_called()
    assert repository.slack_team_installs == {"T06TEST": "org-a"}
    # The token itself still rotates, so running tasks are restarted to pick it up.
    secrets.put_slack_bot_token_secret.assert_called_once()
    ecs.update_service.assert_called()


def test_put_slack_integration_rejects_invalid_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)

    def fake_resolve(_token: str) -> str:
        raise SlackTeamResolutionError("invalid_slack_bot_token")

    monkeypatch.setattr(put_integration_module, "resolve_slack_team_id", fake_resolve)

    with pytest.raises(ClientRequestError) as caught:
        lifecycle.put_organization_integration("org-a", "slack", _SLACK_RECORD)

    assert caught.value.status_code == 400
    assert caught.value.code == "invalid_slack_bot_token"
    secrets.put_current_secret_string.assert_not_called()
    secrets.put_slack_bot_token_secret.assert_not_called()
    assert repository.slack_team_installs == {}


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
