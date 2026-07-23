"""Container image contracts for the multi-tenant Fargate deployment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_SHA256_IMAGE_RE = re.compile(
    r"^(?P<repository>[a-zA-Z0-9][a-zA-Z0-9._/-]*"
    r"\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-zA-Z0-9._/-]+)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)


@dataclass(frozen=True, slots=True)
class ImageBuildDefinition:
    """A build target that is pushed to an already-existing ECR repository."""

    name: str
    dockerfile: Path
    build_context: Path
    repository_strategy: Literal["existing"] = "existing"


GATEWAY_IMAGE = ImageBuildDefinition(
    name="gateway",
    dockerfile=Path("Dockerfile"),
    build_context=Path("."),
)

LAMBDA_IMAGE = ImageBuildDefinition(
    name="control-plane-lambda",
    dockerfile=Path("platform/deployment/fargate/Dockerfile.lambda"),
    build_context=Path("."),
)


def require_immutable_ecr_image(image_uri: str) -> str:
    """Return a normalized ECR URI or reject mutable/non-ECR image references."""

    normalized = image_uri.strip()
    if not _SHA256_IMAGE_RE.fullmatch(normalized):
        raise ValueError("container image must be an ECR image pinned by sha256 digest")
    return normalized
