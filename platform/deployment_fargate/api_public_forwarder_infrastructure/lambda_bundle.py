"""Reproducible Linux/amd64 Lambda bundle for the public forwarder runtime."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import BundlingOptions
from aws_cdk import aws_lambda as lambda_


def bundled_lambda_code() -> lambda_.AssetCode:
    """Package only the runtime source and its native Postgres dependency."""
    repository_root = Path(__file__).resolve().parents[3]
    return lambda_.Code.from_asset(
        str(repository_root),
        bundling=BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_12.bundling_image,
            platform="linux/amd64",
            command=[
                "bash",
                "-c",
                (
                    "pip install --no-cache-dir 'psycopg2-binary>=2.9,<3' "
                    "--target /asset-output "
                    "&& tar --exclude='*/cdk.out' --exclude='*/__pycache__' "
                    "-C /asset-input -cf - "
                    "platform/__init__.py "
                    "platform/deployment_fargate/__init__.py "
                    "platform/deployment_fargate/api_control_plane "
                    "platform/deployment_fargate/api_public_forwarder "
                    "platform/deployment_fargate/utils "
                    "| tar -C /asset-output -xf -"
                ),
            ],
        ),
    )
