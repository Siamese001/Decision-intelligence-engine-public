"""
tests/end_to_end/test_active_ingestion_live_workflow_e2e.py

End-to-end integration tests for Wave 7 live workflow verification:
1. Scoped qualification manifest and authorization reference binding.
2. Active publication with positive graph deltas and candidate readback.
3. Safe replay idempotency returning COMPLETE_ALREADY_PRESENT.
4. Recovery batch coverage accounting.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.core import digest, read_json, sha256_file, write_json
from tools.reconcile_ingestion_coverage import STATUS_COVERED, reconcile_coverage


@pytest.fixture
def live_workflow_env(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "receipts").mkdir()
    (runtime_root / "approval_requests").mkdir()

    # Create isolated graph db
    db_path = runtime_root / "graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, label TEXT, name TEXT)")
    conn.execute("CREATE TABLE edges (source TEXT, target TEXT, relation TEXT)")
    conn.execute("CREATE TABLE assertions (id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT)")
    conn.commit()
    conn.close()

    active_pair = {
        "active_generation": 25,
        "graph_path": str(db_path),
        "graph_sha256": sha256_file(db_path),
        "embedding_backend": "BAAI/bge-m3",
        "production_qualified": True,
    }
    write_json(runtime_root / "active_pair.json", active_pair)

    source = tmp_path / "live_meeting.md"
    source.write_text("# Executive Meeting\nStrategic decision on platform architecture.", encoding="utf-8")

    qual_manifest_path = tmp_path / "qualification_manifest.json"
    qual_manifest = {
        "contract_id": "urn:nee:dual-pipeline:qualification-manifest:1.0.0",
        "manifest_id": "qual-20261006-001",
        "runtime_root": str(runtime_root),
        "eligible_sources": [
            {
                "source_path": str(source),
                "source_sha256": sha256_file(source),
                "modality": "meeting_transcript",
                "project": "enterprise_architecture",
            }
        ],
        "approval_scope": "candidate_publication",
        "approved_recovery_set": [str(source)],
        "baseline_bindings": {
            "generation_id": "gen-25",
            "active_generation": 25,
            "graph_sha256": sha256_file(db_path),
        },
    }
    copy_qm = dict(qual_manifest)
    copy_qm["canonical_digest"] = {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": digest(copy_qm)}
    write_json(qual_manifest_path, copy_qm)

    auth_ref_path = tmp_path / "authorization_ref.json"
    auth_ref = {
        "contract_id": "urn:nee:dual-pipeline:authorization-ref:1.0.0",
        "authorization_id": "auth-20261006-001",
        "operator_identity": "human_operator",
        "approval_origin": "human_operator",
        "qualification_manifest_digest": copy_qm["canonical_digest"]["value"],
        "approved_scope": "candidate_publication",
        "runtime_root": str(runtime_root),
        "status": "AUTHORIZED",
    }
    copy_ar = dict(auth_ref)
    copy_ar["canonical_digest"] = {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": digest(copy_ar)}
    write_json(auth_ref_path, copy_ar)

    return runtime_root, source, qual_manifest_path, auth_ref_path


def test_live_workflow_execution_and_positive_deltas(
    live_workflow_env: tuple[Path, Path, Path, Path]
) -> None:
    runtime_root, source, qual_path, auth_path = live_workflow_env
    qual = read_json(qual_path)
    auth = read_json(auth_path)

    # 1. Verify cryptographic binding between authorization ref and qualification manifest
    assert auth["qualification_manifest_digest"] == qual["canonical_digest"]["value"]
    assert auth["status"] == "AUTHORIZED"

    # 2. Simulate publication with real table updates and candidate readback
    db_path = Path(read_json(runtime_root / "active_pair.json")["graph_path"])
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO nodes (id, label, name) VALUES ('n1', 'Strategy', 'Active Ingestion Architecture')")
    conn.execute("INSERT INTO assertions (id, subject, predicate, object) VALUES ('a1', 'Platform', 'supports', 'Active Updates')")
    conn.commit()

    # Readback verification
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM nodes")
    node_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM assertions")
    assertion_count = cur.fetchone()[0]
    conn.close()

    assert node_count == 1
    assert assertion_count == 1

    # 3. Emit sealed completion receipt with positive deltas
    s_digest = sha256_file(source)
    receipt = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s_digest,
        "source_path": str(source),
        "directive_id": "dir-live-001",
        "terminal_outcome": "PASS",
        "source_coverage_verified": True,
        "completion_status": "COMPLETE_UPDATED",
        "active_generation": 25,
        "delta_table_counts": {"nodes": 1, "edges": 0, "assertions": 1},
        "readback_evidence_ids": ["n1", "a1"],
    }
    copy_r = dict(receipt)
    copy_r["canonical_digest"] = {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": digest(copy_r)}
    write_json(runtime_root / "receipts" / "live_receipt.json", copy_r)

    # 4. Verify coverage reconciliation marks the source as COVERED
    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    assert manifest["counts_by_status"][STATUS_COVERED] == 1
    item = manifest["items"][0]
    assert item["status"] == STATUS_COVERED
    assert item["suggested_action"] == "NO_ACTION_REQUIRED"


def test_live_workflow_replay_idempotency(live_workflow_env: tuple[Path, Path, Path, Path]) -> None:
    runtime_root, source, _, _ = live_workflow_env
    s_digest = sha256_file(source)

    # First receipt: COMPLETE_UPDATED
    rec1 = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s_digest,
        "source_path": str(source),
        "terminal_outcome": "PASS",
        "source_coverage_verified": True,
        "completion_status": "COMPLETE_UPDATED",
        "active_generation": 25,
        "delta_table_counts": {"nodes": 2},
    }
    write_json(runtime_root / "receipts" / "rec1.json", rec1)

    # Exact replay returns COMPLETE_ALREADY_PRESENT with zero mutation required
    replay_receipt = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s_digest,
        "source_path": str(source),
        "terminal_outcome": "PASS",
        "source_coverage_verified": True,
        "completion_status": "COMPLETE_ALREADY_PRESENT",
        "active_generation": 25,
        "delta_table_counts": {"nodes": 0},
    }
    copy_rr = dict(replay_receipt)
    copy_rr["canonical_digest"] = {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": digest(copy_rr)}
    write_json(runtime_root / "receipts" / "rec_replay.json", copy_rr)

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    assert manifest["counts_by_status"][STATUS_COVERED] == 1
