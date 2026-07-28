"""Synth tests for the control-plane API CDK stack."""

from __future__ import annotations

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Match, Template

from platform.deployment_fargate.api_control_plane_infrastructure.app import create_app
from platform.deployment_fargate.api_control_plane_infrastructure.control_plane_api_stack import (
    ControlPlaneApiStack,
)


@pytest.fixture
def api_template() -> Template:
    app = App()
    stack = ControlPlaneApiStack(
        app,
        "TestControlPlaneApi",
        lambda_code=lambda_.Code.from_inline("def handler(event, context): return {}"),
    )
    return Template.from_stack(stack)


def test_creates_lifecycle_lambda_with_fleet_environment(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Architectures": ["x86_64"],
            "Handler": ("platform.deployment_fargate.api_control_plane.runtime.lambda_handler"),
            "Timeout": 29,
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN": {
                            "Ref": "DatabaseUrlSecretArn"
                        },
                        "OPENSRE_CONTROL_PLANE_LIFECYCLE_ROLE_ARNS": Match.any_value(),
                        "OPENSRE_FARGATE_CLUSTER_ARN": {
                            "Fn::ImportValue": ("opensre-fleet:OPENSRE-FARGATE-CLUSTER-ARN")
                        },
                        "OPENSRE_FARGATE_RESOURCE_PREFIX": {
                            "Fn::ImportValue": ("opensre-fleet:OPENSRE-FARGATE-RESOURCE-PREFIX")
                        },
                    }
                )
            },
        },
    )


def test_database_url_uses_runtime_secret_resolution(api_template: Template) -> None:
    functions = api_template.find_resources("AWS::Lambda::Function")
    environments = [
        function["Properties"].get("Environment", {}).get("Variables", {})
        for function in functions.values()
    ]
    assert any(
        environment.get("OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN")
        == {"Ref": "DatabaseUrlSecretArn"}
        for environment in environments
    )
    assert all("DATABASE_URL" not in environment for environment in environments)


def test_creates_http_api_with_iam_lifecycle_routes(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"ProtocolType": "HTTP"},
    )
    api_template.resource_count_is("AWS::ApiGatewayV2::Authorizer", 0)
    routes = api_template.find_resources("AWS::ApiGatewayV2::Route")
    assert len(routes) == 6
    route_properties = [route["Properties"] for route in routes.values()]
    assert all(route["AuthorizationType"] == "AWS_IAM" for route in route_properties)
    assert {route["RouteKey"] for route in route_properties} == {
        "PUT /v1/organizations/{organization_id}/gateway",
        "GET /v1/organizations/{organization_id}/gateway",
        "POST /v1/organizations/{organization_id}/gateway/start",
        "POST /v1/organizations/{organization_id}/gateway/stop",
        "DELETE /v1/organizations/{organization_id}/gateway",
        "POST /v1/organizations/{organization_id}/api-credential/rotate",
    }


def test_creates_default_stage_and_lambda_permissions(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "StageName": "$default",
            "AutoDeploy": True,
            "AccessLogSettings": Match.object_like(
                {"DestinationArn": Match.any_value(), "Format": Match.any_value()}
            ),
        },
    )
    api_template.resource_count_is("AWS::Lambda::Permission", 1)


def test_applies_database_schema_before_exposing_api(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": (
                "platform.deployment_fargate.api_control_plane_infrastructure."
                "migration_runtime.lambda_handler"
            ),
            "Runtime": "python3.12",
            "Architectures": ["x86_64"],
        },
    )
    custom_resources = api_template.find_resources("AWS::CloudFormation::CustomResource")
    assert len(custom_resources) == 1
    resource = next(iter(custom_resources.values()))
    assert resource["Properties"]["DatabaseUrlSecretArn"] == {"Ref": "DatabaseUrlSecretArn"}
    assert resource["Properties"]["MigrationFunctionVersion"]


def test_execution_role_can_reconcile_tenants_without_wildcard_iam(
    api_template: Template,
) -> None:
    policies = api_template.find_resources("AWS::IAM::Policy")
    serialized = str(policies)
    assert "ecs:RegisterTaskDefinition" in serialized
    assert "iam:PassRole" in serialized
    assert "s3files:CreateAccessPoint" in serialized
    assert "secretsmanager:GetSecretValue" in serialized
    assert "'Action': 'iam:*'" not in serialized


def test_creates_retained_logs_and_baseline_alarms(api_template: Template) -> None:
    api_template.resource_count_is("AWS::Logs::LogGroup", 3)
    api_template.resource_count_is("AWS::CloudWatch::Alarm", 3)


def test_emits_api_and_function_outputs(api_template: Template) -> None:
    outputs = api_template.find_outputs("*")
    assert "ControlPlaneApiEndpoint" in outputs
    assert "ControlPlaneFunctionArn" in outputs


def test_control_plane_app_synths_without_docker() -> None:
    app = create_app(lambda_code=lambda_.Code.from_inline("def handler(event, context): return {}"))

    assembly = app.synth()

    assert any(stack.stack_name == "OpensreControlPlaneApi" for stack in assembly.stacks)
