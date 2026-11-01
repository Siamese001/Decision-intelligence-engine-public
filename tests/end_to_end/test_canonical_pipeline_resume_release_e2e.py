"""End-to-end tests for Wave 2 Canonical Pipeline Resume, Release Policy & Exit QA (CAT-4)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _is_unverified_partial_dir,
    _memoize_output_1,
    _memoize_output_2,
)
from dual_pipeline.canonical_pipeline.pipeline import _resolve_evals_mutation_intent
from dual_pipeline.canonical_pipeline.publish_helper import (
    _pre_persist_role_resolutions_if_needed,
    verify_exit_qa,
)
from dual_pipeline.release_policy import _check_out3_threshold, validate_micro_judge_scorecard


class _DummyStateMachine:
    def __init__(self) -> None:
        self.frozen: dict[str, str] = {}

    def is_frozen(self, key: str) -> bool:
        return key in self.frozen

    def freeze(self, key: str, content: str, **_kwargs) -> None:
        self.frozen[key] = content


class _DummyTracker:
    def complete_stage(self, stage, status="PASS", details=None) -> None:
        self.last_status = status

    def build_receipt(self, out_p, terminal_outcome="FAILED"):
        return SimpleNamespace(write_to_dir=lambda p: None)


def test_unverified_partial_qa_failure_never_freezes_on_resume(tmp_path: Path) -> None:
    """CAT4-P0-1: Partial deliverables saved after Exit QA failure cannot be frozen via raw glob fallback."""
    out_p = tmp_path / "run_out"
    out_p.mkdir()
    (out_p / "01_test_hitl_resolved_transcript.md").write_text("X" * 300, encoding="utf-8")
    (out_p / "02_test_executive_recap.md").write_text("Y" * 300, encoding="utf-8")
    (out_p / "metadata.json").write_text(
        json.dumps({"status": "PARTIAL_QA_FAILURE", "partial_deliverables_saved": True}),
        encoding="utf-8",
    )

    assert _is_unverified_partial_dir(out_p, "output_1") is True
    assert _is_unverified_partial_dir(out_p, "output_2") is True

    sm = _DummyStateMachine()
    assert _memoize_output_1(out_p, sm, {"resume": True}, "") is False
    assert _memoize_output_2(out_p, sm, {"resume": True}, "") is False
    assert "output_1" not in sm.frozen
    assert "output_2" not in sm.frozen


def test_mutation_intent_blocks_failed_eval_even_without_top_level_reviewer() -> None:
    """CAT4-P0-3: _resolve_evals_mutation_intent raises RuntimeError when mutate_graph=True and is_pass=False."""
    failed_eval = SimpleNamespace(
        authoritative_write_authorized=False,
        final_decision={"status": "FAIL"},
        judge_report={"overall_verdict": "FAIL"},
    )
    with pytest.raises(RuntimeError, match="Cannot mutate graph because QA/Judge evaluation failed"):
        _resolve_evals_mutation_intent(
            meta={"author": "operator_a"},
            mutate_graph=True,
            auto_mutate_on_pass=False,
            eval_result=failed_eval,
            reviewer=None,
        )

    passing_eval = SimpleNamespace(
        authoritative_write_authorized=True,
        status="PASS",
        final_decision={"status": "PASS", "authoritative_write_authorized": True},
        judge_report={"overall_verdict": "PASS"},
    )
    is_pass, should_mutate = _resolve_evals_mutation_intent(
        meta={"reviewer": "operator_a"},
        mutate_graph=False,
        auto_mutate_on_pass=True,
        eval_result=passing_eval,
        reviewer=None,
    )
    assert is_pass is True
    assert should_mutate is True


def test_verify_exit_qa_updates_manifest_hashes_after_auto_heal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAT4-P0-4: Post-judge X1-X3 healing refreshes artifact_manifest hashes and decision_seal_sha256."""
    from dual_pipeline.transcript_stages_core import x_gates

    out_p = tmp_path / "exit_qa_dir"
    out_p.mkdir()
    f2_p = out_p / "02_test_executive_recap.md"
    f3_p = out_p / "03_test_canonical_signal_projection.json"
    f2_p.write_text("Original recap", encoding="utf-8")
    f3_p.write_text('{"entities": []}', encoding="utf-8")

    def _fake_execute(self, recap, graph_json, metadata, transcript, auto_heal=True):
        f2_p.write_text("Healed recap content", encoding="utf-8")
        f3_p.write_text('{"entities": [{"id": "healed"}]}', encoding="utf-8")
        return True

    monkeypatch.setattr(x_gates.XExitGatesEvaluator, "execute", _fake_execute)

    synth = SimpleNamespace(
        output_1="Transcript text",
        output_2="Original recap",
        output_3='{"entities": []}',
    )
    eval_res = SimpleNamespace(
        authoritative_write_authorized=True,
        final_decision={
            "status": "PASS",
            "artifact_manifest": {
                "output_1_sha256": hashlib.sha256(b"Transcript text").hexdigest(),
                "output_2_sha256": hashlib.sha256(b"Original recap").hexdigest(),
                "output_3_sha256": hashlib.sha256(b'{"entities": []}').hexdigest(),
                "decision_seal_sha256": "old_seal",
            },
        },
    )
    tracker = _DummyTracker()
    verify_exit_qa(tracker, out_p, eval_res, attempt=1, synthesis_result=synth, metadata={})

    manifest = eval_res.final_decision["artifact_manifest"]
    assert manifest["output_2_sha256"] == hashlib.sha256(b"Healed recap content").hexdigest()
    assert manifest["output_3_sha256"] == hashlib.sha256(b'{"entities": [{"id": "healed"}]}').hexdigest()
    assert manifest["decision_seal_sha256"] != "old_seal"


def test_release_policy_accepts_resolved_hitl_clarification_and_advisory_out3() -> None:
    """CAT4-P1-1: validate_micro_judge_scorecard and _check_out3_threshold accept resolved HITL & PASS_ADVISORY."""
    scorecard = {
        "dimension_1": 18.0,
        "dimension_2": 18.0,
        "dimension_3": 18.0,
        "dimension_4": 18.0,
        "dimension_5": 18.0,
        "section_scores": {
            "header_and_overview": 9.0,
            "key_updates_technical_substance": 32.0,
            "conditional_extensions": 18.0,
            "appendix_stakeholder_perspectives": 31.0,
        },
        "composite_score": 90.0,
        "verdict": "PASS_AUTHORIZED_EXCEPTION",
        "hitl_clarification": {"status": "RESOLVED"},
    }
    valid, errors = validate_micro_judge_scorecard(scorecard, output_num=2)
    assert valid is True, f"Unexpected errors: {errors}"

    _check_out3_threshold(
        {
            "output_3_evaluation": {"composite_score": 92.0, "verdict": "PASS_ADVISORY"},
            "overall_verdict": "PASS_ADVISORY",
        },
        "pkg_test",
    )


def test_pre_persist_role_resolutions_falls_back_to_sqlite_graph_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAT4-P1-3: When written['03_graph'] is a JSON file, fallback resolves the SQLite graph path."""
    json_f = tmp_path / "03_signal.json"
    json_f.write_text("{}", encoding="utf-8")
    db_f = tmp_path / "candidate.sqlite"
    db_f.write_bytes(b"sqlite_dummy")

    applied: list[Path] = []
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.publish_helper._apply_role_resolutions_to_graph",
        lambda roles, g_path, rev: applied.append(g_path),
    )

    _pre_persist_role_resolutions_if_needed(
        meta={"hitl_role_resolutions": {"Alice": "VP"}, "graph_path": str(db_f)},
        written={"03_graph": str(json_f)},
        rev_op="operator_1",
    )
    assert applied == [db_f]
