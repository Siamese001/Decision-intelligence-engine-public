"""Governed constants and component definitions for GraphDB delta calculations."""
from __future__ import annotations

_COMPONENTS = (
    ("source", "Sources", "source"),
    ("source_version", "Source versions", "source_version"),
    ("evidence", "Evidence items", "evidence_item"),
    ("external_concept", "Source-scoped external concepts", "node"),
    ("internal_entity", "Internal candidate entities", "node"),
    ("candidate_assertion", "Candidate assertions", "candidate_assertion"),
    ("canonical_assertion", "Canonical assertions", "canonical_assertion"),
    ("superseded_assertion", "Superseded assertions", "assertion"),
    ("candidate_search_document", "Candidate search documents", "candidate_search_document"),
    ("audit_event", "Graph audit events", "audit_event"),
)

_SIDECAR_COMPONENTS = (
    ("dense_vector", "Dense vector embeddings (1024-dim BGE-M3 float32)", "dense_vector"),
    ("sparse_weight", "Sparse token weights (BGE-M3 learned lexical)", "sparse_weight"),
    ("colbert_token", "ColBERT multi-vectors (contextual token embeddings)", "colbert_token"),
    ("retrieval_record", "Retrieval records (clearance-approved corpus)", "retrieval_record"),
)

_SIDECAR_NET_KEYS = {
    "dense_vector": "dense_vectors",
    "sparse_weight": "sparse_weights",
    "colbert_token": "colbert_tokens",
    "retrieval_record": "retrieval_records",
}
