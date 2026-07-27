"""Deterministic AWS tag payloads."""

from collections.abc import Mapping


def resource_tags(tags: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "value": value} for key, value in sorted(tags.items())]


def iam_tags(tags: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]
