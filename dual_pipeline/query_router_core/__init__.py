"""Core subpackage for provider-free front-door query routing."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .._validators import require_strict_keys
from ..contracts import create_artifact
from ..core import ROOT, digest, normalize_space, stable_id
from ..l2 import canonical_query_type
from .constants import (
    _INDEPENDENT_GENERAL_TARGET,
    _META_ROUTING_TOKEN,
    _META_WRAPPER,
    _NEGATED_CLAUSE,
    _NEGATED_PREPOSITIONAL_CLAUSE,
    _OUTER_INSTRUCTION_WRAPPERS,
    _QUOTED_OR_CODE,
    _REQUEST_TEXT_ENVELOPE,
    _STATUS_WRITE_REQUEST,
    _TOP_LEVEL_JOINER,
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    EVIDENCE_QUERY_TYPES,
    LEXICON_RULE_KEYS,
    MAX_QUESTION_LENGTH,
    NEE_QUERY_TYPES,
    ROUTING_POLICY_PATH,
)
from .intent import _joined_independent_actions
from .models import (
    DestinationDecision,
    QueryDestination,
    QueryRoutingPolicy,
    RoutingText,
)
from .policy import (
    _compiled_lexicon_rule,
    _lexicon_match,
    _lexicon_matches,
    _load_routing_lexicon,
    _strict_keys,
    load_query_routing_policy,
)
from .router import (
    _evidence_has_independent_material_action,
    _has_joined_semantic_and_handoff,
    _materiality,
    _query_type,
    route_query,
)
from .text import (
    _routing_text,
    _unwrap_single_outer_instruction_wrapper,
)

__all__ = [
    "ARTIFACT_TYPES",
    "Any",
    "DELIVERY_PROFILES",
    "DestinationDecision",
    "EVIDENCE_QUERY_TYPES",
    "LEXICON_RULE_KEYS",
    "MAX_QUESTION_LENGTH",
    "Mapping",
    "NEE_QUERY_TYPES",
    "Path",
    "QueryDestination",
    "QueryRoutingPolicy",
    "ROOT",
    "ROUTING_POLICY_PATH",
    "RoutingText",
    "StrEnum",
    "_INDEPENDENT_GENERAL_TARGET",
    "_META_ROUTING_TOKEN",
    "_META_WRAPPER",
    "_NEGATED_CLAUSE",
    "_NEGATED_PREPOSITIONAL_CLAUSE",
    "_OUTER_INSTRUCTION_WRAPPERS",
    "_QUOTED_OR_CODE",
    "_REQUEST_TEXT_ENVELOPE",
    "_STATUS_WRITE_REQUEST",
    "_TOP_LEVEL_JOINER",
] + [
    "_compiled_lexicon_rule",
    "_evidence_has_independent_material_action",
    "_has_joined_semantic_and_handoff",
    "_joined_independent_actions",
    "_lexicon_match",
    "_lexicon_matches",
    "_load_routing_lexicon",
    "_materiality",
    "_query_type",
    "_routing_text",
    "_strict_keys",
    "_unwrap_single_outer_instruction_wrapper",
    "canonical_query_type",
    "create_artifact",
    "dataclass",
    "digest",
    "json",
    "load_query_routing_policy",
    "lru_cache",
    "normalize_space",
    "re",
    "require_strict_keys",
    "route_query",
    "stable_id",
    "determine_project_directory",
    "determine_query_project",
    "ComplexityTensor",
    "classify_task_complexity",
    "evaluate_complexity_tensor",
]

from .complexity_classifier import (
    ComplexityTensor,
    classify_task_complexity,
    evaluate_complexity_tensor,
)
from .project_router import determine_project_directory, determine_query_project
