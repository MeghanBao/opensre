"""Synth tests for the public forwarder API CDK stack."""

from __future__ import annotations

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Match, Template

from platform.deployment_fargate.api_public_forwarder_infrastructure.app import create_app
from platform.deployment_fargate.api_public_forwarder_infrastructure.public_forwarder_api_stack import (
    PublicForwarderApiStack,
)


@pytest.fixture
def api_template() -> Template:
    app = App()
    stack = PublicForwarderApiStack(
        app,
        "TestPublicForwarderApi",
        lambda_code=lambda_.Code.from_inline("def handler(event, context): return {}"),
    )
    return Template.from_stack(stack)


def test_creates_public_forwarder_lambda(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Architectures": ["x86_64"],
            "Handler": ("platform.deployment_fargate.api_public_forwarder.runtime.lambda_handler"),
            "Timeout": 29,
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN": {
                            "Ref": "DatabaseUrlSecretArn"
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


def test_creates_http_api_authorizer_and_run_routes(api_template: Template) -> None:
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"ProtocolType": "HTTP"},
    )
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {
            "AuthorizerType": "REQUEST",
            "AuthorizerPayloadFormatVersion": "2.0",
            "AuthorizerResultTtlInSeconds": 0,
            "EnableSimpleResponses": True,
            "IdentitySource": ["$request.header.Authorization"],
        },
    )
    routes = api_template.find_resources("AWS::ApiGatewayV2::Route")
    assert len(routes) == 2
    route_properties = [route["Properties"] for route in routes.values()]
    assert all(route["AuthorizationType"] == "CUSTOM" for route in route_properties)
    assert {route["RouteKey"] for route in route_properties} == {
        "POST /v1/runs",
        "GET /v1/runs/{run_id}",
    }


def test_execution_role_reads_database_and_tenant_public_secrets(
    api_template: Template,
) -> None:
    policies = api_template.find_resources("AWS::IAM::Policy")
    serialized = str(policies)
    assert "secretsmanager:GetSecretValue" in serialized
    assert "/tenants/*/public-api-*" in serialized
    assert "ecs:RegisterTaskDefinition" not in serialized
    assert "iam:CreateRole" not in serialized


def test_creates_retained_logs_and_baseline_alarms(api_template: Template) -> None:
    api_template.resource_count_is("AWS::Logs::LogGroup", 2)
    api_template.resource_count_is("AWS::CloudWatch::Alarm", 3)
    api_template.resource_count_is("AWS::Lambda::Permission", 2)


def test_emits_api_and_function_outputs(api_template: Template) -> None:
    outputs = api_template.find_outputs("*")
    assert "PublicForwarderApiEndpoint" in outputs
    assert "PublicForwarderFunctionArn" in outputs


def test_public_forwarder_app_synths_without_docker() -> None:
    app = create_app(lambda_code=lambda_.Code.from_inline("def handler(event, context): return {}"))

    assembly = app.synth()

    assert any(stack.stack_name == "OpensrePublicForwarderApi" for stack in assembly.stacks)
