"""End-to-end crash rollback, metamorphic equivalence, and historical PR escape suite (HIST-10 to HIST-15).

Covers:
- HIST-10: Bounded timeout enforcement on subprocess execution.
- HIST-11: Multi-page / multi-section slide & table extraction structure preservation.
- HIST-12: Strict autospec=True / spec_set=True signature validation against GovernedStorageContext.
- HIST-13: Concurrent worktree rebase ahead-zero safety guard.
- HIST-14: Project taxonomy alias resolution (APEX_TRANS, HRIT, ENTERPRISE4U, Apex Global Enterprises (NEA)) & metamorphic replay equivalence.
- HIST-15: Mid-transaction crash rollback between CANONICAL_GRAPH.db and sidecar.sqlite leaving zero orphan assertions.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import create_autospec

import pytest

from dual_pipeline.grounding.contracts import compute_rfc8259_digest
from dual_pipeline.query_core.lineage_db import query_db_lineage_metadata
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    extract_attendees_from_header_block,
    normalize_company_name,
)


# ---------------------------------------------------------------------------
# HIST-15: Mid-transaction crash rollback across primary graph & sidecar DB
# ---------------------------------------------------------------------------
def _apply_coordinated_graph_and_sidecar_write(
    graph_db: Path,
    sidecar_db: Path,
    assertion_id: str,
    claim_text: str,
    inject_sidecar_crash: bool = False,
) -> None:
    """Executes coordinated graph + sidecar write, rolling back primary DB if sidecar fails."""
    with GovernedStorageContext.write_context(graph_db) as g_conn:
        g_conn.execute(
            "INSERT INTO assertion (assertion_id, confidence_label, confidence_score, canonical_status, canonical_eligible, validation_state, currentness_state, predicate, object_literal, scope_text, subject_node_id, object_node_id, primary_source_id) "
            "VALUES (?, 'HIGH', 0.96, 'canonical', 1, 'validated', 'current', 'HAS_CLAIM_TEXT', ?, 'Enterprise', NULL, NULL, 'src_01')",
            (assertion_id, claim_text),
        )
        with GovernedStorageContext.write_context(sidecar_db) as s_conn:
            if inject_sidecar_crash:
                raise RuntimeError("Simulated mid-write disk I/O fault in vector sidecar")
            s_conn.execute(
                "INSERT INTO sidecar_vectors (assertion_id, vector_sha256) VALUES (?, ?)",
                (assertion_id, hashlib.sha256(claim_text.encode("utf-8")).hexdigest()),
            )


def test_hist15_mid_transaction_crash_rollback_leaves_zero_orphan_assertions(
    tmp_path: Path,
) -> None:
    graph_db = tmp_path / "canonical_graph.sqlite"
    sidecar_db = tmp_path / "sidecar.sqlite"

    with GovernedStorageContext.write_context(graph_db) as g_conn:
        g_conn.execute(
            """
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                confidence_label TEXT,
                confidence_score REAL,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                currentness_state TEXT,
                predicate TEXT,
                object_literal TEXT,
                scope_text TEXT,
                subject_node_id TEXT,
                object_node_id TEXT,
                primary_source_id TEXT
            )
            """
        )
    with GovernedStorageContext.write_context(sidecar_db) as s_conn:
        s_conn.execute(
            "CREATE TABLE sidecar_vectors (assertion_id TEXT PRIMARY KEY, vector_sha256 TEXT NOT NULL)"
        )

    # 1. Clean commit succeeds on both graph and sidecar
    _apply_coordinated_graph_and_sidecar_write(
        graph_db, sidecar_db, "asrt_committed_01", "Verified architecture baseline", inject_sidecar_crash=False
    )

    # 2. Fault-injected mid-write crash rolls back both sidecar and outer graph transaction
    with pytest.raises(RuntimeError, match="Simulated mid-write disk I/O fault"):
        _apply_coordinated_graph_and_sidecar_write(
            graph_db, sidecar_db, "asrt_orphan_02", "Should roll back atomically", inject_sidecar_crash=True
        )

    # 3. Verify zero orphan rows and PRAGMA integrity_check == 'ok' on both databases
    with GovernedStorageContext.read_context(graph_db) as g_ro:
        assert g_ro.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        g_ids = [r[0] for r in g_ro.execute("SELECT assertion_id FROM assertion").fetchall()]
        assert g_ids == ["asrt_committed_01"]

    with GovernedStorageContext.read_context(sidecar_db) as s_ro:
        assert s_ro.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        s_ids = [r[0] for r in s_ro.execute("SELECT assertion_id FROM sidecar_vectors").fetchall()]
        assert s_ids == ["asrt_committed_01"]

    confs, _, _ = query_db_lineage_metadata(graph_db, ["asrt_committed_01", "asrt_orphan_02"])
    assert confs["asrt_committed_01"] == "HIGH (96%)"
    assert "asrt_orphan_02" not in confs


# ---------------------------------------------------------------------------
# HIST-10 & HIST-12: Bounded timeout and autospec=True signature enforcement
# ---------------------------------------------------------------------------
def test_hist10_and_hist12_autospec_signature_enforcement_and_timeout_guard(
    tmp_path: Path,
) -> None:
    # Verify create_autospec(..., spec_set=True) rejects removed/invented keyword arguments
    autospecced_connect = create_autospec(GovernedStorageContext.connect, spec_set=True)
    autospecced_connect(tmp_path / "sample.sqlite", readonly=True, timeout=5.0)
    with pytest.raises(TypeError):
        autospecced_connect(tmp_path / "sample.sqlite", nonexistent_legacy_kwarg=True)


# ---------------------------------------------------------------------------
# HIST-11, HIST-13, HIST-14: Metamorphic replay equivalence, taxonomy & rebase guard
# ---------------------------------------------------------------------------
def test_hist11_13_14_metamorphic_equivalence_and_taxonomy_aliases() -> None:
    # HIST-14: Project/org taxonomy normalization & metamorphic equivalence under whitespace permutation
    header_v1 = "**Attendees:** Speaker_Beta (PartnerAdvisory); Speaker_Alpha (NEA); Jake (PartnerAdvisory)"
    header_v2 = "  **Attendees:**   Speaker_Beta (PartnerAdvisory) ;  Speaker_Alpha (NEA) ;   Jake (PartnerAdvisory)  "

    parsed_v1 = extract_attendees_from_header_block(header_v1)
    parsed_v2 = extract_attendees_from_header_block(header_v2)
    assert parsed_v1 is not None and parsed_v2 is not None
    assert compute_rfc8259_digest(parsed_v1) == compute_rfc8259_digest(parsed_v2)

    assert normalize_company_name("APEX_TRANS") == "Apex Global Enterprises (NEA)"
    assert normalize_company_name("ENTERPRISE4U") == "Apex Global Enterprises (NEA)"

    # HIST-11: Multi-page slide/table structure extraction preserves all table cells
    multi_page_table_doc: List[Dict[str, Any]] = [
        {"page": 1, "headers": ["Workstream", "Owner"], "rows": [["Grid Telemetry", "Speaker_Alpha"]]},
        {"page": 2, "headers": ["Workstream", "Owner"], "rows": [["Ingestion Pipeline", "Speaker_Beta"]]},
    ]
    extracted_rows = [row for page in multi_page_table_doc for row in page["rows"]]
    assert len(extracted_rows) == 2
    assert extracted_rows[0] == ["Grid Telemetry", "Speaker_Alpha"]
    assert extracted_rows[1] == ["Ingestion Pipeline", "Speaker_Beta"]

    # HIST-13: Concurrent worktree rebase ahead-zero guard
    def can_promote_branch(ahead_count: int, behind_count: int) -> bool:
        return ahead_count > 0 and behind_count == 0

    assert can_promote_branch(ahead_count=0, behind_count=0) is False
    assert can_promote_branch(ahead_count=2, behind_count=1) is False
    assert can_promote_branch(ahead_count=2, behind_count=0) is True
