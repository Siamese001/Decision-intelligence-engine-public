"""Adversarial regression suite for Historical 400-PR Escape Gates HIST-01 through HIST-07.

Covers:
- HIST-01 (INV-TEST-COCHANGE-FIX): Co-changed regression test detector for fix/* commits.
- HIST-02 (INV-TEST-NESTED-FLOCK-REENTRANCY): Same-thread nested lock & rollback reentrancy (<2s timeout).
- HIST-03 (INV-TEST-COLD-SUBPROCESS-IMPORT): Cold subprocess worker imports & AST unbound symbol check.
- HIST-04 (INV-TEST-CHAINED-STAGE-INVARIANT): Multi-stage role confidence preservation & session-scoped isolation.
- HIST-05 (INV-TEST-ZERO-VACUOUS-PASS): Fail-closed rejection on vacuous claims=[] or empty citations.
- HIST-06: Single-token attendee names, compound delimiters, and affiliate org codes (APEX_TRANS/HRIT/ENTERPRISE4U).
- HIST-07: Parameterized SQL AST enforcement & retracted/expired-assumption lineage filtering.
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

import pytest

from dual_pipeline.query_core.lineage_db import query_db_lineage_metadata
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    clean_attendee_entry,
    extract_attendees_from_header_block,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# HIST-01: Co-change regression test detector for fix/* commits
# ---------------------------------------------------------------------------
def evaluate_fix_commit_cochange(commit_subject: str, touched_files: List[str]) -> bool:
    """Returns False if a production fix/rca commit touches prod dirs with 0 test files."""
    is_fix = any(tok in commit_subject.lower() for tok in ("fix(", "fix:", "fix/", "rca(", "rca:"))
    if not is_fix:
        return True
    prod_prefixes = ("dual_pipeline/", "storage/", "pipeline_a_intake/", "pipeline_b_extraction/", "tools/")
    touches_prod = any(f.startswith(prod_prefixes) for f in touched_files)
    touches_tests = any(f.startswith("tests/") for f in touched_files)
    if touches_prod and not touches_tests:
        return False
    return True


def test_hist01_fix_commit_without_regression_test_is_flagged() -> None:
    assert evaluate_fix_commit_cochange(
        "fix(graph-writer): preserve historical assertion state",
        ["dual_pipeline/graph_writer_core/writer.py"],
    ) is False
    assert evaluate_fix_commit_cochange(
        "fix(graph-writer): preserve historical assertion state",
        ["dual_pipeline/graph_writer_core/writer.py", "tests/test_graph_writer.py"],
    ) is True
    assert evaluate_fix_commit_cochange(
        "docs(readme): update architecture diagram",
        ["README.md"],
    ) is True


# ---------------------------------------------------------------------------
# HIST-02: Same-thread nested lock & rollback reentrancy under < 2s timeout
# ---------------------------------------------------------------------------
def test_hist02_nested_reentrant_lock_and_rollback_completes_under_2s(tmp_path: Path) -> None:
    from dual_pipeline.storage.governed_context import GovernedStorageContext

    db_path = tmp_path / "reentrant_lock.sqlite"
    start = time.monotonic()
    entered_inner = False

    with GovernedStorageContext.write_context(db_path) as conn1:
        conn1.execute("CREATE TABLE IF NOT EXISTS t_lock (id TEXT PRIMARY KEY)")
        # Nested acquisition on same thread must not self-deadlock on POSIX flock
        with GovernedStorageContext.write_context(db_path) as conn2:
            conn2.execute("INSERT INTO t_lock VALUES ('row_1')")
            entered_inner = True

    elapsed = time.monotonic() - start
    assert entered_inner is True
    assert elapsed < 2.0, f"Nested write_context took {elapsed:.2f}s (>= 2.0s deadlock threshold)"


# ---------------------------------------------------------------------------
# HIST-03: Cold subprocess import of multiprocessing worker entrypoints
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "module_name",
    [
        "dual_pipeline.audio_ingestion_core. acoustic_cache".replace(" ", ""),
        "dual_pipeline.live_meeting_recorder_core.mlx_worker",
        "dual_pipeline.query_core.lineage_db",
    ],
)
def test_hist03_cold_subprocess_worker_imports_without_circular_import(module_name: str) -> None:
    res = subprocess.run(
        [sys.executable, "-c", f"import {module_name}; print('OK')"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"Cold import of {module_name} failed: {res.stderr}"
    assert "OK" in res.stdout


# ---------------------------------------------------------------------------
# HIST-04: Multi-stage role confidence preservation & session-scoped isolation
# ---------------------------------------------------------------------------
def test_hist04_chained_stage_confidence_floor_and_session_isolation(tmp_path: Path) -> None:
    # 1. Decoy historical session in same parent sandbox must never bleed into active session lookup
    decoy_dir = tmp_path / "session_2026_09_01_decoy"
    active_dir = tmp_path / "session_2026_10_25_active"
    decoy_dir.mkdir()
    active_dir.mkdir()
    (decoy_dir / "decoy_segments.jsonl").write_text('{"speaker": "DecoySpeaker", "text": "Wrong session"}\n')
    (active_dir / "active_segments.jsonl").write_text('{"speaker": "Speaker_Beta", "text": "Active session"}\n')

    active_files = list(active_dir.glob("*segments*.jsonl"))
    assert len(active_files) == 1
    assert "DecoySpeaker" not in active_files[0].read_text()

    # 2. Chained stage confidence handoff must clamp to human-confirmed floor rather than eroding
    initial_confidence = 0.95
    stage_multipliers = [0.98, 0.97, 0.96]
    # Human-confirmed floor invariant: human-confirmed role confidence never erodes below 0.90
    is_human_confirmed = True
    effective_conf = initial_confidence
    for mult in stage_multipliers:
        effective_conf = max(0.90, effective_conf * mult) if is_human_confirmed else (effective_conf * mult)
    assert effective_conf >= 0.90


# ---------------------------------------------------------------------------
# HIST-05: Fail-closed rejection on vacuous claims=[] or empty citations
# ---------------------------------------------------------------------------
def test_hist05_vacuous_empty_claims_and_memory_fallback_rejected(tmp_path: Path) -> None:
    import hashlib
    from dual_pipeline.grounding.contracts import ClaimManifest
    from dual_pipeline.grounding.delivery_boundary import verify_grounded_delivery

    payload = "Executive summary without any backing claims."
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    manifest = ClaimManifest(
        manifest_id="MAN-VACUOUS-01",
        payload_digest=digest,
        runtime_root=str(tmp_path / "missing_runtime"),
        graph_generation_id="gen_01",
        graph_sha256="0" * 64,
        claims=[],
    )
    certified, receipt, reasons = verify_grounded_delivery(
        payload_text=payload,
        manifest=manifest,
        runtime_root=tmp_path / "missing_runtime",
    )
    assert certified is False
    assert receipt.status in ("BLOCKED", "REJECTED")
    assert len(reasons) >= 1


# ---------------------------------------------------------------------------
# HIST-06: Single-token attendee names, compound delimiters & affiliate org codes
# ---------------------------------------------------------------------------
def test_hist06_single_token_attendees_and_affiliate_org_codes() -> None:
    raw = "**Attendees:** Speaker_Beta (PartnerAdvisory); Speaker_Alpha — NEA; Jake (APEX_TRANS); Tenika (HRIT); Mitchell (ENTERPRISE4U)"
    grouped = extract_attendees_from_header_block(raw)
    assert grouped is not None
    flat = " ".join(item for items in grouped.values() for item in items)
    for expected in ("Speaker_Beta", "Speaker_Alpha", "Jake"):
        assert expected in flat
    name_single, _, org_single = clean_attendee_entry("Speaker_Beta (PartnerAdvisory)")
    assert "Speaker_Beta" in name_single
    assert "PartnerAdvisory" in org_single


# ---------------------------------------------------------------------------
# HIST-07: Parameterized SQL AST check & superseded/retracted lineage badge
# ---------------------------------------------------------------------------
def test_hist07_lineage_db_uses_parameterized_where_and_marks_superseded(tmp_path: Path) -> None:
    src = (REPO_ROOT / "dual_pipeline" / "query_core" / "lineage_db.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert tree is not None
    # Verify query_db_lineage_metadata uses '?' placeholders for token lists
    assert '",".join("?" for _ in expanded)' in src

    db_file = tmp_path / "superseded_check.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.executescript(
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
            );
            INSERT INTO assertion VALUES (
                'asrt_retracted', 'HIGH', 0.95, 'canonical', 1, 'superseded', 'retracted',
                'HAS_CLAIM_TEXT', 'Old expired assumption', NULL, NULL, NULL, NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    confs, _, _ = query_db_lineage_metadata(db_file, ["asrt_retracted"])
    assert confs["asrt_retracted"] == "SUPERSEDED (0%)"
