"""Constants for retrieval release suite generation."""
from __future__ import annotations

from ..core import ROOT
from ..index import CLASSIFICATION_RANK

RETRIEVAL_SUITE_GENERATION_POLICY_PATH = (
    ROOT / "requirements" / "retrieval-release-suite-generation-policy.json"
)
GENERATOR_VERSION = "1.0.1"
_MAX_QUERY_CHARACTERS = 4000
_RELATIONSHIP_RECORD_TYPES = frozenset({"edge", "architecture_relationship", "relationship_assertion", "relationship"})
_CLASSIFICATIONS = (
    "public",
    "internal",
    "confidential",
    "restricted",
)
