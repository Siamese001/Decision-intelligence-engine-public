"""Record types and constants for identity resolution."""
from __future__ import annotations

from dataclasses import dataclass

from dual_pipeline.index import CLASSIFICATION_RANK  # noqa: F401

MINIMUM_RANKED_CANDIDATE_SCORE = 0.45
IDENTITY_REVIEW_EXTENSION_KEY = "com.partner_advisory.nee.identity-review"
NEW_NODE_ASSESSMENT_EXTENSION_KEY = "com.partner_advisory.nee.new-node-assessment"
ENTITY_INTENT_PROFILES_EXTENSION_KEY = "com.partner_advisory.nee.entity-intent-profiles"


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    canonical_name: str
    node_type: str
    description: str
    normalized_name: str
    status: str = "canonical"
