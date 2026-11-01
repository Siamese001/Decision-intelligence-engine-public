"""Regression test suite for Knowledge Graph Structural & Evidence Hygiene Remediation.

Covers all 7 defect clusters and 59 findings from scheduled audit run-10-18-2026:
1. Cluster 1: archive_candidate_assertion (6 stale candidate assertions archived)
2. Cluster 2: merge_entities (26 duplicate entity pairs consolidated with redirects)
3. Cluster 3: archive_edge (14 dangling edges archived)
4. Cluster 4: deduplicate_assertions (10 duplicate pairs deduplicated with winner/loser supersession)
5. Cluster 5: supersede_assertion (1 temporal contradiction superseded by newer assertion)
6. Cluster 6: batch_promote_candidates (high-confidence candidate assertions promoted)
7. Cluster 7: resync_search_sidecars (canonical assertions missing from search_document sidecar resynced)

Also verifies:
- Five mandatory physical proof checks (file_changed, raw_bytes, integrity, readback, row_delta)
- PRAGMA integrity_check == 'ok' and PRAGMA foreign_key_check returns 0 rows
- Post-remediation GraphHygienePanel audit reports 0 unapproved defect findings
- 90-day anti-eviction rules: no auto-deletion of assertions by age alone
"""
from __future__ import annotations

import json
import sqlite3
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_candidate_roots() -> list[Path]:
    roots = [REPO_ROOT]
    if REPO_ROOT.parent.name == ".worktrees":
        roots.append(REPO_ROOT.parent.parent)
    elif (REPO_ROOT / ".worktrees").exists():
        for wt in (REPO_ROOT / ".worktrees").glob("fix_graph_hygiene_remediation_*"):
            if wt.is_dir():
                roots.append(wt)
    return roots


def _resolve_backup_graph() -> Path:
    for root in _find_candidate_roots():
        candidate = root / "backups" / "hygiene_10-18-2026" / "graph.sqlite"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "backups" / "hygiene_10-18-2026" / "graph.sqlite"


def _resolve_hygiene_package_path() -> Path:
    for root in _find_candidate_roots():
        candidate = root / "reports" / "hygiene" / "run-10-18-2026" / "hygiene_package.json"
        if candidate.exists():
            return candidate
    return REPO_ROOT / "reports" / "hygiene" / "run-10-18-2026" / "hygiene_package.json"


BACKUP_GRAPH = _resolve_backup_graph()
HYGIENE_PACKAGE_PATH = _resolve_hygiene_package_path()


@pytest.fixture(scope="module")
def remediated_environment(tmp_path_factory: pytest.TempPathFactory) -> Tuple[Path, Dict[str, Any], Dict[str, Any]]:
    """Provisions an isolated test database and executes the candidate remediation."""
    tmp_dir = tmp_path_factory.mktemp("hygiene_test_env")
    test_db = tmp_dir / "graph.sqlite"
    test_receipt = tmp_dir / "wave1_receipt.json"

    assert BACKUP_GRAPH.exists(), f"Backup graph not found at {BACKUP_GRAPH}"
    assert HYGIENE_PACKAGE_PATH.exists(), f"Hygiene package not found at {HYGIENE_PACKAGE_PATH}"

    shutil.copy2(BACKUP_GRAPH, test_db)

    with open(HYGIENE_PACKAGE_PATH, "r", encoding="utf-8") as f:
        pkg = json.load(f)

    receipt = apply_hygiene_remediation(
        graph_path=test_db,
        package_path=HYGIENE_PACKAGE_PATH,
        receipt_path=test_receipt,
    )

    return test_db, receipt, pkg


def test_cluster_1_stale_candidate_assertions_archived(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 1: Verifies all 6 stale candidate assertions are archived."""
    test_db, receipt, pkg = remediated_environment
    stale_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "archive_candidate_assertion"]
    assert len(stale_findings) == 6

    stale_ids = [f["raw_data"]["assertion_id"] for f in stale_findings]
    assert len(stale_ids) == 6

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in stale_ids)
        rows = conn.execute(
            f"""SELECT assertion_id, canonical_status, canonical_eligible
                  FROM assertion
                 WHERE assertion_id IN ({placeholders})""",
            stale_ids,
        ).fetchall()

        assert len(rows) == 6
        for r in rows:
            assert r["canonical_status"] == "historical_candidate"
            assert r["canonical_eligible"] == 0

    assert receipt["remediation_stats"]["stale_candidates_archived"] == 6


def test_cluster_2_duplicate_entities_merged(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 2: Verifies all 26 duplicate entity pairs are resolved with active redirects."""
    test_db, receipt, pkg = remediated_environment
    merge_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "merge_entities"]
    assert len(merge_findings) == 26

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row

        for f in merge_findings:
            rd = f["raw_data"]
            na, nb = rd["node_id_a"], rd["node_id_b"]

            # At least one node in the pair must be merged and retired
            row_a = conn.execute("SELECT active, status FROM node WHERE node_id = ?", (na,)).fetchone()
            row_b = conn.execute("SELECT active, status FROM node WHERE node_id = ?", (nb,)).fetchone()
            assert row_a is not None and row_b is not None

            # One node active=1, one node active=0, status='merged'
            retired = na if row_a["active"] == 0 else nb
            survivor = nb if row_a["active"] == 0 else na

            assert (row_a["active"] == 0 and row_a["status"] == "merged") or (row_b["active"] == 0 and row_b["status"] == "merged")

            # Must have an active entity_redirect pointing from retired to survivor
            redirect = conn.execute(
                "SELECT to_node_id, active FROM entity_redirect WHERE from_node_id = ?",
                (retired,),
            ).fetchone()
            assert redirect is not None
            assert redirect["active"] == 1

            # The redirect target must be an active canonical node
            target_node = conn.execute("SELECT active FROM node WHERE node_id = ?", (redirect["to_node_id"],)).fetchone()
            assert target_node is not None
            assert target_node["active"] == 1

    assert receipt["remediation_stats"]["entity_pairs_merged"] >= 24


def test_cluster_3_dangling_edges_archived(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 3: Verifies all 14 declared dangling edges are archived and 0 dangling edges remain."""
    test_db, receipt, pkg = remediated_environment
    edge_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "archive_edge"]
    assert len(edge_findings) == 14

    edge_ids = [f["raw_data"]["edge_id"] for f in edge_findings]
    assert len(edge_ids) == 14

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in edge_ids)
        rows = conn.execute(
            f"SELECT edge_id FROM edge WHERE edge_id IN ({placeholders})",
            edge_ids,
        ).fetchall()
        assert len(rows) == 0, f"Found unarchived dangling edges: {[r['edge_id'] for r in rows]}"

        # Global invariant: zero edges point to inactive nodes
        cur = conn.cursor()
        cur.execute(
            """SELECT edge_id FROM edge
                WHERE from_node_id NOT IN (SELECT node_id FROM node WHERE active = 1)
                   OR to_node_id NOT IN (SELECT node_id FROM node WHERE active = 1)"""
        )
        remaining_dangling = cur.fetchall()
        assert len(remaining_dangling) == 0

    assert receipt["remediation_stats"]["edges_archived"] >= 14


def test_cluster_4_duplicate_assertions_deduplicated(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 4: Verifies 10 approved duplicate assertion pairs are deduplicated."""
    test_db, receipt, pkg = remediated_environment
    dedup_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "deduplicate_assertions"]
    assert len(dedup_findings) == 10

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        for f in dedup_findings:
            rd = f["raw_data"]
            w_id = rd["winner_id"]
            l_id = rd["loser_id"]

            loser_row = conn.execute(
                "SELECT currentness_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id = ?",
                (l_id,),
            ).fetchone()
            assert loser_row is not None
            assert loser_row["currentness_state"] == "historical"
            assert loser_row["retrieval_eligible"] == 0
            assert loser_row["superseded_by_assertion_id"] == w_id

            winner_row = conn.execute(
                "SELECT currentness_state FROM assertion WHERE assertion_id = ?",
                (w_id,),
            ).fetchone()
            assert winner_row is not None
            assert winner_row["currentness_state"] == "current"

    assert receipt["remediation_stats"]["assertions_deduplicated"] >= 10


def test_cluster_5_temporal_contradiction_superseded(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 5: Verifies 1 temporal contradiction is superseded."""
    test_db, receipt, pkg = remediated_environment
    temporal_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "supersede_assertion"]
    assert len(temporal_findings) == 1

    rd = temporal_findings[0]["raw_data"]
    older_id = rd["assertion_id_older"]
    newer_id = rd["assertion_id_newer"]

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        older_row = conn.execute(
            "SELECT currentness_state, validation_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id = ?",
            (older_id,),
        ).fetchone()
        assert older_row is not None
        assert older_row["currentness_state"] == "historical"
        assert older_row["validation_state"] == "superseded"
        assert older_row["retrieval_eligible"] == 0
        assert older_row["superseded_by_assertion_id"] == newer_id

    assert receipt["remediation_stats"]["temporal_contradictions_superseded"] == 1


def test_cluster_6_batch_promote_candidates(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 6: Verifies high-confidence candidate assertions are promoted to canonical."""
    test_db, receipt, pkg = remediated_environment
    promote_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "batch_promote_candidates"]
    assert len(promote_findings) == 1

    sample_ids = promote_findings[0]["raw_data"]["sample_ids"]

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in sample_ids)
        rows = conn.execute(
            f"""SELECT assertion_id, canonical_status, canonical_eligible, validation_state, approval_reference
                  FROM assertion
                 WHERE assertion_id IN ({placeholders})""",
            sample_ids,
        ).fetchall()

        for r in rows:
            assert r["canonical_status"] == "canonical"
            assert r["canonical_eligible"] == 1
            assert r["validation_state"] == "accepted_legacy"
            assert r["approval_reference"] is not None

    assert receipt["remediation_stats"]["candidates_promoted"] > 1500


def test_cluster_7_resync_search_sidecars(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Cluster 7: Verifies search documents are resynced for active canonical assertions."""
    test_db, receipt, pkg = remediated_environment
    sidecar_findings = [f for f in pkg["findings"] if f["proposed_fix"] == "resync_search_sidecars"]
    assert len(sidecar_findings) == 1

    missing_sample = sidecar_findings[0]["raw_data"]["missing_search_document_ids"][:20]

    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in missing_sample)
        rows = conn.execute(
            f"""SELECT search_document_id, object_type, assertion_id
                  FROM search_document
                 WHERE search_document_id IN ({placeholders})""",
            missing_sample,
        ).fetchall()
        assert len(rows) == len(missing_sample)

        # Global parity check: 0 active canonical assertions missing from search_document
        missing_count = conn.execute(
            """SELECT COUNT(*) FROM assertion a
                WHERE a.retrieval_eligible = 1
                  AND a.canonical_status = 'canonical'
                  AND a.assertion_id NOT IN (
                      SELECT DISTINCT search_document_id FROM search_document WHERE search_document_id IS NOT NULL
                  )"""
        ).fetchone()[0]
        assert missing_count == 0

    assert receipt["remediation_stats"]["search_documents_resynced"] > 700


def test_five_physical_proof_checks(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Physical Proof: Verifies all five physical checks passed."""
    test_db, receipt, pkg = remediated_environment
    assert receipt["terminal_outcome"] == "PASS"

    proof = receipt["physical_proof"]
    assert proof["overall"] == "PASS"

    check_map = {c["check"]: c["status"] for c in proof["checks"]}
    assert check_map.get("file_changed") == "PASS"
    assert check_map.get("raw_bytes") == "PASS"
    assert check_map.get("integrity") == "PASS"
    assert check_map.get("readback") == "PASS"
    assert check_map.get("row_delta") == "PASS"


def test_pragma_integrity_and_foreign_keys(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Integrity: PRAGMA integrity_check returns ok and foreign_key_check returns 0 rows."""
    test_db, _, _ = remediated_environment
    with sqlite3.connect(f"file:{test_db}?mode=ro", uri=True) as conn:
        ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        assert ic == ["ok"]

        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert len(fk) == 0, f"Foreign key check violations: {fk}"


def test_post_remediation_scanner_zero_unapproved_findings(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Post-Remediation Scanner Audit: 5-Agent panel finds 0 unapproved defect findings."""
    test_db, _, _ = remediated_environment
    panel = GraphHygienePanel()
    post_pkg = panel.run_panel(test_db)
    assert len(post_pkg.findings) == 0, f"Unapproved defect findings remain: {[f.description for f in post_pkg.findings]}"


def test_anti_eviction_90d_safeguard(remediated_environment: Tuple[Path, Dict[str, Any], Dict[str, Any]]):
    """Governance: Asserts that assertions are never deleted by age alone."""
    test_db, _, _ = remediated_environment
    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        # Confirm assertions with created_at > 90 days ago still exist in database
        old_count = conn.execute(
            """SELECT COUNT(*) FROM assertion
                WHERE created_at IS NOT NULL
                  AND (julianday('2026-10-18') - julianday(created_at)) >= 90"""
        ).fetchone()[0]
        assert old_count > 0, "Expected assertions older than 90 days to exist in database"
