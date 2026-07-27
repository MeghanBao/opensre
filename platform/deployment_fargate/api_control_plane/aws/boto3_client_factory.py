"""Boto3 client factory for the Fargate control-plane adapters."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

_BOTO3_RETRY_MAX_ATTEMPTS = 3
_BOTO3_CONNECT_TIMEOUT_SECONDS = 10
_BOTO3_READ_TIMEOUT_SECONDS = 30


def create_boto3_client(service: str, region: str) -> Any:
    """Return a boto3 client with standard retry and timeout configuration."""
    config = Config(
        retries={"max_attempts": _BOTO3_RETRY_MAX_ATTEMPTS, "mode": "adaptive"},
        connect_timeout=_BOTO3_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_BOTO3_READ_TIMEOUT_SECONDS,
    )
    return boto3.client(service, region_name=region, config=config)  # type: ignore[call-overload]
