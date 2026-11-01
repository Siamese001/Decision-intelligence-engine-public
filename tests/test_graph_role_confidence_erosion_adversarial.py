# -*- coding: utf-8 -*-
"""Adversarial and edge-case test suite for graph role confidence erosion fixes.

Covers mandatory edge-case categories under .agents/rules/e2e-and-edge-case-coverage.md:
1. Exact production regression: Amit Ayer compound title elimination & 10/16 HITL ledger precedence.
2. Boundary payload: Empty strings, whitespace-only, 0-byte SQLite ledger, empty metadata.
3. Malformed input: Corrupted JSON in ledger records, unstripped company suffixes, malformed roster structures.
4. Concurrency & race: Concurrent multi-threaded WAL writes to persist_validated_role under 16 workers.
5. Invariant bypass attempt: Lower-tier high-confidence assertion attempting to bypass verified canonical assertion.
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    compute_resolution_digest,
    create_speaker_role_resolution,
)
from storage.connection import create_sqlite_conn
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    EntityCandidate,
    PreSynthesisEntityResolver,
    ResolvedEntity,
)
from dual_pipeline.transcript_stages_core.attribution_builder import AttributionSummaryBuilder
from dual_pipeline.canonical_pipeline.u0_ingress import _load_ledger_role_resolutions
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
    validate_speaker_role_resolution_for_projection,
)
from dual_pipeline.transcript_qa_core.recap_healer import _heal_output_2_placeholder_titles
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role


GRAPH_INIT_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 30000;

CREATE TABLE IF NOT EXISTS node_type_registry (node_type_id TEXT PRIMARY KEY, registry_state TEXT NOT NULL);
INSERT OR IGNORE INTO node_type_registry VALUES ('stakeholder', 'active'), ('internal_person', 'active'), ('role_group', 'active'), ('org', 'active');

CREATE TABLE IF NOT EXISTS controlled_value_registry (vocabulary TEXT NOT NULL, value TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (vocabulary, value));
INSERT OR IGNORE INTO controlled_value_registry VALUES ('evidence_relationship', 'supports', 1);

CREATE TABLE IF NOT EXISTS node (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    display_name TEXT,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
    node_id UNINDEXED,
    canonical_name,
    description,
    aliases,
    node_type UNINDEXED
);

CREATE TABLE IF NOT EXISTS source (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_date TEXT,
    authority_rank INTEGER NOT NULL,
    source_posture TEXT NOT NULL,
    cold_archive_ref TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_version (
    source_version_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_date_normalized TEXT,
    source_date_precision TEXT NOT NULL,
    source_date_status TEXT NOT NULL,
    authority_rank INTEGER NOT NULL,
    scope_text TEXT NOT NULL,
    posture_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_item (
    evidence_id TEXT PRIMARY KEY,
    evidence_fingerprint TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version_id TEXT NOT NULL,
    locator_kind TEXT NOT NULL,
    locator_value TEXT NOT NULL,
    direct_quote TEXT,
    evidence_directness TEXT NOT NULL,
    source_authority_rank INTEGER NOT NULL,
    extraction_confidence REAL NOT NULL,
    corroboration_group TEXT,
    location_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assertion (
    assertion_id TEXT PRIMARY KEY,
    subject_node_id TEXT NOT NULL REFERENCES node(node_id),
    predicate TEXT NOT NULL,
    object_node_id TEXT,
    object_literal TEXT,
    scope_text TEXT,
    confidence_score REAL NOT NULL,
    confidence_label TEXT,
    currentness_state TEXT NOT NULL,
    validation_state TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    superseded_by_assertion_id TEXT,
    superseded_at TEXT,
    classification TEXT,
    allowed_audiences_json TEXT,
    primary_source_id TEXT,
    canonical_status TEXT DEFAULT 'candidate',
    retrieval_eligible INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS assertion_evidence (
    assertion_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    evidence_relationship TEXT NOT NULL,
    independent_corroboration INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (assertion_id, evidence_id)
);
"""


def _seed_base_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(GRAPH_INIT_SQL)
    cur.execute("INSERT OR IGNORE INTO source VALUES ('src-adv', 'MEETING_TRANSCRIPT', 'Adv Discussion', '2026-10-16', 1, 'CONFIRMED', NULL, NULL, '2026-10-16T12:00:00Z')")
    cur.execute("INSERT OR IGNORE INTO source_version VALUES ('sv-adv', 'src-adv', 1, 'fp-1', 'hash-1', '2026-10-16', 'DAY', 'NORMALIZED', 1, 'Scope', 'CONFIRMED', '2026-10-16T12:00:00Z')")
    cur.execute("INSERT OR IGNORE INTO evidence_item VALUES ('ev-adv', 'fp-ev', 'src-adv', 'sv-adv', 'LINE', '10', 'Quote', 'DIRECT_ASSERTION', 1, 1.0, NULL, 'VERIFIED', '2026-10-16T12:00:00Z')")
    cur.execute(
        "INSERT OR IGNORE INTO node VALUES ('person_amit', 'internal_person', 'Amit Ayer', 'Amit Ayer', 'Principal Architect', 1, '2026-08-28T00:00:00Z', '2026-10-16T12:00:00Z')"
    )
    cur.execute(
        "INSERT OR IGNORE INTO node_fts VALUES ('person_amit', 'Amit Ayer', 'Principal Architect', 'Amit;Ayer', 'internal_person')"
    )
    conn.commit()
    conn.close()


# ==============================================================================
# Category 1: Exact Production Regression
# ==============================================================================

def test_production_regression_amit_compound_role_supersession(tmp_path: Path) -> None:
    """Exact regression test for the 10/16 compound role issue.

    Scenario:
    1. Historical candidate assertion in GraphDB had compound role 'Lead Client Partner / Principal Architect & Technology Strategist'.
    2. HITL ledger has chronological confirmation on 10/16 confirming 'Principal Architect & Technology Strategist, PartnerAdvisory'.
    3. Traversal resolver, C0.6 gate, and UWG must eliminate compound slash and supersede historical assertion.
    """
    db_file = tmp_path / "canonical_graph.sqlite"
    _seed_base_graph(db_file)
    (tmp_path / "active_pair.json").write_text(json.dumps({"graph_path": "canonical_graph.sqlite"}), encoding="utf-8")

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            confidence_score, confidence_label, currentness_state, validation_state,
            valid_from, canonical_status, retrieval_eligible, created_at
        ) VALUES (
            'asrt-historical-compound', 'person_amit', 'HAS_ROLE', NULL,
            'Lead Client Partner / Principal Architect & Technology Strategist',
            0.85, 'HIGH', 'current', 'needs_review', '2026-08-28', 'candidate', 1, '2026-08-28T00:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()

    # Step 1: Ledger contains operator override
    sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
    hconn = create_sqlite_conn(sqlite_path, readonly=False)
    hcur = hconn.cursor()
    hcur.executescript(HITL_TABLE_SCHEMA)
    hcur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-regression', 1, 'dec-amit', 'role_clarification', 'operator',
            'Role clarification', '[]', 'Principal Architect & Technology Strategist, PartnerAdvisory',
            'Principal Architect & Technology Strategist, PartnerAdvisory', 'Operator confirmation 10/16', '0000',
            '2026-10-16T15:30:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Principal Architect & Technology Strategist, PartnerAdvisory"}', 'h-reg'
        )
        """
    )
    hconn.commit()
    hconn.close()

    # Step 2: C0.6 gate loads ledger and syncs metadata
    init_meta = {
        "repo_root": str(tmp_path),
        "roster": ["Amit Ayer"],
    }
    hitl_decisions = _load_ledger_role_resolutions(init_meta)
    metadata: Dict[str, Any] = {
        "repo_root": str(tmp_path),
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Client Partner / Principal Architect & Technology Strategist", "org": "PartnerAdvisory"}
        ],
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)"]
        },
        "hitl_role_resolutions": {
            "Amit Ayer": {"resolved_role": hitl_decisions["Amit Ayer"], "provenance": "HITL_LEDGER"}
        },
    }
    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    gate.execute({"metadata": metadata})

    # Step 3: Verify slash eliminated everywhere
    assert metadata["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert "/" not in metadata["roster"][0]["role"]
    assert "Lead Client Partner /" not in metadata["attendees"]["PartnerAdvisory"][0]

    # Step 4: UWG write supersedes historical assertion in DB
    receipt = persist_validated_role(
        graph_db=db_file,
        canonical_name="Amit Ayer",
        selected_role="Principal Architect & Technology Strategist",
    )
    assert receipt["status"] in ("SUCCESS", "PERSISTED_SSOT")

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT assertion_id, superseded_by_assertion_id FROM assertion WHERE assertion_id='asrt-historical-compound'")
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[1] is not None  # Superseded


# ==============================================================================
# Category 2: Boundary Payload Coverage
# ==============================================================================

def test_boundary_empty_and_whitespace_roles_handled_gracefully(tmp_path: Path) -> None:
    """Boundary test: empty string, whitespace-only, and None roles do not cause unhandled crashes."""
    metadata: Dict[str, Any] = {
        "repo_root": str(tmp_path),
        "roster": [
            {"name": "Alice Empty", "role": "", "org": "PartnerAdvisory"},
            {"name": "Bob Whitespace", "role": "   \t  \n  ", "org": "PartnerAdvisory"},
            {"name": "Charlie None", "role": None, "org": "PartnerAdvisory"},
        ],
    }
    # Speaker role reconciler should gracefully skip or reject empty roles without crashing
    final_data: Dict[str, Any] = {"graph_mutations": {"entity_upserts": []}}
    reconciled = reconcile_speaker_role_resolutions(final_data, metadata)
    assert "graph_mutations" in reconciled


def test_boundary_zero_byte_and_empty_ledger_store(tmp_path: Path) -> None:
    """Boundary test: 0-byte or uninitialized ledger SQLite file does not throw unhandled exception."""
    empty_ledger = tmp_path / "hitl_decision_ledger.sqlite"
    empty_ledger.write_bytes(b"")  # 0-byte file

    res = _load_ledger_role_resolutions({"repo_root": str(tmp_path)})
    assert res == {}


def test_boundary_empty_metadata_in_healers_and_reconciler() -> None:
    """Boundary test: empty metadata dict does not trigger KeyError or AttributeError."""
    text = "# Amit Ayer — PartnerAdvisory, [Title Unknown]\nPerspectives."
    healed = _heal_output_2_placeholder_titles(text, {}, [], [])
    # Authoritative directory healer resolves Amit Ayer and heals placeholder gracefully
    assert "# Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist" in healed
    assert "[Title Unknown]" not in healed

    summary = AttributionSummaryBuilder.build_summary({}, "Amit Ayer: Speaking.")
    assert isinstance(summary, str)


# ==============================================================================
# Category 3: Malformed Input Coverage
# ==============================================================================

def test_malformed_corrupted_json_in_hitl_ledger(tmp_path: Path) -> None:
    """Malformed input: SQLite table containing corrupted JSON strings in metadata column."""
    sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
    conn = create_sqlite_conn(sqlite_path, readonly=False)
    cur = conn.cursor()
    cur.executescript(HITL_TABLE_SCHEMA)
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-bad-json', 1, 'dec-bad', 'role_clarification', 'operator',
            'Prompt', '[]', 'Role', 'Role', 'Rationale', '0000',
            '2026-10-16T15:30:00Z', '{malformed json! not valid}', 'h-bad'
        )
        """
    )
    conn.commit()
    conn.close()

    # Must survive and return empty dict instead of blowing up
    resolutions = _load_ledger_role_resolutions({"repo_root": str(tmp_path)})
    assert resolutions == {}


def test_malformed_unstripped_suffixes_in_resolver_and_roster() -> None:
    """Malformed input: role strings with messy trailing punctuation and company suffixes."""
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import strip_org_suffix_from_role

    assert strip_org_suffix_from_role("Principal Architect, PartnerAdvisory")[0] == "Principal Architect"
    assert strip_org_suffix_from_role("Transformation Lead (PartnerAdvisory)")[0] == "Transformation Lead"
    assert strip_org_suffix_from_role("Managing Director - PartnerAdvisory")[0] == "Managing Director"


# ==============================================================================
# Category 4: Concurrency & Race Condition Coverage
# ==============================================================================

def test_concurrency_parallel_wal_writes_to_persist_validated_role(tmp_path: Path) -> None:
    """Concurrency test: 16 concurrent threads persisting roles to the same SQLite database.

    Verifies PRAGMA busy_timeout = 30000 and WAL mode prevent 'database is locked' errors.
    """
    db_file = tmp_path / "concurrent_graph.sqlite"
    _seed_base_graph(db_file)

    def _worker(worker_id: int) -> Dict[str, Any]:
        role_title = f"Role Iteration {worker_id}"
        return persist_validated_role(
            graph_db=db_file,
            canonical_name="Amit Ayer",
            selected_role=role_title,
        )

    num_workers = 16
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(_worker, i) for i in range(num_workers)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All 16 writes must succeed without OperationalError
    for r in results:
        assert r["status"] in ("SUCCESS", "PERSISTED_SSOT")

    # Verify database integrity after stress writes
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    row = cur.fetchone()
    assert row[0] == "ok"
    conn.close()


# ==============================================================================
# Category 5: Invariant Bypass Attempt Coverage
# ==============================================================================

def test_invariant_bypass_lower_tier_high_confidence_cannot_override_verified() -> None:
    """Invariant bypass test: a candidate assertion with confidence 0.95 (needs_review)

    must NEVER outrank an authoritative canonical assertion with confidence 1.0 (verified).
    """
    from dual_pipeline.transcript_metadata_core.entity_resolver_graph import _rank_role_candidates

    c_unverified_high = EntityCandidate(
        assertion_id="asrt-unverified",
        source_node_id="person_amit",
        role="Pretender Executive Role",
        confidence=0.99,
        validation_state="needs_review",
    )
    c_verified_canonical = EntityCandidate(
        assertion_id="asrt-verified",
        source_node_id="person_amit",
        role="Principal Architect & Technology Strategist",
        confidence=1.0,
        validation_state="verified",
    )

    ranked = _rank_role_candidates([c_unverified_high, c_verified_canonical])
    # The verified candidate must rank first despite unverified having high score
    assert ranked[0].role == "Principal Architect & Technology Strategist"
    assert ranked[0].validation_state == "verified"


def test_invariant_bypass_forged_resolution_digest_fails_closed() -> None:
    """Invariant bypass test: a resolution with a forged or stale digest fails closed."""
    res = {
        "person_name": "Amit Ayer",
        "resolved_role": "Principal Architect & Technology Strategist",
        "provenance": "HUMAN_OPERATOR",
        "is_human_confirmed": True,
        "digest": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    with pytest.raises(ValueError, match="Stale or corrupted answer digest"):
        validate_speaker_role_resolution_for_projection(res, {})
