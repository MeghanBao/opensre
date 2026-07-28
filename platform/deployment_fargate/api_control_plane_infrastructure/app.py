#!/usr/bin/env python3
"""CDK application entrypoint for the control-plane API."""

from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_

from platform.deployment_fargate.api_control_plane_infrastructure.control_plane_api_stack import (
    ControlPlaneApiStack,
)


def create_app(*, lambda_code: lambda_.Code | None = None) -> cdk.App:
    """Build the CDK app, allowing synth tests to bypass Docker bundling."""
    app = cdk.App()
    ControlPlaneApiStack(
        app,
        "OpensreControlPlaneApi",
        lambda_code=lambda_code,
        env=cdk.Environment(
            account=os.getenv("CDK_DEFAULT_ACCOUNT"),
            region=os.getenv("CDK_DEFAULT_REGION", os.getenv("AWS_REGION", "us-east-1")),
        ),
        description="OpenSRE IAM-protected lifecycle API",
    )
    return app


if __name__ == "__main__":
    create_app().synth()
