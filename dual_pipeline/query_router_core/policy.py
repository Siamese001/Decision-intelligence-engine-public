"""Policy loading and lexicon compilation for query routing."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .._validators import require_strict_keys
from ..core import digest
from .constants import (
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    LEXICON_RULE_KEYS,
    ROUTING_POLICY_PATH,
)
from .models import QueryDestination, QueryRoutingPolicy


def _strict_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    require_strict_keys(value, required=expected, label="query routing policy")


def _load_routing_lexicon(value: Any) -> tuple[str, Mapping[str, tuple[str, ...]]]:
    if not isinstance(value, Mapping):
        raise ValueError("query routing lexicon must be an object")
    _strict_keys(value, {"version", *LEXICON_RULE_KEYS})
    version = value.get("version")
    if version != "2.1.0":
        raise ValueError("query routing lexicon must be version 2.1.0")
    rules: dict[str, tuple[str, ...]] = {}
    for key in sorted(LEXICON_RULE_KEYS):
        entries = value[key]
        if (
            not isinstance(entries, list)
            or not entries
            or any(not isinstance(entry, str) or not entry.strip() for entry in entries)
            or len(entries) != len(set(entries))
        ):
            raise ValueError(f"query routing lexicon rule {key} must be a unique nonempty string list")
        try:
            re.compile("(?:" + ")|(?:".join(entries) + ")", re.I)
        except re.error as exc:
            raise ValueError(f"query routing lexicon rule {key} has an invalid pattern") from exc
        rules[key] = tuple(entries)
    return version, rules


@lru_cache(maxsize=64)
def _compiled_lexicon_rule(entries: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("(?:" + ")|(?:".join(entries) + ")", re.I)


def _lexicon_match(policy: QueryRoutingPolicy, rule: str, question: str) -> re.Match[str] | None:
    return _compiled_lexicon_rule(policy.routing_lexicon[rule]).search(question)


def _lexicon_matches(policy: QueryRoutingPolicy, rule: str, question: str) -> bool:
    return _lexicon_match(policy, rule, question) is not None


def load_query_routing_policy(path: Path = ROUTING_POLICY_PATH) -> QueryRoutingPolicy:
    raw = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("query routing policy must be an object")
    expected = {
        "schema_version",
        "mode",
        "automatic_handoff",
        "external_classifier_allowed",
        "operator_override_allowed",
        "default_delivery_profile",
        "default_artifact_type",
        "allowed_destinations",
        "routing_lexicon",
    }
    _strict_keys(raw, expected)
    if raw["schema_version"] != "1.0.0" or raw["mode"] != "enforced":
        raise ValueError("query routing policy must be enforced schema 1.0.0")
    if raw["automatic_handoff"] is not False:
        raise ValueError("automatic query handoff must remain disabled")
    if raw["external_classifier_allowed"] is not False:
        raise ValueError("external query classification must remain disabled")
    if raw["operator_override_allowed"] is not True:
        raise ValueError("governed operator destination override must remain available")
    delivery = str(raw["default_delivery_profile"])
    artifact = str(raw["default_artifact_type"])
    if delivery not in DELIVERY_PROFILES or artifact not in ARTIFACT_TYPES:
        raise ValueError("query routing defaults are invalid")
    destinations = raw["allowed_destinations"]
    expected_destinations = {value.value for value in QueryDestination}
    if not isinstance(destinations, list) or set(destinations) != expected_destinations:
        raise ValueError("query routing destinations must match the governed set")
    lexicon_version, lexicon = _load_routing_lexicon(raw["routing_lexicon"])
    return QueryRoutingPolicy(
        schema_version="1.0.0",
        mode="enforced",
        automatic_handoff=False,
        external_classifier_allowed=False,
        operator_override_allowed=True,
        default_delivery_profile=delivery,
        default_artifact_type=artifact,
        allowed_destinations=frozenset(str(value) for value in destinations),
        routing_lexicon_version=lexicon_version,
        routing_lexicon=lexicon,
        policy_digest=digest(raw),
    )
