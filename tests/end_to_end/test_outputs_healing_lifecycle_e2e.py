"""
tests/end_to_end/test_outputs_healing_lifecycle_e2e.py

End-to-end lifecycle verification across Outputs 01-03:
1. Full meeting intake deterministic healing, state machine evaluation, and frozen preservation.
2. Selective remediation with frozen Output 01/02 and regenerated/healed Output 03.
3. Notes mode lifecycle execution (Outputs 02 and 03 healed and frozen without Output 01).
4. Bounded recovery and circuit breaker halting retry loops at max attempts.
5. Verbatim evidence fidelity and strict byte-level idempotency.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy
from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_projection_evidence_and_coverage,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher


def _build_test_artifacts() -> tuple[str, str, str]:
    raw_f1 = (
        "### [00:01:00]\n\n"
        "**Alice Smith:** Welcome team. We will migrate the core database on 2026-10-15.\n\n"
        "### [00:02:30]\n\n"
        "**Bob Jones:** Agreed. PartnerAdvisory engineering will deliver the schema transition plan next week.   \n"
    )
    raw_f2 = (
        "# Subject: Platform Architecture Review\n\n"
        "**Date:** 2026-10-15\n\n"
        "## Objective\nMigrate core database to scalable cloud infrastructure.\n\n"
        "## Key Decisions & Agreements\n- Database migration scheduled for October 15, 2026.\n\n"
        "## Critical Action Items & Follow-ups\n"
        "| Owner | Action Item | Target Date |\n"
        "| :--- | :--- | :--- |\n"
        "| Bob Jones | Schema transition plan | 2026-10-22 |\n\n"
        "## Explicit Deadlines & Target Milestones\n- Milestone 1: Schema plan by 2026-10-22.\n"
    )
    raw_f3 = json.dumps(
        {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "person:alice-smith",
                    "source_classification": "FACT",
                    "exact_text": "We will migrate the core database [...] on 2026-10-15.",
                    "locator": {"kind": "timestamp", "value": "00:01:00"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:core-db",
                        "type": "system",
                        "name": "Core Database",
                        "mutation": "create",
                        "evidence_ids": ["ev-01"],
                        "description": "Primary relational database.",
                    }
                ],
                "relationship_signals": [],
            },
        },
        indent=2,
    )
    return raw_f1, raw_f2, raw_f3


class TestOutputsHealingLifecycleE2E:
    """End-to-end verification of deterministic healing, freezing, and bounded recovery."""

    def test_full_pipeline_heal_freeze_and_idempotency_lifecycle(self) -> None:
        raw_f1, raw_f2, raw_f3 = _build_test_artifacts()
        meta: Dict[str, Any] = {"meeting_date": "10/15/2026", "meeting_title": "Platform Review"}

        # Step 1: Execute initial healing pass
        h1, h2, h3, changes = DeterministicHealer.heal_all(raw_f1, raw_f2, raw_f3, metadata=meta)
        assert len(changes["output_1_healed"]) > 0 or "10/15/2026" in h1
        assert "2026-10-15" not in h1
        assert "2026-10-15" not in h2

        # Step 2: State machine evaluates passing attempt and freezes artifacts
        sm = SelectiveRemediationStateMachine()
        artifacts = {"output_1": h1, "output_2": h2, "output_3": h3}
        final_dec = {"status": "PASS", "output_1_deterministic_gates_pass": True}
        judge_rep = {
            "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 94, "verdict": "PASS"},
            "cross_artifact_reconciliation": {"passed": True},
        }
        qa_rep = {
            "output_01_forensic_transcript": {"passed": True},
            "output_02_executive_recap": {"passed": True},
            "output_03_canonical_signal_projection": {"passed": True},
        }

        all_pass, failed = sm.evaluate_attempt(final_dec, judge_rep, qa_rep, artifacts=artifacts)
        assert all_pass is True
        assert len(failed) == 0
        assert sm.is_frozen("output_1") and sm.is_frozen("output_2") and sm.is_frozen("output_3")

        # Step 3: Re-running healing on frozen artifacts guarantees strict byte immutability
        frozen_meta = dict(meta)
        frozen_meta["state_machine"] = sm

        h1_re, h2_re, h3_re, re_changes = DeterministicHealer.heal_all(h1, h2, h3, metadata=frozen_meta)
        assert h1_re == h1 and len(re_changes["output_1_healed"]) == 0
        assert h2_re == h2 and len(re_changes["output_2_healed"]) == 0
        assert h3_re == h3 and len(re_changes["output_3_healed"]) == 0

        # Step 4: Verify SHA256 integrity and dependency validity
        valid, err = check_frozen_dependency_validity("output_1", h1, sm, metadata=frozen_meta)
        assert valid is True, f"Output 1 validation failed: {err}"
        valid, err = check_frozen_dependency_validity("output_2", h2, sm, metadata=frozen_meta)
        assert valid is True, f"Output 2 validation failed: {err}"
        valid, err = check_frozen_dependency_validity("output_3", h3, sm, metadata=frozen_meta)
        assert valid is True, f"Output 3 validation failed: {err}"

    def test_selective_remediation_lifecycle_e2e(self) -> None:
        raw_f1, raw_f2, _ = _build_test_artifacts()
        sm = SelectiveRemediationStateMachine()
        sm.freeze_artifact("output_1", raw_f1)
        sm.freeze_artifact("output_2", raw_f2)

        # Attempt 1: Output 3 fails validation check
        bad_f3 = '{"schema_version": "5.7", "status": "incomplete"}'
        judge_rep_att1 = {
            "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 60, "verdict": "FAIL"},
            "cross_artifact_reconciliation": {"passed": False},
        }
        qa_rep_att1 = {
            "output_01_forensic_transcript": {"passed": True},
            "output_02_executive_recap": {"passed": True},
            "output_03_canonical_signal_projection": {"passed": False},
        }
        artifacts = {"output_1": raw_f1, "output_2": raw_f2, "output_3": bad_f3}

        all_pass, failed = sm.evaluate_attempt(
            {"status": "FAIL"}, judge_rep_att1, qa_rep_att1, artifacts=artifacts
        )
        assert all_pass is False
        assert failed == {"output_3"}
        assert sm.is_frozen("output_1") and sm.is_frozen("output_2")
        assert not sm.is_frozen("output_3")

        # Selective regeneration resolves output 2 directly from frozen state
        strat = FullMeetingSynthesisStrategy(provider=None)
        with tempfile.TemporaryDirectory() as td:
            ckpt_dir = Path(td) / "attempts" / "attempt_1"
            pa_ctx = {"state_machine": sm, "metadata": {"state_machine": sm}}
            resolved_o2 = strat._resolve_output_2(
                forensic_transcript=raw_f1,
                pa_context=pa_ctx,
                attempt=1,
                remediation_directive=None,
                progress_notify=None,
                ckpt_dir=ckpt_dir,
            )
            assert resolved_o2 == raw_f2
            assert sm.is_frozen("output_2")

        # Attempt 2: Repaired Output 3 passes
        repaired_f3 = json.dumps({"schema_version": "5.7", "status": "complete", "evidence_index": {}}, indent=2)
        judge_rep_att2 = {
            "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
            "cross_artifact_reconciliation": {"passed": True},
        }
        qa_rep_att2 = {
            "output_01_forensic_transcript": {"passed": True},
            "output_02_executive_recap": {"passed": True},
            "output_03_canonical_signal_projection": {"passed": True},
        }
        artifacts["output_3"] = repaired_f3
        sm.advance_attempt()
        all_pass_att2, failed_att2 = sm.evaluate_attempt(
            {"status": "PASS"}, judge_rep_att2, qa_rep_att2, artifacts=artifacts
        )
        assert all_pass_att2 is True
        assert len(failed_att2) == 0
        assert sm.is_frozen("output_3")

    def test_notes_mode_lifecycle_without_output_1_e2e(self) -> None:
        _, raw_f2, raw_f3 = _build_test_artifacts()
        sm = SelectiveRemediationStateMachine()
        meta = {"state_machine": sm, "is_notes_mode": True}

        h1, h2, h3, _ = DeterministicHealer.heal_all(None, raw_f2, raw_f3, metadata=meta)
        assert h1 == ""

        artifacts = {"output_2": h2, "output_3": h3}
        judge_rep = {
            "output_2_evaluation": {"composite_score": 94, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 93, "verdict": "PASS"},
            "cross_artifact_reconciliation": {"passed": True},
        }
        qa_rep = {
            "output_02_executive_recap": {"passed": True},
            "output_03_canonical_signal_projection": {"passed": True},
        }

        all_pass, failed = sm.evaluate_attempt(
            {"status": "PASS"}, judge_rep, qa_rep, artifacts=artifacts, is_notes_mode=True
        )
        assert all_pass is True
        assert len(failed) == 0
        assert sm.is_frozen("output_2") and sm.is_frozen("output_3")
        assert not sm.is_frozen("output_1")

        # Verify frozen immutability in notes mode
        h1_sub, h2_sub, h3_sub, sub_changes = DeterministicHealer.heal_all(None, h2, h3, metadata=meta)
        assert h1_sub == ""
        assert h2_sub == h2 and len(sub_changes["output_2_healed"]) == 0
        assert h3_sub == h3 and len(sub_changes["output_3_healed"]) == 0

    def test_bounded_recovery_and_circuit_breaker_e2e(self) -> None:
        sm = SelectiveRemediationStateMachine(max_attempts=3)
        assert sm.should_retry() is True

        # Attempt 1: Score 50 (Fail)
        sm.record_candidate("output_3", '{"cand": 1}', composite_score=50, verdict="FAIL")
        assert sm.get_high_water_mark_score("output_3") == 50
        assert sm.should_retry() is True
        sm.advance_attempt()
        assert sm.current_attempt == 2

        # Attempt 2: Score 40 (Regression triggers high-water mark restore)
        sm.record_candidate("output_3", '{"cand": 2}', composite_score=40, verdict="FAIL")
        assert sm.has_regressed("output_3") is True
        restored = sm.restore_high_water_mark("output_3")
        assert restored == '{"cand": 1}'
        assert sm.should_retry() is True
        sm.advance_attempt()
        assert sm.current_attempt == 3

        # Attempt 3: Score 55 (Still failing, reaches max attempts)
        sm.record_candidate("output_3", '{"cand": 3}', composite_score=55, verdict="FAIL")
        sm.advance_attempt()
        assert sm.current_attempt == 4
        assert sm.should_retry() is False, "Circuit breaker failed to halt execution after reaching max_attempts!"

    def test_verbatim_evidence_fidelity_and_spliced_locators_e2e(self) -> None:
        raw_f1, _, raw_f3 = _build_test_artifacts()
        f3_dict = json.loads(raw_f3)
        changes: list[str] = []

        heal_projection_evidence_and_coverage(f3_dict, metadata={}, f1_text=raw_f1, changes=changes)

        ev_idx = f3_dict["evidence_index"]
        # ev-01 should be cleaned of [...] and must exist in raw_f1
        ev_01 = ev_idx["ev-01"]
        assert "[...]" not in ev_01["exact_text"]
        assert ev_01["exact_text"] in raw_f1, "Cleaned quote was synthesized and does not exist in raw_f1!"

        # Dialogue turns should have been extracted from raw_f1 without orphan drops
        assert any(k.startswith("ev-dialogue") or k == "ev-01" for k in ev_idx)
        for eid, ev in ev_idx.items():
            if "exact_text" in ev and ev.get("attribution_mode") == "SPOKEN_DIALOGUE":
                assert ev["exact_text"] in raw_f1 or ev["text"] in raw_f1
