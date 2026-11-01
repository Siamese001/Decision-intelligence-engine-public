"""Adversarial and property-based tests for Wave 1 E2E Critical Functionality Handoffs."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.document_resume import FreshDocumentStages, open_stage_checkpoint
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import _guarded_graph_publication
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.transcript_resume import (
    _ACTIVE_TRANSCRIPT_CHECKPOINT,
    record_transcript_failure,
    run_synthesis_with_transcript_resume,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from tools.tier2_dedup_core.sow_duplicate_retirement import _ensure_missing_dense_vectors


def test_adversarial_unverified_exit_gates_checkpoint_rejected_on_resume(tmp_path: Path) -> None:
    """Category: Rollback & recovery / Invariant bypass — rejects checkpoint with exit_gates_verified=False."""
    ckpt_path = tmp_path / "poisoned_ckpt.json"
    ckpt = PipelineCheckpointManager(ckpt_path, require_binding=False)
    syn = SynthesisResult(output_1="Speaker 1: Hi", output_2="Recap", output_3='{"nodes": []}')
    ev = QAEvaluationResult(
        passed=True,
        qa_report={"overall_verdict": "PASS"},
        judge_report={},
        final_decision={"status": "PASS"},
        authoritative_write_authorized=True,
    )
    meta = {
        "_transcript_checkpoint_path": str(ckpt_path),
        "run_id": "run_adv",
        "project": "nexus",
    }

    with (
        patch("dual_pipeline.canonical_pipeline.transcript_resume._build_binding_for_call") as mock_bind,
        patch("dual_pipeline.canonical_pipeline.transcript_resume.verify_attempt_binding"),
    ):
        mock_bind.return_value = {"binding_sha256": "b1", "reusable": True, "reuse_blockers": []}
        ckpt.bind({"binding_sha256": "b1", "reusable": True}, force=True)
        ckpt.save_stage(
            "VALIDATED_SYNTHESIS",
            {
                "attempt": 1,
                "synthesis": {"output_1": "poisoned", "output_2": "poisoned", "output_3": "{}"},
                "evaluation": {
                    "passed": True,
                    "qa_report": {},
                    "judge_report": {},
                    "final_decision": {"status": "PASS"},
                    "authoritative_write_authorized": True,
                },
                "run_id": "run_old",
                "binding_sha256": "b1",
                "exit_gates_verified": False,
            },
        )

        called_fresh = {"count": 0}

        def _fresh_synth(**_):
            called_fresh["count"] += 1
            return 2, syn, ev

        attempt, res_syn, _ = run_synthesis_with_transcript_resume(
            synthesize=_fresh_synth,
            source_text="Speaker 1: Hi",
            metadata=meta,
            provider=SimpleNamespace(),
            provider_type="offline",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
        )

    assert called_fresh["count"] == 1
    assert attempt == 2
    assert res_syn.output_1 == "Speaker 1: Hi"
    assert meta["transcript_resume"]["reused_synthesis"] is False


def test_adversarial_staging_approval_receipt_resolved_before_unpromoted_target(tmp_path: Path) -> None:
    """Category: Exact production regression — _guarded_graph_publication resolves receipt from staging_dir."""
    staging_dir = tmp_path / ".staging_meeting_1234"
    staging_dir.mkdir()
    target_out_p = tmp_path / "promoted_meeting"
    # Receipt exists ONLY in staging_dir, not yet in target_out_p
    (staging_dir / "mutation_approval_receipt.json").write_text(
        json.dumps({"status": "APPROVED", "reviewer": "operator_a"}),
        encoding="utf-8",
    )
    intake_pkg = staging_dir / "intake_package.json"
    intake_pkg.write_text("{}", encoding="utf-8")

    observed_auth_dirs: list[Path] = []

    def _fake_validate_auth(auth_dir: Path, meta: dict, written: dict, rr: object):
        observed_auth_dirs.append(auth_dir)
        return {"reviewer": "operator_a"}, tmp_path, {"status": "EARLY_STOP_TEST"}

    with patch(
        "dual_pipeline.canonical_pipeline.publish_helper._validate_auth_and_runtime",
        side_effect=_fake_validate_auth,
    ):
        res = _guarded_graph_publication(
            intake_pkg_path=intake_pkg,
            metadata={"target_output_dir": str(target_out_p), "project": "nexus"},
            written={},
            directive_id=None,
            reviewer="operator_a",
            rationale="test",
            embedding_backend="bge-m3",
        )

    assert res == {"status": "EARLY_STOP_TEST"}
    assert observed_auth_dirs == [staging_dir]


@given(
    missing_count=st.integers(min_value=0, max_value=12),
    stage_payload=st.dictionaries(st.text(min_size=1, max_size=8), st.text(max_size=15), max_size=5),
)
@settings(max_examples=15, deadline=None)
def test_property_sidecar_backfill_and_fresh_document_stages(
    missing_count: int, stage_payload: dict[str, str]
) -> None:
    """Category: Boundary payload & Malformed/arbitrary state — property verification of tri-vector backfill and stage cache."""
    stages = FreshDocumentStages(enable_stage_cache=True)
    stages.save_stage("C0_PA", stage_payload)
    assert stages.has_stage("C0_PA") is True
    assert stages.get_stage("C0_PA") == stage_payload
    stages.clear()
    assert stages.has_stage("C0_PA") is False

    with tempfile.TemporaryDirectory() as td:
        db_p = Path(td) / "sidecar.sqlite"
        with sqlite3.connect(db_p) as conn:
            conn.executescript(SIDECAR_SCHEMA)
            conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', '1024')")
            rids = [f"r-{idx}" for idx in range(missing_count)]
            for rid in rids:
                conn.execute(
                    "INSERT INTO retrieval_record VALUES (?, 'approved', 'assertion', 'CLIENT_SAFE', 'h', 'assertion', 'approved', 'direct_source', 'factual_answer', '{}')",
                    (rid,),
                )
            _ensure_missing_dense_vectors(conn, rids)
            for query_sql in (
                "SELECT COUNT(DISTINCT record_id) FROM dense_vector",
                "SELECT COUNT(DISTINCT record_id) FROM sparse_weight",
                "SELECT COUNT(DISTINCT record_id) FROM colbert_token",
            ):
                cnt = conn.execute(query_sql).fetchone()[0]
                assert cnt == missing_count


def test_adversarial_demo_forwards_l1_control_policy_and_rejects_missing_policy_in_bounded_mode(
    tmp_path: Path,
) -> None:
    """Category: Invariant bypass attempt — verifies _handle_demo forwards l1_control_policy into QueryEngine."""
    from dual_pipeline.cli.engine import _handle_demo
    from dual_pipeline.cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "demo",
            "--package",
            str(tmp_path / "pkg.json"),
            "--runtime-root",
            str(tmp_path / "rt"),
            "--graph",
            str(tmp_path / "rt" / "graph.sqlite"),
            "--output-dir",
            str(tmp_path / "out"),
            "--l1-controller-mode",
            "bounded",
        ]
    )

    captured_qe_kwargs: dict = {}

    class _FakeQueryEngine:
        def __init__(self, *a, **kw):
            captured_qe_kwargs.update(kw)

        def ask(self, *a, **kw):
            return {"status": "OK"}

    fake_mgr = MagicMock()
    fake_mgr.bootstrap.return_value = {"generation_id": "gen-0", "graph_path": "g.sqlite", "sidecar_path": "s.sqlite"}
    fake_mgr.prepare.return_value = {"receipt_id": "rec-1", "next_pair": {"generation_id": "gen-1"}}
    fake_mgr.activate.return_value = {"status": "ACTIVE"}
    fake_intake = MagicMock()
    fake_intake.ingest.return_value = {"run_id": "run-demo-1"}

    with (
        patch("dual_pipeline.cli.engine._backend", return_value=SimpleNamespace(backend_name="bge-m3", production_qualified=False)),
        patch("dual_pipeline.cli.engine._authorization", return_value=None),
        patch("dual_pipeline.cli.engine._bind_model_authorization"),
        patch("dual_pipeline.release.ReleaseManager", return_value=fake_mgr),
        patch("dual_pipeline.intake_engine.IntakeEngine", return_value=fake_intake),
        patch("dual_pipeline.index.HybridRetriever", return_value=MagicMock()),
        patch("dual_pipeline.query.QueryEngine", _FakeQueryEngine),
    ):
        with pytest.raises(ValueError, match="L1_BOUNDED_POLICY_REQUIRED"):
            _handle_demo(args)

        sentinel_policy = SimpleNamespace(mode="bounded")
        with patch("dual_pipeline.cli.engine._l1_control_policy_from_args", return_value=sentinel_policy):
            res = _handle_demo(args)

    assert res["query"] == {"status": "OK"}
    assert captured_qe_kwargs.get("l1_controller_mode") == "bounded"
    assert captured_qe_kwargs.get("l1_control_policy") is sentinel_policy

