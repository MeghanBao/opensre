"""Unit tests for control-plane IntegrationStore v2 mutation helpers."""

from __future__ import annotations

import json

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.utils.integration_store_v2 import (
    delete_integration_service,
    parse_integration_store,
    serialize_integration_store,
    upsert_integration_record,
)

_RECORD = {
    "id": "int-1",
    "service": "github",
    "status": "active",
    "instances": [
        {
            "name": "default",
            "tags": {},
            "credentials": {"auth_token": "ghp_test"},
        }
    ],
}


def test_upsert_and_delete_are_idempotent() -> None:
    store = parse_integration_store('{"version":2,"integrations":[]}')
    updated, changed = upsert_integration_record(store, _RECORD)
    assert changed is True
    again, changed_again = upsert_integration_record(updated, _RECORD)
    assert changed_again is False
    assert again == updated

    removed, removed_changed = delete_integration_service(updated, "github")
    assert removed_changed is True
    noop, noop_changed = delete_integration_service(removed, "github")
    assert noop_changed is False
    assert noop == removed


def test_serialize_enforces_size_limit() -> None:
    huge = {
        **_RECORD,
        "instances": [
            {
                "name": "default",
                "tags": {},
                "credentials": {"token": "x" * 70_000},
            }
        ],
    }
    with pytest.raises(ValueError, match="integration_store_too_large"):
        serialize_integration_store({"version": 2, "integrations": [huge]})


def test_parse_rejects_extra_keys() -> None:
    with pytest.raises(ValueError, match="invalid_integration_store"):
        parse_integration_store(json.dumps({"version": 2, "integrations": [], "extra": True}))
