"""Shared HTTP helpers for Lambda-backed deployment APIs."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

_MAX_BODY_BYTES = 1 * 1024 * 1024
_ORGANIZATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,128}$")


class ClientRequestError(ValueError):
    """A client-controlled parse or validation failure."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    request_context = event.get("requestContext")
    http = request_context.get("http") if isinstance(request_context, dict) else None
    method = http.get("method") if isinstance(http, dict) else event.get("httpMethod")
    path = event.get("rawPath") or event.get("path")
    if not isinstance(method, str) or not isinstance(path, str):
        raise ClientRequestError(400, "invalid_request")
    return method.upper(), path.rstrip("/") or "/"


def is_authorizer_event(event: dict[str, Any]) -> bool:
    return event.get("type") == "REQUEST" and isinstance(event.get("routeArn"), str)


def header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None
    expected = name.lower()
    for raw_name, value in headers.items():
        if str(raw_name).lower() == expected and isinstance(value, str):
            return value
    return None


def json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body")
    if not isinstance(raw_body, str):
        raise ClientRequestError(400, "invalid_json")
    try:
        body = (
            base64.b64decode(raw_body, validate=True)
            if event.get("isBase64Encoded")
            else raw_body.encode()
        )
    except (ValueError, TypeError):
        raise ClientRequestError(400, "invalid_json") from None
    if len(body) > _MAX_BODY_BYTES:
        raise ClientRequestError(413, "payload_too_large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ClientRequestError(400, "invalid_json") from None
    if not isinstance(payload, dict):
        raise ClientRequestError(400, "invalid_request")
    return payload


def jsonable(value: object) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(payload, separators=(",", ":"), sort_keys=True),
        "isBase64Encoded": False,
    }


def organization_id_pattern() -> re.Pattern[str]:
    return _ORGANIZATION_ID_PATTERN


def run_id_pattern() -> re.Pattern[str]:
    return _RUN_ID_PATTERN
