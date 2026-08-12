"""Yandex Cloud environment variable names.

The names deliberately match the ones the official ``yc`` CLI and the Yandex
SDKs already read, so a shell that can run ``yc`` can run OpenSRE without a
second set of exports.
"""

from __future__ import annotations

YC_FOLDER_ID_ENV = "YC_FOLDER_ID"
YC_CLOUD_ID_ENV = "YC_CLOUD_ID"

#: Path to a service-account authorized key JSON file.
YC_SA_KEY_FILE_ENV = "YC_SA_KEY_FILE"
#: The same key inline, for deployments with no writable filesystem.
YC_SA_KEY_ENV = "YC_SA_KEY"
#: OAuth token for a user account.
YC_TOKEN_ENV = "YC_TOKEN"
#: A ready IAM token. Expires within 12 hours.
YC_IAM_TOKEN_ENV = "YC_IAM_TOKEN"
#: Set on a Yandex Cloud VM with an attached service account.
YC_USE_METADATA_ENV = "YC_USE_METADATA"

#: Override the endpoint discovery document, for an isolated installation.
YC_API_ENDPOINT_ENV = "YC_API_ENDPOINT"
#: Per-service host overrides, as ``service=host,service=host``.
YC_ENDPOINT_OVERRIDES_ENV = "YC_ENDPOINT_OVERRIDES"

__all__ = [
    "YC_API_ENDPOINT_ENV",
    "YC_CLOUD_ID_ENV",
    "YC_ENDPOINT_OVERRIDES_ENV",
    "YC_FOLDER_ID_ENV",
    "YC_IAM_TOKEN_ENV",
    "YC_SA_KEY_ENV",
    "YC_SA_KEY_FILE_ENV",
    "YC_TOKEN_ENV",
    "YC_USE_METADATA_ENV",
]
