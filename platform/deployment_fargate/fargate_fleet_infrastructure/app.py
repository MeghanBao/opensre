#!/usr/bin/env python3
"""CDK application entrypoint for shared OpenSRE deployment infrastructure."""

from __future__ import annotations

import os

import aws_cdk as cdk

from platform.deployment_fargate.fargate_fleet_infrastructure.fargate_fleet_stack import (
    FargateFleetStack,
)

app = cdk.App()
FargateFleetStack(
    app,
    "OpensreFargateFleet",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", os.getenv("AWS_REGION", "us-east-1")),
    ),
    description="Shared ECS Fargate fleet foundation for tenant Gateway services",
)
app.synth()
