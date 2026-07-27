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
    platform: Literal["linux/amd64"] = "linux/amd64"
    provenance: bool = False
    sbom: bool = False

    def buildx_flags(self) -> tuple[str, ...]:
        """Return ECS-compatible Buildx flags for a single image manifest."""

        return (
            "--platform",
            self.platform,
            f"--provenance={str(self.provenance).lower()}",
            f"--sbom={str(self.sbom).lower()}",
            "--push",
        )


GATEWAY_IMAGE = ImageBuildDefinition(
    name="gateway",
    dockerfile=Path("Dockerfile"),
    build_context=Path("."),
)


@dataclass(frozen=True, slots=True)
class LambdaZipDefinition:
    """AWS-managed Lambda runtime contract for a ZIP deployment package."""

    name: str
    runtime: Literal["python3.12"]
    architecture: Literal["x86_64"]
    handler: str
    package_type: Literal["Zip"] = "Zip"


CONTROL_PLANE_LAMBDA = LambdaZipDefinition(
    name="control-plane-lambda",
    runtime="python3.12",
    architecture="x86_64",
    handler="platform.deployment_fargate.api_control_plane.runtime.lambda_handler",
)


def require_immutable_ecr_image(image_uri: str) -> str:
    """Return a normalized ECR URI or reject mutable/non-ECR image references."""

    normalized = image_uri.strip()
    if not _SHA256_IMAGE_RE.fullmatch(normalized):
        raise ValueError("container image must be an ECR image pinned by sha256 digest")
    return normalized
