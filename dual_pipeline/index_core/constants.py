"""Constants, schemas, and binary packing utilities for hybrid index and retrieval."""

from __future__ import annotations

import os
import re
import struct
from typing import Iterable

from dual_pipeline.core import ROOT, digest
from dual_pipeline.label_quality import APPROVED_VALIDATION_STATES  # noqa: F401
from dual_pipeline.parameters import get_index_core_parameters

_INDEX_PARAMS = get_index_core_parameters()
CANONICAL_EMBEDDING_MODEL_ID = "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
DENSE_DIMENSIONS = 1024
COLBERT_DIMENSIONS = 1024
MAX_COLBERT_TOKENS = int(_INDEX_PARAMS["max_colbert_tokens"])
EMBEDDING_BATCH_SIZE = int(_INDEX_PARAMS["embedding_batch_size"])
ENTITY_FACET_MAX_ASSERTIONS = int(_INDEX_PARAMS["entity_facet_max_assertions"])
ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS = int(_INDEX_PARAMS["entity_facet_max_assertion_text_characters"])
ENTITY_FACET_MAX_TEXT_CHARACTERS = int(_INDEX_PARAMS["entity_facet_max_text_characters"])
ENTITY_FACET_RETRIEVAL_MODES = frozenset({"entity_navigation", "candidate_entity_navigation"})
ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BGE_M3_MODEL_POLICY = ROOT / "requirements" / "bge-m3-model.json"
CLASSIFICATION_RANK = {
    "public": 0,
    "external_approved": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
RETRIEVAL_METADATA_FIELDS = (
    "retrieval_kind",
    "epistemic_status",
    "provenance_kind",
    "answer_use",
)
RETRIEVAL_METADATA_SCHEMA_VERSION = "1.0.0"
OBJECT_FACET_SCHEMA_VERSION = "1.0.0"
OBJECT_FACET_METADATA_KEYS = (
    "object_facet_schema_version",
    "object_facet_assignment_count",
    "object_facet_manifest_digest",
)
LEGACY_RRF_WEIGHTS = {
    "dense": 0.4,
    "sparse": 0.2,
    "colbert": 0.4,
}
BM25_RRF_WEIGHTS = {
    "dense": 0.05,
    "sparse": 0.15,
    "bm25": 0.70,
    "colbert": 0.10,
}
STRATEGIC_BM25_RRF_WEIGHTS = {
    "dense": 0.20,
    "sparse": 0.15,
    "bm25": 0.45,
    "colbert": 0.20,
}
CALIBRATED_HYBRID_RRF_WEIGHTS = {
    "dense": 0.20,
    "sparse": 0.15,
    "bm25": 0.40,
    "colbert": 0.25,
}
RETRIEVAL_MODE_RRF_WEIGHTS: dict[str, dict[str, float]] = {
    "factual": BM25_RRF_WEIGHTS,
    "strategic": STRATEGIC_BM25_RRF_WEIGHTS,
    "guidance": CALIBRATED_HYBRID_RRF_WEIGHTS,
    "status_snapshot_current": CALIBRATED_HYBRID_RRF_WEIGHTS,
    "status_snapshot_history": CALIBRATED_HYBRID_RRF_WEIGHTS,
    "candidate_review": {"dense": 0.22, "sparse": 0.18, "bm25": 0.35, "colbert": 0.25},
    "candidate_evaluation": {"dense": 0.25, "sparse": 0.15, "bm25": 0.35, "colbert": 0.25},
    "candidate_guidance": {"dense": 0.22, "sparse": 0.18, "bm25": 0.35, "colbert": 0.25},
    "entity_navigation": {"dense": 0.25, "sparse": 0.25, "bm25": 0.20, "colbert": 0.30},
    "candidate_entity_navigation": {"dense": 0.25, "sparse": 0.25, "bm25": 0.20, "colbert": 0.30},
}
MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE = int(_INDEX_PARAMS["max_exact_anchor_injections_per_lane"])
MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE = int(_INDEX_PARAMS["max_exact_entity_title_matches_per_lane"])
HYBRID_RETRIEVAL_POLICY = {
    "policy_id": "bge-m3-bm25-rrf-v3",
    "policy_version": "3.0.0",
    "legacy_weights": LEGACY_RRF_WEIGHTS,
    "bm25_weights": BM25_RRF_WEIGHTS,
    "strategic_bm25_weights": STRATEGIC_BM25_RRF_WEIGHTS,
    "rrf_k": 60,
    "lane_blending": False,
    "exact_anchor_injection": {
        "enabled": True,
        "maximum_per_lane": MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
        "authorization": "same_lane_clearance_audience_and_answer_use_filters",
    },
    "entity_title_exact_match": {
        "enabled": True,
        "maximum_per_lane": MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE,
        "authorization": "same_lane_clearance_audience_and_answer_use_filters",
    },
}
HYBRID_RETRIEVAL_POLICY_DIGEST = digest(HYBRID_RETRIEVAL_POLICY)
RETRIEVAL_KIND_BY_RECORD_TYPE = {
    "assertion": "assertion",
    "edge": "relationship_assertion",
    "architecture_relationship": "relationship_assertion",
    "architecture_component": "entity_facet",
    "entity_facet": "entity_facet",
    "action_item": "guidance",
    "source_chunk": "evidence_span",
}
RETRIEVAL_MODE_POLICY: dict[str, dict[str, frozenset[str]]] = {
    "factual": {
        "approved": frozenset({"factual_answer"}),
    },
    "guidance": {"approved": frozenset({"factual_answer", "guidance"})},
    "strategic": {
        "approved": frozenset(
            {
                "factual_answer",
                "guidance",
                "strategic_context",
                "decision_constraint",
                "external_analogy",
            }
        ),
        "candidate": frozenset({"candidate_hypothesis", "guidance"}),
        "external_public": frozenset({"external_analogy", "strategic_context", "factual_answer"}),
    },
    "candidate_review": {
        "approved": frozenset({"factual_answer"}),
        "candidate": frozenset({"candidate_hypothesis", "citation_only"}),
    },
    "candidate_evaluation": {"candidate": frozenset({"candidate_hypothesis"})},
    "candidate_guidance": {"candidate": frozenset({"guidance"})},
    "status_snapshot_current": {
        "approved": frozenset({"factual_answer", "citation_only", "guidance", "strategic_context"}),
        "candidate": frozenset({"citation_only", "candidate_hypothesis", "guidance"}),
    },
    "status_snapshot_history": {
        "approved": frozenset({"factual_answer", "citation_only", "guidance", "strategic_context"}),
        "candidate": frozenset({"citation_only", "candidate_hypothesis", "guidance"}),
    },
    "entity_navigation": {"approved": frozenset({"navigation_only"})},
    "candidate_entity_navigation": {
        "candidate": frozenset({"navigation_only"}),
    },
}
LEGACY_DEFAULT_RETRIEVAL_MODE_BY_LANES = {
    frozenset({"approved"}): "factual",
    frozenset({"approved", "candidate"}): "candidate_review",
    frozenset({"candidate"}): "candidate_evaluation",
}


def _pack(values: Iterable[float]) -> bytes:
    import numpy as np

    return np.ascontiguousarray(list(values), dtype="<f4").tobytes()


def _unpack(value: bytes) -> tuple[float, ...]:
    import numpy as np

    return tuple(float(x) for x in np.frombuffer(value, dtype="<f4"))


with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r") as f:
    SIDECAR_SCHEMA = f.read()
