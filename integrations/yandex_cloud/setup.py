"""What Yandex Cloud needs before it is considered configured.

The folder is always asked for — every read is folder-scoped, so nothing works
without it. Auth is a picker, because the four ways to hold a Yandex Cloud
credential are genuinely alternatives rather than a set to fill in.

The rule that exactly one credential must be present lives on
``YandexCloudIntegrationConfig``, which the verifier validates against, so
setup and health checks agree on any surface that skips the picker.
"""

from __future__ import annotations

from dataclasses import replace

from config.constants.yandex_cloud import (
    YC_CLOUD_ID_ENV,
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_TOKEN_ENV,
    YC_USE_METADATA_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode
from integrations.yandex_cloud import metadata
from integrations.yandex_cloud.verifier import verify_yandex_cloud

FOLDER_ID_FIELD = "folder_id"
CLOUD_ID_FIELD = "cloud_id"
SA_KEY_FILE_FIELD = "sa_key_file"
SA_KEY_FIELD = "sa_key"
OAUTH_TOKEN_FIELD = "oauth_token"
IAM_TOKEN_FIELD = "iam_token"
USE_METADATA_FIELD = "use_metadata"

YANDEX_CLOUD_SETUP = IntegrationSetupSpec(
    service="yandex_cloud",
    fields=(
        SetupField(
            name=FOLDER_ID_FIELD,
            label="Folder ID",
            prompt="Folder ID (from the console URL, or `yc config get folder-id`)",
            env_var=YC_FOLDER_ID_ENV,
        ),
        SetupField(
            name=CLOUD_ID_FIELD,
            label="Cloud ID",
            prompt="Cloud ID (optional)",
            env_var=YC_CLOUD_ID_ENV,
            required=False,
        ),
        SetupField(
            name=SA_KEY_FILE_FIELD,
            label="Service-account key file",
            prompt="Path to the authorized key JSON",
            env_var=YC_SA_KEY_FILE_ENV,
            required=False,
        ),
        SetupField(
            name=SA_KEY_FIELD,
            label="Service-account key",
            prompt="Authorized key JSON (paste the file contents)",
            env_var=YC_SA_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=OAUTH_TOKEN_FIELD,
            label="OAuth token",
            prompt="OAuth token for your Yandex account",
            env_var=YC_TOKEN_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=IAM_TOKEN_FIELD,
            label="IAM token",
            prompt="IAM token (expires within 12 hours)",
            env_var=YC_IAM_TOKEN_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USE_METADATA_FIELD,
            label="Use the instance metadata service",
            prompt="Take tokens from the VM's attached service account",
            env_var=YC_USE_METADATA_ENV,
            default="true",
            required=False,
        ),
    ),
    mode_prompt="How should OpenSRE authenticate to Yandex Cloud?",
    modes=(
        SetupMode(
            value="sa_key_file",
            label="Service-account key file (recommended)",
            fields=(SA_KEY_FILE_FIELD,),
        ),
        SetupMode(
            value="sa_key",
            label="Service-account key, pasted inline",
            fields=(SA_KEY_FIELD,),
        ),
        SetupMode(
            value="oauth",
            label="OAuth token (personal account)",
            fields=(OAUTH_TOKEN_FIELD,),
        ),
        SetupMode(
            value="iam_token",
            label="IAM token (short-lived, for testing)",
            fields=(IAM_TOKEN_FIELD,),
        ),
        SetupMode(
            value="metadata",
            label="Instance metadata (running on a Yandex Cloud VM)",
            fields=(USE_METADATA_FIELD,),
        ),
    ),
    verify=verify_yandex_cloud,
)


def setup_spec_for_this_host() -> IntegrationSetupSpec:
    """Return the setup spec, with folder and cloud prefilled on a Yandex Cloud VM.

    On an instance both identifiers are already known - the metadata service
    hands them out - so asking the operator to fetch them from the console is
    make-work. It reads worst in the metadata auth mode, which needs no
    credential at all and would still demand a folder typed in by hand.

    Resolved when setup runs rather than at import: ``SetupField.default`` is a
    plain string, and the metadata service lives on a link-local address that
    does not answer outside the cloud, so asking at import time would cost a
    timeout on every start elsewhere.

    Off an instance the spec is returned untouched.
    """
    if not metadata.is_available():
        return YANDEX_CLOUD_SETUP

    from_instance = {
        FOLDER_ID_FIELD: metadata.fetch_folder_id() or "",
        CLOUD_ID_FIELD: metadata.fetch_cloud_id() or "",
    }
    if not any(from_instance.values()):
        return YANDEX_CLOUD_SETUP

    fields = tuple(
        replace(field, default=from_instance[field.name])
        if from_instance.get(field.name)
        else field
        for field in YANDEX_CLOUD_SETUP.fields
    )
    return replace(YANDEX_CLOUD_SETUP, fields=fields)


__all__ = [
    "CLOUD_ID_FIELD",
    "FOLDER_ID_FIELD",
    "IAM_TOKEN_FIELD",
    "OAUTH_TOKEN_FIELD",
    "SA_KEY_FIELD",
    "SA_KEY_FILE_FIELD",
    "USE_METADATA_FIELD",
    "YANDEX_CLOUD_SETUP",
    "setup_spec_for_this_host",
]
