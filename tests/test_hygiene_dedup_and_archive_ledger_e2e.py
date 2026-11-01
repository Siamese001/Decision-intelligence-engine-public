"""End-to-end tests for Graph Hygiene Deduplication & Graph-vs-Archive Posture Ledger."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from dual_pipeline.graph_maintenance_core.health import calculate_graph_health_metrics
from dual_pipeline.graph_maintenance_core.hygiene_agent import Finding
from dual_pipeline.graph_maintenance_core.hygiene_council import (
    CouncilDebateRecord,
    _format_finding_row,
)
from dual_pipeline.graph_maintenance_core.posture_ledger import (
    snapshot_graph_vs_archive_posture,
)
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def test_wave1_posture_snapshot_and_hitl_cells_distinguish_active_graph_vs_archive(tmp_path: Path) -> None:
    """Verify posture snapshot partitions Active Graph, Staging Hold, and Historical Archive with exact sum parity."""
    db_path = tmp_path / "posture_wave1.sqlite"
    _bootstrap_test_graph(db_path)

    conn = sqlite3.connect(str(db_path))
    # Add 1 candidate node, 1 merged node, 1 archived_orphan node
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, status, active) VALUES ('n_cand', 'workstream', 'Cand WS', 'candidate', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, status, active) VALUES ('n_merged', 'workstream', 'Merged WS', 'merged', 0)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, status, active) VALUES ('n_orphan', 'workstream', 'Orphan WS', 'archived_orphan', 0)"
    )
    # Add assertions: 1 active canonical, 1 staging candidate, 1 superseded archive, 1 rejected archive
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, currentness_state, canonical_status, retrieval_eligible, validation_state) "
        "VALUES ('a_active', 'stakeholder_dan_okray', 'LEADS', 'Dan leads AI', 'current', 'canonical', 1, 'verified')"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, currentness_state, canonical_status, retrieval_eligible, validation_state) "
        "VALUES ('a_staging', 'stakeholder_dan_okray', 'NOTES', 'Draft note', 'current', 'candidate', 0, 'needs_review')"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, currentness_state, canonical_status, retrieval_eligible, validation_state, superseded_by_assertion_id) "
        "VALUES ('a_sup', 'stakeholder_dan_okray', 'LEADS', 'Dan leads Data', 'historical', 'canonical', 0, 'superseded', 'a_active')"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, currentness_state, canonical_status, retrieval_eligible, validation_state) "
        "VALUES ('a_rej', 'stakeholder_dan_okray', 'LEADS', 'Bleed claim', 'historical', 'historical_candidate', 0, 'rejected')"
    )
    conn.commit()

    posture = snapshot_graph_vs_archive_posture(conn)
    for key in ("nodes", "assertions", "search_documents"):
        sec = posture[key]
        assert sec["active_graph"] + sec["staging_hold"] + sec["historical_archive"] == sec["total"]

    assert posture["nodes"]["active_graph"] == 1
    assert posture["nodes"]["staging_hold"] == 1
    assert posture["nodes"]["historical_archive"] == 2
    assert posture["assertions"]["active_graph"] == 1
    assert posture["assertions"]["staging_hold"] == 1
    assert posture["assertions"]["historical_archive"] == 2

    health = calculate_graph_health_metrics(conn)
    assert "graph_vs_archive_posture" in health
    conn.close()

    # Verify HITL row formatting explicitly labels [KEPT IN ACTIVE GRAPH] and [MOVED TO ARCHIVE]
    debate = CouncilDebateRecord(0, "CLUSTER-02A", "Alpha", "Beta", "Approve")
    f_merge = Finding(
        detective="StructuralValidator",
        severity="HIGH",
        description="Merge pair",
        entity_references=["stakeholder_dan_okray", "person_dan"],
        proposed_fix="merge_entities",
        raw_data={"canonical_name_a": "Daniel Okray", "canonical_name_b": "Drew Oakley", "similarity_score": 0.95, "margin_delta": 0.4},
    )
    row_merge = _format_finding_row(f_merge, debate)
    assert "[KEPT IN ACTIVE GRAPH]: Daniel Okray" in row_merge
    assert "[MOVED TO ARCHIVE]: Drew Oakley" in row_merge

    f_dedup = Finding(
        detective="StructuralValidator",
        severity="MEDIUM",
        description="Dedup statement",
        entity_references=["a1", "a2"],
        proposed_fix="deduplicate_assertions",
        raw_data={"winner_claim": "Canonical statement text", "loser_claim": "Duplicate statement text", "similarity": 0.98},
    )
    row_dedup = _format_finding_row(f_dedup, debate)
    assert "[KEPT IN ACTIVE GRAPH]: Canonical statement text" in row_dedup
    assert "[MOVED TO ARCHIVE]: Duplicate statement text" in row_dedup


def test_wave2_e2e_remediation_emits_deduplication_ledger_and_graph_vs_archive_summary(tmp_path: Path) -> None:
    """Verify run_hygiene_agent_cron.py and apply_hygiene_remediation emit complete deduplication ledger and posture transition."""
    db_path = tmp_path / "e2e_ledger.sqlite"
    _bootstrap_test_graph(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, normalized_name, description, status, active) "
        "VALUES ('person_dan_dup', 'stakeholder', 'Drew Oakley', 'Drew Oakley', 'Drew Oakley', 'Sponsor dup', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, object_literal, currentness_state, canonical_status, canonical_eligible, retrieval_eligible, validation_state, confidence_score) "
        "VALUES ('asrt_win', 'stakeholder_dan_okray', 'OWNS', 'Daniel Okray owns the AI charter.', 'AI charter', 'current', 'canonical', 1, 1, 'verified', 0.95)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, object_literal, currentness_state, canonical_status, canonical_eligible, retrieval_eligible, validation_state, confidence_score) "
        "VALUES ('asrt_lose', 'stakeholder_dan_okray', 'OWNS', 'Daniel Okray owns the AI charter.', 'AI charter', 'current', 'canonical', 1, 1, 'accepted_legacy', 0.85)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, object_literal, currentness_state, canonical_status, canonical_eligible, retrieval_eligible, validation_state, confidence_score) "
        "VALUES ('asrt_stale', 'stakeholder_dan_okray', 'NOTES', 'Unpromoted draft note', 'Draft', 'current', 'candidate', 0, 0, 'needs_review', 0.50)"
    )
    conn.commit()
    conn.close()

    # 1. Run CLI discovery via subprocess to verify real entrypoint execution
    cron_pkg_path = tmp_path / "cron_pkg.json"
    repo_root = Path(__file__).resolve().parent.parent
    res = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "run_hygiene_agent_cron.py"), "--graph", str(db_path), "--output", str(cron_pkg_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    cron_pkg = json.loads(cron_pkg_path.read_text(encoding="utf-8"))
    assert "graph_vs_archive_posture" in cron_pkg
    assert cron_pkg["graph_vs_archive_posture"]["nodes"]["active_graph"] == 2

    # 2. Apply hygiene remediation with entity merge, duplicate assertion, and candidate archival
    pkg_path = tmp_path / "remediate_pkg.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "proposed_fix": "merge_entities",
                        "entity_references": ["stakeholder_dan_okray", "person_dan_dup"],
                        "raw_data": {
                            "node_id_a": "stakeholder_dan_okray",
                            "node_id_b": "person_dan_dup",
                            "canonical_name_a": "Daniel Okray",
                            "canonical_name_b": "Drew Oakley",
                        },
                    },
                    {
                        "proposed_fix": "deduplicate_assertions",
                        "entity_references": ["asrt_lose", "asrt_win"],
                        "raw_data": {
                            "winner_id": "asrt_win",
                            "loser_id": "asrt_lose",
                            "winner_claim": "Daniel Okray owns the AI charter.",
                            "loser_claim": "Daniel Okray owns the AI charter.",
                            "similarity": 1.0,
                        },
                    },
                    {
                        "proposed_fix": "archive_candidate_assertion",
                        "entity_references": ["asrt_stale"],
                        "raw_data": {"assertion_id": "asrt_stale"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"
    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path, receipt_path=receipt_path, strict_delta=True)
    assert receipt["terminal_outcome"] == "PASS"

    gva = receipt["graph_vs_archive_summary"]
    assert gva["before"]["nodes"]["active_graph"] == 2
    assert gva["after"]["nodes"]["active_graph"] == 1
    assert gva["after"]["nodes"]["historical_archive"] == 1
    assert gva["delta"]["nodes"]["active_graph"] == -1
    assert gva["delta"]["nodes"]["historical_archive"] == 1

    assert gva["before"]["assertions"]["active_graph"] == 2
    assert gva["after"]["assertions"]["active_graph"] == 1
    assert gva["after"]["assertions"]["historical_archive"] == 2

    ledger = receipt["deduplication_ledger"]
    assert ledger["counts"]["entity_pairs_deduplicated"] == 1
    assert ledger["counts"]["assertions_deduplicated"] == 1
    assert ledger["counts"]["standalone_records_archived_or_quarantined"] == 1
    assert ledger["entity_merges"][0]["kept_in_active_graph"]["node_id"] == "stakeholder_dan_okray"
    assert ledger["entity_merges"][0]["moved_to_archive"]["node_id"] == "person_dan_dup"
    assert ledger["assertion_deduplications"][0]["kept_in_active_graph"]["assertion_id"] == "asrt_win"
    assert ledger["assertion_deduplications"][0]["moved_to_archive"]["assertion_id"] == "asrt_lose"
    assert "### Active Graph vs. Historical Archive Posture" in receipt["plain_english_posture_report"]
    assert "### Itemized Deduplication Ledger" in receipt["plain_english_posture_report"]
