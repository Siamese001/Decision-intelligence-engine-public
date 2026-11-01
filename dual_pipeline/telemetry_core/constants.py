"""
telemetry_core/constants.py — Governed span names, attribute keys, and ledger schemas.
"""
from __future__ import annotations

from pathlib import Path

from storage.connection import sqlite3

SPAN_NAMES: dict[str, dict[str, str]] = {
    "intake": {
        "U0": "nee.uc1.u0.validate",
        "L1": "nee.uc1.l1.plan",
        "L0": "nee.uc1.l0.route",
        "C0.1": "nee.uc1.c0.entity_resolution",
        "C0.2": "nee.uc1.c0.semantic_compare",
        "C0.3": "nee.uc1.c0.semantic_compare",
        "L2D": "nee.uc1.l2.decide",
        "PA": "nee.uc1.pa.assemble",
        "L2": "nee.uc1.l2.advise",
        "X1": "nee.uc1.x.exit",
        "X2": "nee.uc1.x.exit",
        "X3": "nee.uc1.x.exit",
        "W0": "nee.uc1.w0.graph_write",
        "E0": "nee.uc1.e0.index_build",
        "L4": "nee.uc1.l4.seal",
    },
    "query": {
        "U0": "nee.uc2.u0.validate",
        "L1": "nee.uc2.l1.plan",
        "L0": "nee.uc2.l0.route",
        "C0.1": "nee.uc2.c0.exact",
        "C0.2": "nee.uc2.c0.hybrid",
        "C0.3": "nee.uc2.c0.graph_hydrate",
        "L2D": "nee.uc2.l2.decide",
        "PA": "nee.uc2.pa.assemble",
        "L2": "nee.uc2.l2.reason",
        "X1": "nee.uc2.x.exit",
        "X2": "nee.uc2.x.exit",
        "X3": "nee.uc2.x.exit",
        "L4": "nee.uc2.l4.seal",
    },
    "hitl": {
        "H0": "nee.hitl.h0.calibrate",
        "H1": "nee.hitl.h1.capture",
        "H2": "nee.hitl.h2.persist",
        "H3": "nee.hitl.h3.telemetry",
        "L4": "nee.hitl.l4.seal",
    },
    "learning": {
        "LP0": "nee.learning.lp0.ingest_cluster",
        "LP1": "nee.learning.lp1.propose",
        "LP2": "nee.learning.lp2.simulate",
        "LP3": "nee.learning.lp3.approve",
        "LP4": "nee.learning.lp4.commit",
        "LP5": "nee.learning.lp5.canary",
        "L4": "nee.learning.l4.seal",
    },
}

L2_OTEL_ATTRIBUTE_KEYS = frozenset(
    {
        "query_type",
        "materiality",
        "assurance_profile",
        "initial_complexity",
        "final_complexity",
        "classification_source",
        "classifier_call_status",
        "l2_policy",
        "l2_required",
        "l2_reason_code",
        "selected_model_tier",
        "selected_model_profile",
        "selected_model",
        "delivered_model_tier",
        "delivered_model_profile",
        "delivered_model",
        "provider_egress_allowed",
        "approved_evidence_count",
        "candidate_evidence_count",
        "external_public_evidence_count",
        "conflict_count",
        "gap_count",
        "graph_path_count",
        "call_purpose",
        "call_ordinal",
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
        "input_output_ratio",
        "cached_input_share",
        "request_input_chars",
        "request_schema_chars",
        "request_serialized_chars",
        "request_payload_digest",
        "budget_status",
        "budget_reason_code",
        "estimated_input_cost",
        "estimated_output_cost",
        "estimated_total_cost",
        "cost_usd",
        "cached_tokens_saved_usd",
        "reasoning_tokens",
        "latency_ms",
        "disposition",
        "model_call_count",
        "repair_call_count",
        "escalation_call_count",
        "judge_call_count",
        "challenger_call_count",
        "response_digest",
        "run_mode",
    }
)

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_event (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    use_case TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_reference TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    artifact_json TEXT NOT NULL CHECK (json_valid(artifact_json)),
    created_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_learning_event_run_stage
    ON learning_event(run_id, stage, created_at, event_id);
CREATE TABLE IF NOT EXISTS run_seal (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    use_case TEXT NOT NULL,
    status TEXT NOT NULL,
    required_stages_json TEXT NOT NULL CHECK (json_valid(required_stages_json)),
    observed_stages_json TEXT NOT NULL CHECK (json_valid(observed_stages_json)),
    event_count INTEGER NOT NULL,
    event_chain_digest TEXT NOT NULL,
    sealed_at TEXT NOT NULL
) WITHOUT ROWID;
"""


def ledger_connection(path: Path) -> sqlite3.Connection:
    """Initializes and returns a WAL-mode SQLite connection to the learning ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    from ..storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(LEDGER_SCHEMA)
    return connection


_ledger_connection = ledger_connection
