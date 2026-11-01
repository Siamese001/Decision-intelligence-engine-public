"""
Adversarial and edge-case tests for selective regeneration memoization, frozen gate hash alignment,
and PASS_AUTHORIZED_EXCEPTION boundary conditions.

Covers mandatory edge-case categories:
1. Exact production regression (Task-237 stale remediation_directive_graph unfreezing output_3 during --regenerate 02)
2. Boundary payload (empty regen_spec, empty metadata, missing frozen content)
3. Malformed input (corrupted JSON in release decision / mismatched judge digest)
4. Invariant bypass attempt (mock/short-circuit synthesis attempting authoritative mutation under PASS_AUTHORIZED_EXCEPTION)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _should_regenerate_output
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    is_eval_verdict_pass,
)
from dual_pipeline.transcript_qa_core.evaluator import _build_skip_eval
from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity


class _DummyStateMachine:
    def __init__(self, frozen: Dict[str, str]) -> None:
        self.frozen_artifacts = dict(frozen)
        self.unfrozen: list[str] = []

    def is_frozen(self, key: str) -> bool:
        return key in self.frozen_artifacts

    def get_frozen_content(self, key: str) -> str | None:
        return self.frozen_artifacts.get(key)

    def unfreeze_artifact(self, key: str, force: bool = False) -> None:
        self.unfrozen.append(key)
        self.frozen_artifacts.pop(key, None)


def test_selective_regen_02_ignores_stale_graph_remediation_directive() -> None:
    """
    Exact production regression: when --regenerate 02 is passed (regen_spec="['output_2']"),
    a stale remediation_directive_graph in metadata.json must NOT unfreeze output_3.
    """
    sm = _DummyStateMachine({"output_1": "f1", "output_3": "f3"})
    meta = {
        "regenerate": "02",
        "remediation_directive_graph": "STALE_DIRECTIVE_FROM_PRIOR_RUN",
        "resume": False,
    }
    regen_spec = "['output_2']"

    assert _should_regenerate_output(sm, "1", regen_spec, meta) is False
    assert _should_regenerate_output(sm, "2", regen_spec, meta) is True
    assert _should_regenerate_output(sm, "3", regen_spec, meta) is False
    assert "output_3" not in sm.unfrozen
    assert sm.is_frozen("output_3") is True


def test_unscoped_rerun_honors_remediation_directive_graph() -> None:
    """When no explicit selective regen_spec is active, remediation_directive_graph unfreezes output_3."""
    sm = _DummyStateMachine({"output_3": "f3"})
    meta = {
        "remediation_directive_graph": "ACTIVE_DIRECTIVE",
        "resume": False,
    }
    assert _should_regenerate_output(sm, "3", "", meta) is True
    assert "output_3" in sm.unfrozen


def test_frozen_dependency_validity_aligns_hash_on_regenerate() -> None:
    """When --regenerate 02 is active and output_3 is frozen in state_machine, stale metadata sha aligns cleanly."""
    content = '{"graph_mutations": {}}'
    actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    sm = _DummyStateMachine({"output_3": content})
    meta = {
        "regenerate": "02",
        "output_3_sha256": "0" * 64,
        "artifact_manifest": {"output_3_sha256": "0" * 64},
    }
    valid, err = check_frozen_dependency_validity("output_3", content, sm, meta)
    assert valid is True
    assert err is None
    assert meta["output_3_sha256"] == actual_sha
    assert meta["artifact_manifest"]["output_3_sha256"] == actual_sha


def test_boundary_empty_frozen_content_fails_closed() -> None:
    """Boundary payload: empty frozen content must fail closed."""
    sm = _DummyStateMachine({"output_3": ""})
    valid, err = check_frozen_dependency_validity("output_3", "", sm, {})
    assert valid is False
    assert "Missing frozen content" in str(err)


def test_invariant_bypass_mock_or_digest_mismatch_blocked(tmp_path: Path) -> None:
    """
    Invariant bypass attempt & malformed input:
    1. Mock synthesis claiming PASS_AUTHORIZED_EXCEPTION must be blocked.
    2. Mismatched judge evaluation digest must be blocked.
    3. Corrupted JSON must raise RuntimeError.
    """
    out_p = tmp_path / "bypass_attempt"
    out_p.mkdir()

    # 1. Corrupted JSON
    (out_p / "final_release_decision.json").write_text("{corrupted_json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Corrupted release decision file"):
        _assert_authoritative_mutation_preconditions(out_p, {})

    # 2. Mock flag in decision despite PASS_AUTHORIZED_EXCEPTION
    (out_p / "final_release_decision.json").write_text(
        json.dumps({"status": "PASS_AUTHORIZED_EXCEPTION", "is_mock": True}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
        _assert_authoritative_mutation_preconditions(out_p, {})

    # 3. Digest mismatch between decision and judge report
    (out_p / "final_release_decision.json").write_text(
        json.dumps({
            "status": "PASS_AUTHORIZED_EXCEPTION",
            "judge_evaluation_digest": "sha256:1111",
        }),
        encoding="utf-8",
    )
    (out_p / "llm_judge_evaluation.json").write_text(
        json.dumps({
            "overall_verdict": "PASS_AUTHORIZED_EXCEPTION",
            "evaluation_receipt_digest": "sha256:2222",
        }),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="decision judge digest"):
        _assert_authoritative_mutation_preconditions(out_p, {})


def test_frozen_output_3_skip_eval_structure() -> None:
    """Verify _build_skip_eval produces a valid FROZEN_PRIOR_ARTIFACT scorecard for output_3."""
    qa_report = {"output_03_canonical_signal_projection": {"frozen": True, "passed": True}}
    skip_card = _build_skip_eval(qa_report, "output_03_canonical_signal_projection", '{"graph_mutations": {}}')
    assert skip_card["verdict"] == "PASS"
    assert skip_card["reason"] == "FROZEN_PRIOR_ARTIFACT"


def test_appendix_grouped_participants_promotion_and_role_split_edge_cases() -> None:
    """
    Wave 2 adversarial edge cases:
    - Grouped participant headers ('# Other Active Participants') promote cleanly to individual
      speaker sections without duplicating existing speaker headers.
    - _split_compound_roles preserves '&' and 'and' inside single titles while splitting multi-word
      '/' titles.
    """
    from dual_pipeline.transcript_qa_core.appendix_topics import (
        promote_grouped_appendix_participants,
        speaker_header_present,
    )
    from dual_pipeline.transcript_stages_core.speaker_role_reconciler import _split_compound_roles

    assert _split_compound_roles("Cloud & Infrastructure Lead") == ["Cloud & Infrastructure Lead"]
    assert _split_compound_roles("Enterprise Compute and Cloud Leader") == ["Enterprise Compute and Cloud Leader"]
    assert _split_compound_roles("Enterprise Architect / Cloud Platform Lead") == [
        "Enterprise Architect",
        "Cloud Platform Lead",
    ]
    assert _split_compound_roles("AI/ML Director") == ["AI/ML Director"]

    raw_appendix = (
        "Drew Oakley — PartnerAdvisory, Managing Director\n\n- **Scope:** Led introductions.\n\n"
        "# Other Active Participants\n"
        "- **Christian Moberg — Principal DevOps Engineer:** Described Terraform state isolation.\n"
        "- **Drew Oakley — Managing Director:** Duplicate entry should be skipped.\n"
    )
    promoted = promote_grouped_appendix_participants(raw_appendix)
    assert speaker_header_present(promoted, "Christian Moberg") is True
    assert speaker_header_present(promoted, "Drew Oakley") is True
    assert promoted.count("# Drew Oakley") == 1

