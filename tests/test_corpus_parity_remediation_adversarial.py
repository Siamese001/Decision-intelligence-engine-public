"""Wave 4 adversarial and edge-case tests for corpus parity remediation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from tools.document_graph_parity_core import (
    _extract_signal_claims_from_projection,
    promote_candidate_nodes_and_link_orphans,
    remediate_boilerplate_assertions,
    sanitize_staging_refs_in_db,
)
from tools.remediate_document_graph_parity import execute_remediation_on_db
from tests.test_corpus_parity_remediation_e2e import _create_v48_graph_db


# Category 1: Boundary payload
def test_boundary_payload_empty_projection_signal_extraction() -> None:
    assert _extract_signal_claims_from_projection({}, "Empty Doc") == []
    assert _extract_signal_claims_from_projection({"graph_mutations": "invalid"}, "Bad Doc") == []


# Category 2: Malformed input
def test_malformed_input_non_dict_entries_in_projection_mutations() -> None:
    proj = {
        "graph_mutations": {
            "entity_upserts": ["bad", {"id": "system:ok", "name": "System OK", "description": "Valid substantive system description."}],
            "relationship_signals": [None, 42],
            "decision_updates": ["not-a-dict", {"id": "decision:1", "statement": "Approve target state."}],
        }
    }
    claims = _extract_signal_claims_from_projection(proj, "Malformed Doc")
    assert len(claims) == 2


# Category 3: Concurrency & race
def test_concurrency_sequential_idempotent_remediation_runs(tmp_path: Path) -> None:
    db_path = _create_v48_graph_db(tmp_path / "idempotent.sqlite")

    res1 = execute_remediation_on_db(db_path, tmp_path / "empty_output")
    assert res1["terminal_outcome"] == "PASS"

    res2 = execute_remediation_on_db(db_path, tmp_path / "empty_output")
    assert res2["terminal_outcome"] == "PASS"
    assert res2["stats"]["staging_refs_sanitized"] == 0
    assert res2["stats"]["promoted_candidate_nodes"] == 0


# Category 4: Overflow & exhaustion
def test_overflow_large_batch_of_candidate_and_orphan_nodes(tmp_path: Path) -> None:
    db_path = _create_v48_graph_db(tmp_path / "overflow_nodes.sqlite")

    conn = sqlite3.connect(db_path)
    for i in range(150):
        conn.execute(
            "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, normalized_name, description, status, confidence_score, created_at, updated_at, active) "
            "VALUES (?, 'technical_anchor', ?, ?, 'Batch node', 'candidate', 0.9, '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z', 1)",
            (f"concept:batch-{i:03d}", f"Batch Concept {i}", f"batch concept {i}"),
        )
    conn.commit()
    stats = promote_candidate_nodes_and_link_orphans(conn, "2026-10-26T12:00:00Z")
    conn.commit()
    conn.close()

    assert stats["promoted_candidate_nodes"] >= 150
    assert stats["linked_orphan_nodes"] >= 150


# Category 5: Invariant bypass attempt
def test_invariant_bypass_boilerplate_claims_eliminated(tmp_path: Path) -> None:
    db_path = _create_v48_graph_db(tmp_path / "boiler.sqlite")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, normalized_name, description, status, confidence_score, created_at, updated_at, active) "
        "VALUES ('metric:gold-total', 'technical_anchor', 'Pitch & Rotor Total', 'pitch rotor total', 'Pitch & Rotor Total: 123 assets representing 38.2% of Gold Dataset.', 'canonical', 0.9, '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z', 1)"
    )
    any_asrt = conn.execute("SELECT assertion_id FROM assertion LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE assertion SET subject_node_id = 'metric:gold-total', scope_text = 'Pitch & Rotor Total is an identified metric in Document Gold Dataset.', object_literal = 'Pitch & Rotor Total is an identified metric in Document Gold Dataset.' WHERE assertion_id = ?",
        (any_asrt,),
    )
    conn.commit()

    fixed = remediate_boilerplate_assertions(conn, "2026-10-26T12:00:00Z")
    conn.commit()
    row = conn.execute("SELECT scope_text FROM assertion WHERE assertion_id = ?", (any_asrt,)).fetchone()
    conn.close()

    assert fixed >= 1
    assert "123 assets representing 38.2%" in row[0]
    assert "is an identified" not in row[0].lower()


# Category 6: Rollback & recovery
def test_rollback_and_recovery_staging_ref_sanitization(tmp_path: Path) -> None:
    db_path = _create_v48_graph_db(tmp_path / "staging.sqlite")
    conn = sqlite3.connect(db_path)
    any_src = conn.execute("SELECT source_id FROM source LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE source SET cold_archive_ref = '.staging_Serpa_Intro_Angela_Michael_04496b59/01_transcript.md' WHERE source_id = ?",
        (any_src,),
    )
    conn.commit()
    cleaned = sanitize_staging_refs_in_db(conn)
    conn.commit()
    ref_after = conn.execute("SELECT cold_archive_ref FROM source WHERE source_id = ?", (any_src,)).fetchone()[0]
    conn.close()
    assert cleaned == 1
    assert not ref_after.startswith(".staging_")


# Category 7: Exact production regression
def test_exact_production_regression_re_running_remediation_preserves_canonical_assertions(tmp_path: Path) -> None:
    db_path = _create_v48_graph_db(tmp_path / "prod_reg.sqlite")
    res = execute_remediation_on_db(db_path, tmp_path / "empty_out")
    assert res["terminal_outcome"] == "PASS"
    assert res["parity_audit"]["zero_canonical_sources_count"] == 0
    assert res["parity_audit"]["boilerplate_sources_count"] == 0
