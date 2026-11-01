"""Audience canonicalization and serialization primitives."""
from __future__ import annotations

import json
from typing import Any, Iterable

from .constants import AUDIENCE_PATTERN, LabelQualityError


def canonicalize_audiences(value: str | Iterable[str]) -> tuple[str, ...]:
    """Return a sorted, unique audience tuple or fail closed.

    Audience parsing intentionally has no default.  A malformed or empty
    access label must not silently become an engineering audience.
    """

    raw: Any = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise LabelQualityError("audiences must be valid JSON") from exc
    if not isinstance(raw, (list, tuple)):
        raise LabelQualityError("audiences must be an array")
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item != item.strip() or not AUDIENCE_PATTERN.fullmatch(
            item
        ):
            raise LabelQualityError(f"invalid audience label: {item!r}")
        normalized.append(item)
    result = tuple(sorted(set(normalized)))
    if not result:
        raise LabelQualityError("at least one audience is required")
    return result


def canonical_audiences_json(value: str | Iterable[str]) -> str:
    return json.dumps(
        list(canonicalize_audiences(value)),
        ensure_ascii=False,
        separators=(",", ":"),
    )

