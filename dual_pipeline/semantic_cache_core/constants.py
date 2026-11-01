"""Constants for semantic cache."""
from __future__ import annotations

MODES = frozenset({"off", "read", "write", "read_write"})
INITIAL_RUNTIME_MODE = "write"
CONFIG_SCHEMA_VERSION = "2.0"
BGE_M3_BACKEND_IDS = frozenset(
    {
        "flagembedding-bge-m3",
        "flagembedding-bge-m3-dev",
        "flagembedding-bge-m3-internal",
    }
)
QUERY_NAMESPACE_FIELDS = frozenset(
    {
        "cache_payload_kind", "destination", "eligibility_policy_digest",
        "assurance_profile", "delivery_profile", "artifact_type", "classification",
        "audiences", "allowed_lanes", "active_pair", "coverage_requirements_digest",
        "l2_policy_digest", "x1_policy_digest", "provider_egress_allowed",
        "query_plan_version", "l1_controller_mode", "l1_controller_policy_digest",
        "l1_action_policy_digest", "l1_budget_profile",
    }
)
SUPPORTED_SEMANTIC_NAMESPACES = (
    "intake",
    "query",
    "l2_extraction",
    "micro_judge",
    "l2_healer",
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache_embedding_binding (
    binding_id INTEGER PRIMARY KEY CHECK (binding_id=1),
    embedding_backend TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    model_manifest_digest TEXT NOT NULL,
    bound_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS semantic_cache_entry (
    entry_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('PENDING','ACTIVE')),
    use_case TEXT NOT NULL CHECK (use_case IN ('intake','query','l2_extraction','micro_judge','l2_healer')),
    compatibility_digest TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0 AND dimensions <= 4096),
    vector BLOB NOT NULL,
    output_json TEXT NOT NULL CHECK (json_valid(output_json)),
    output_digest TEXT NOT NULL,
    outcome TEXT NOT NULL,
    replay_eligible INTEGER NOT NULL CHECK (replay_eligible IN (0,1)),
    source_run_id TEXT NOT NULL,
    source_trace_id TEXT NOT NULL,
    source_event_count INTEGER,
    source_event_chain_digest TEXT,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    expires_at TEXT NOT NULL,
    accessed_at TEXT
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_semantic_cache_lookup
    ON semantic_cache_entry(
        state,use_case,compatibility_digest,replay_eligible,expires_at,created_at
    );
"""
