"""Evidence-ordered entity resolution with explicit ambiguity controls."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    connect_sqlite,
    digest,
    normalize_name,
    normalize_space,
    stable_id,
    utc_now,
)
from dual_pipeline.index import CLASSIFICATION_RANK
from dual_pipeline.node_type_policy import active_creatable_node_types

from .identity_core import (
    ENTITY_INTENT_PROFILES_EXTENSION_KEY,
    IDENTITY_REVIEW_EXTENSION_KEY,
    MINIMUM_RANKED_CANDIDATE_SCORE,
    NEW_NODE_ASSESSMENT_EXTENSION_KEY,
    EntityRecord,
    GraphEntityCatalog,
    _external_concept_proposal,
    _external_concept_scope,
    _identity_review_profile,
    _initial_match,
    _intent_profile,
    _label_options,
    _new_node_assessment,
    _review_confidence,
    _score,
    _stable_identifier,
    _tokens,
    resolve_bundle,
    resolve_mention,
)

__all__ = [
    "Any",
    "CLASSIFICATION_RANK",
    "ENTITY_INTENT_PROFILES_EXTENSION_KEY",
    "EntityRecord",
    "GraphEntityCatalog",
    "IDENTITY_REVIEW_EXTENSION_KEY",
    "MINIMUM_RANKED_CANDIDATE_SCORE",
    "NEW_NODE_ASSESSMENT_EXTENSION_KEY",
    "Path",
    "_external_concept_proposal",
    "_external_concept_scope",
    "_identity_review_profile",
    "_initial_match",
    "_intent_profile",
    "_label_options",
    "_new_node_assessment",
    "_review_confidence",
    "_score",
    "_stable_identifier",
    "_tokens",
    "active_creatable_node_types",
    "annotations",
    "connect_sqlite",
    "create_artifact",
    "dataclass",
    "difflib",
    "digest",
    "json",
    "normalize_name",
    "normalize_space",
    "re",
    "resolve_bundle",
    "resolve_mention",
    "stable_id",
    "utc_now",
]
