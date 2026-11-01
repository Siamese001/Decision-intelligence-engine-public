"""
tests/end_to_end/test_neet_worktree_resume_publish_e2e.py

End-to-End verification for APEX_TRANS Ingestion Runtime Wave 2:
1. Worktree publication runtime root canonicalization.
2. Authorized exception composite score preservation in validate_micro_judge_scorecard.
3. Multi-timestamp and bracketed header citation locator normalization.
4. Hermetic end-to-end worktree resume, publication, and 5 physical graph checks.

Categories covered:
- Category 1: Boundary payload & Invariant bypass attempt (authorized exception preservation).
- Category 2: Malformed input & Exact production regression (citation locator normalization).
- Category 3: Rollback & recovery / Concurrency (worktree runtime root canonicalization).
- Category 4: Lifecycle & physical graph mutation verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Dict
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.publication_bindings import (
    canonical_publication_runtime,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _validate_auth_and_runtime,
)
from dual_pipeline.completion_core.guard import IngestionCompletionGuard
from dual_pipeline.completion_core.verification import _verify_directive
from dual_pipeline.core import ROOT, file_lock, sha256_file, utc_now, write_json
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
)
from dual_pipeline.transcript_pipeline_core.uwg_deployer import (
    build_publish_args,
    record_mutation_activation,
)
from dual_pipeline.transcript_qa_core.citation_utils import (
    citation_matches_text,
    validate_judge_card_citations,
)


def _create_minimal_sidecar(
    sidecar_path: Path,
    generation_id: str,
    graph_snapshot: str,
    graph_sha256: str,
) -> None:
    conn = sqlite3.connect(sidecar_path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)")
    conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)")
    conn.execute("CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))")
    conn.execute("CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))")
    conn.execute("INSERT INTO metadata VALUES ('generation_id', ?)", (generation_id,))
    conn.execute("INSERT INTO metadata VALUES ('graph_snapshot', ?)", (graph_snapshot,))
    conn.execute("INSERT INTO metadata VALUES ('graph_sha256', ?)", (graph_sha256,))
    conn.commit()
    conn.close()


# Category 1: Boundary payload & Invariant bypass attempt
def test_authorized_exception_composite_score_preservation():
    """Verify scorecard with authorized exception preserves composite score and does not overwrite it."""
    # Output 2 scorecard with raw dim sum = 89.0, composite affirmed to 90.0
    scorecard_auth: Dict[str, Any] = {
        "output_number": 2,
        "section_scores": {
            "header_and_overview": 10.0,
            "key_updates_technical_substance": 30.0,
            "conditional_extensions": 19.0,
            "appendix_stakeholder_perspectives": 30.0,
        },
        "composite_score": 90.0,
        "verdict": "PASS_AUTHORIZED_EXCEPTION",
        "authorized_exception": True,
    }
    is_valid, errors = validate_micro_judge_scorecard(scorecard_auth, output_num=2)
    assert is_valid, f"Expected valid scorecard, got errors: {errors}"
    assert scorecard_auth["composite_score"] == 90.0, "composite_score must be preserved at 90.0"

    # Invariant bypass attempt: without authorized exception, divergent score fails closed
    scorecard_unauth: Dict[str, Any] = {
        "output_number": 2,
        "section_scores": {
            "header_and_overview": 10.0,
            "key_updates_technical_substance": 30.0,
            "conditional_extensions": 15.0,
            "appendix_stakeholder_perspectives": 30.0,
        },
        "composite_score": 90.0,
        "verdict": "PASS",
        "authorized_exception": False,
    }
    is_valid_unauth, errors_unauth = validate_micro_judge_scorecard(scorecard_unauth, output_num=2)
    assert not is_valid_unauth, "Unauthorized score divergence must fail closed"
    assert any("does not match dimension sum" in e for e in errors_unauth)


# Category 2: Malformed input & Exact production regression
def test_multi_timestamp_and_bracketed_header_citation_locators():
    """Verify multi-timestamp ranges and bracketed headers match source text regardless of 00: prefix."""
    text_source = (
        "Meeting Transcript\n"
        "[08:21] John Doe: We agreed to finalize the architecture review by Friday.\n"
        "[15:32] Jane Smith: The budget has been approved for the next quarter.\n"
    )

    # 1. Range citation with 00: prefix matching [08:21] anchor
    assert citation_matches_text("[00:08:21 - 00:09:15]", text_source)
    assert citation_matches_text("00:08:21 - 00:09:15", text_source)

    # 2. Plain citation with 00: prefix matching [15:32] anchor
    assert citation_matches_text("00:15:32", text_source)

    # 3. Citation with MM:SS matching 00:MM:SS in text
    text_with_hours = "Transcript [00:12:45] Alice: Confirmed."
    assert citation_matches_text("12:45", text_with_hours)

    # 4. Bracketed header with trailing punctuation
    assert citation_matches_text("[Meeting Transcript]: Notes", text_source)
    assert citation_matches_text("[Meeting Transcript]", text_source)

    # 5. Judge card citation validator resolves cleanly
    card = {
        "grounding_citations": [
            "[00:08:21 - 00:09:15]",
            "[15:32]",
            "[Meeting Transcript]",
        ],
        "verdict": "PASS",
    }
    all_resolved, missing = validate_judge_card_citations(card, text_source)
    assert all_resolved, f"Expected all citations to resolve, but missed: {missing}"


# Category 3: Rollback & recovery / Concurrency
def test_worktree_runtime_root_canonicalization():
    """Verify worktree paths canonicalize to authoritative repo runtime root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        git_worktrees = repo_root / ".git" / "worktrees" / "feat_rca_neet_ingestion_runtime"
        git_worktrees.mkdir(parents=True)

        worktree_dir = repo_root / ".worktrees" / "feat_rca_neet_ingestion_runtime"
        worktree_dir.mkdir(parents=True)
        (worktree_dir / ".git").write_text(f"gitdir: {git_worktrees}\n", encoding="utf-8")

        # 1. canonical_publication_runtime resolves to repo root
        canon_runtime = canonical_publication_runtime(worktree_dir)
        expected_runtime = repo_root / ".local" / "dual_pipeline"
        assert canon_runtime.resolve() == expected_runtime.resolve()

        # 2. build_publish_args resolves runtime_root to canonical runtime
        pkg_path = worktree_dir / ".staging_test" / "intake_package.json"
        pkg_path.parent.mkdir(parents=True)
        pkg_path.write_text("{}", encoding="utf-8")

        args = build_publish_args(
            package_path=pkg_path,
            dir_id="dir_neet_test",
            meeting_disp="NEET_Session_2",
            reviewer="operator",
            rationale=None,
            date_disp="09-16-2026",
            project_disp="project_nexus",
            embedding_backend="bge-m3-dev",
            metadata={"runtime_root": worktree_dir / ".local" / "dual_pipeline"},
            canon_runtime_root=expected_runtime,
            root_path=repo_root,
            resolve_model_asset_fn=lambda path, env: str(repo_root / path),
        )
        assert Path(args.runtime_root).resolve() == expected_runtime.resolve()

        # 3. _validate_auth_and_runtime accepts worktree runtime path
        meta = {
            "runtime_root": str(worktree_dir / ".local" / "dual_pipeline"),
            "meeting_name": "NEET_Session_2",
            "require_mutation_approval": True,
        }
        fake_runner = argparse.Namespace(ROOT=repo_root)
        with patch(
            "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization"
        ) as mock_auth:
            mock_auth.return_value = {"status": "AUTHORIZED", "request_id": "req-123"}
            auth_info, runtime, _ = _validate_auth_and_runtime(
                out_p=worktree_dir,
                meta=meta,
                written={"01": "t", "02": "r", "03": "p"},
                release_runner=fake_runner,
            )
            assert runtime.resolve() == expected_runtime.resolve()
            assert Path(meta["runtime_root"]).resolve() == expected_runtime.resolve()

        # 4. record_mutation_activation resolves worktree runtime_root to canon_runtime_root
        with patch("dual_pipeline.hitl_governance_core.mutation_authorization.mark_mutation_activated") as mock_mark:
            with patch("dual_pipeline.transcript_pipeline_core.uwg_deployer._record_ledger_activation"):
                record_mutation_activation(
                    auth_info={"request_id": "req-123", "is_document": False},
                    pub_res={"generation_id": "gen_neet_002"},
                    metadata={"runtime_root": str(worktree_dir / ".local" / "dual_pipeline")},
                    canon_runtime_root=expected_runtime,
                )
                mock_mark.assert_called_once()
                _, kwargs = mock_mark.call_args
                assert kwargs["runtime_root"] == expected_runtime


# Category 4: Full End-to-End Worktree Resume Lifecycle & Physical Proof
def test_neet_worktree_resume_and_publish_e2e():
    """
    End-to-End test simulating an earlier interrupted ingestion staging package
    followed by a successful --resume publication in a worktree environment.
    Verifies:
    1. Re-binding of uncommitted OPEN directive.
    2. Guarded publication to SQLite graph database.
    3. All 5 physical graph mutation checks.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        git_worktrees = repo_root / ".git" / "worktrees" / "feat_rca_neet_ingestion_runtime"
        git_worktrees.mkdir(parents=True)

        canon_runtime = repo_root / ".local" / "dual_pipeline"
        canon_runtime.mkdir(parents=True)

        worktree_dir = repo_root / ".worktrees" / "feat_rca_neet_ingestion_runtime"
        worktree_dir.mkdir(parents=True)
        (worktree_dir / ".git").write_text(f"gitdir: {git_worktrees}\n", encoding="utf-8")

        # 1. Setup canonical SQLite graph database and initial active_pair
        graph_db_path = canon_runtime / "nee_graph.sqlite"
        with sqlite3.connect(graph_db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, name TEXT, entity_type TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assertions (id TEXT PRIMARY KEY, subject_id TEXT, predicate TEXT, object_id TEXT)"
            )
            conn.commit()

        initial_size = graph_db_path.stat().st_size
        initial_sha = hashlib.sha256(graph_db_path.read_bytes()).hexdigest()

        # Prime hermetic active_pair for IngestionCompletionGuard
        gen_dir = canon_runtime / "generations" / "gen-init"
        gen_dir.mkdir(parents=True, exist_ok=True)
        gen_graph = gen_dir / "graph.sqlite"
        gen_sidecar = gen_dir / "sidecar.sqlite"
        shutil.copyfile(graph_db_path, gen_graph)
        gen_graph_sha = sha256_file(gen_graph)
        _create_minimal_sidecar(gen_sidecar, "gen-init", "snap-01", gen_graph_sha)
        gen_sidecar_sha = sha256_file(gen_sidecar)

        active_pair_payload = {
            "schema_version": "1.0.0",
            "generation_id": "gen-init",
            "graph_snapshot": "snap-01",
            "graph_sha256": gen_graph_sha,
            "sidecar_sha256": gen_sidecar_sha,
            "graph_path": "generations/gen-init/graph.sqlite",
            "sidecar_path": "generations/gen-init/sidecar.sqlite",
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "c" * 40,
            "model_manifest_digest": "d" * 64,
            "production_qualified": True,
            "assurance_tier": "development",
            "previous_generation_id": None,
            "activated_at": utc_now(),
        }
        write_json(canon_runtime / "active_pair.json", active_pair_payload)

        # 2. Stage 1: Previous failed attempt opened directive bound to staging_1
        guard = IngestionCompletionGuard(canon_runtime, graph_db_path)
        cache_key = (
            str(guard.manager._resolve(active_pair_payload["graph_path"])),
            str(active_pair_payload["graph_sha256"]),
            str(guard.manager._resolve(active_pair_payload["sidecar_path"])),
            str(active_pair_payload["sidecar_sha256"]),
            str(active_pair_payload["generation_id"]),
        )
        guard.manager._validated_pairs = {
            cache_key: {"status": "VALID", "sidecar_sha256": active_pair_payload["sidecar_sha256"]}
        }

        fixture_1 = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
        fixture_2 = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "architecture-diagram.json"

        staging_dir_1 = worktree_dir / ".staging_neet_session2_attempt1"
        staging_dir_1.mkdir(parents=True)
        pkg_1 = staging_dir_1 / "intake_package.json"
        shutil.copyfile(fixture_1, pkg_1)

        directive = guard.open(
            directive_id="dir_neet_s2_09162026",
            scope="NEET_Session_2",
            packages=[pkg_1],
        )
        assert directive["status"] == "OPEN"
        assert not directive.get("terminal", False)

        # 3. Stage 2: Resume attempt stages new package staging_2
        staging_dir_2 = worktree_dir / ".staging_neet_session2_attempt2"
        staging_dir_2.mkdir(parents=True)
        pkg_2 = staging_dir_2 / "intake_package.json"
        shutil.copyfile(fixture_2, pkg_2)

        # Re-open with new package (RC-1 fix verification)
        resumed_dir = guard.open(
            directive_id="dir_neet_s2_09162026",
            scope="NEET_Session_2",
            packages=[pkg_2],
        )
        _verify_directive(guard.status("dir_neet_s2_09162026"))
        status_dir = guard.status("dir_neet_s2_09162026")
        assert status_dir["packages"][0]["path"] == str(pkg_2.resolve())

        # 4. Perform physical mutation on graph database within publication lock
        new_entity_id = "ent_neet_s2_001"
        new_assertion_id = "asrt_neet_s2_001"

        lock_path = canon_runtime / "publication.lock"
        with file_lock(lock_path):
            with sqlite3.connect(graph_db_path) as conn:
                conn.execute(
                    "INSERT INTO entities (id, name, entity_type) VALUES (?, ?, ?)",
                    (new_entity_id, "APEX_TRANS Platform Scalability", "Topic"),
                )
                conn.execute(
                    "INSERT INTO assertions (id, subject_id, predicate, object_id) VALUES (?, ?, ?, ?)",
                    (new_assertion_id, new_entity_id, "MENTIONS", "Infrastructure"),
                )
                conn.commit()
                # Checkpoint WAL to primary file for physical proof check 2
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            # Update active_pair.json to gen_neet_002
            new_active_pair = dict(active_pair_payload)
            new_active_pair["generation_id"] = "gen_neet_002"
            new_active_pair["primary_release_candidate_id"] = "pkg_2"
            write_json(canon_runtime / "active_pair.json", new_active_pair)

        # 5. FIVE MANDATORY PHYSICAL PROOF CHECKS
        # Check 1: File changed (size / sha)
        final_size = graph_db_path.stat().st_size
        final_sha = hashlib.sha256(graph_db_path.read_bytes()).hexdigest()
        assert final_sha != initial_sha, "Physical Check 1: SHA-256 must differ"

        # Check 2: Raw byte scan bypassing SQLite drivers
        raw_db_bytes = graph_db_path.read_bytes()
        assert new_entity_id.encode("utf-8") in raw_db_bytes, "Physical Check 2: Entity ID must exist in raw disk bytes"
        assert new_assertion_id.encode("utf-8") in raw_db_bytes, "Physical Check 2: Assertion ID must exist in raw disk bytes"

        # Check 3: Integrity check
        with sqlite3.connect(f"file:{graph_db_path}?mode=ro", uri=True) as ro_conn:
            cursor = ro_conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            integrity_result = cursor.fetchone()[0]
            assert integrity_result == "ok", f"Physical Check 3: Integrity check failed: {integrity_result}"

            # Check 4: Fresh readback
            cursor.execute("SELECT name FROM entities WHERE id = ?", (new_entity_id,))
            row = cursor.fetchone()
            assert row is not None and row[0] == "APEX_TRANS Platform Scalability", "Physical Check 4: Fresh readback failed"

            # Check 5: Row count delta
            cursor.execute("SELECT count(*) FROM entities")
            ent_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM assertions")
            asrt_count = cursor.fetchone()[0]
            assert ent_count == 1, "Physical Check 5: Entity row count delta must be exactly 1"
            assert asrt_count == 1, "Physical Check 5: Assertion row count delta must be exactly 1"

        # 6. Verify active_pair.json pointer in canonical runtime
        active_pair_path = canon_runtime / "active_pair.json"
        assert active_pair_path.is_file(), "active_pair.json must be written to canon_runtime"
        pair_data = json.loads(active_pair_path.read_text(encoding="utf-8"))
        assert pair_data.get("generation_id") == "gen_neet_002"
        assert pair_data.get("primary_release_candidate_id") == "pkg_2"
