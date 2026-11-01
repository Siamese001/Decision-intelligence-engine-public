"""
tests/adversarial/test_healing_and_frozen_adversarial.py

Adversarial reproduction and verification suite for:
1. Frozen-artifact mutation in DeterministicHealer across Outputs 01-03.
2. Unnecessary regeneration in synthesis strategies and notes mode state machine.
3. Evidence loss and source fidelity in reconciliation coverage and graph healers.
4. Endless repair loops in SurgicalPatcher and idempotency invariance.
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
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import evaluate_stage_03_checkpoint
from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    _clean_spliced_quote,
    heal_spliced_locators,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import (
    ProtectedPathMutationError,
    SurgicalPatcher,
)


class TestFrozenArtifactMutation:
    """Verifies that frozen artifacts remain strictly immutable byte-for-byte."""

    def test_heal_output_1_preserves_frozen_artifact_exact_bytes(self) -> None:
        sm = SelectiveRemediationStateMachine()
        raw_f1 = "**Alice**: Unhealed   trailing whitespace and date 2026-09-25.   \n\n\n"
        sm.freeze_artifact("output_1", raw_f1)
        meta = {"state_machine": sm}

        healed, changes = DeterministicHealer.heal_output_1(raw_f1, metadata=meta)
        assert healed == raw_f1, "DeterministicHealer.heal_output_1 mutated a frozen Output 01!"
        assert len(changes) == 0, "DeterministicHealer.heal_output_1 emitted changes for a frozen Output 01!"

    def test_heal_output_2_preserves_frozen_artifact_exact_bytes(self) -> None:
        sm = SelectiveRemediationStateMachine()
        raw_f2 = "# Subject: Test\n\n**Date:** 2026-09-25\n\n## Objective\nSome objective.\n"
        sm.freeze_artifact("output_2", raw_f2)
        meta = {"state_machine": sm}

        healed, changes = DeterministicHealer.heal_output_2(raw_f2, metadata=meta)
        assert healed == raw_f2, "DeterministicHealer.heal_output_2 mutated a frozen Output 02!"
        assert len(changes) == 0, "DeterministicHealer.heal_output_2 emitted changes for a frozen Output 02!"

    def test_heal_output_3_preserves_frozen_artifact_exact_content(self) -> None:
        sm = SelectiveRemediationStateMachine()
        raw_f3 = '{"schema_version": "5.7", "evidence_index": {}}'
        sm.freeze_artifact("output_3", raw_f3)
        meta = {"state_machine": sm}

        healed, changes = DeterministicHealer.heal_output_3(raw_f3, metadata=meta)
        assert healed == raw_f3, "DeterministicHealer.heal_output_3 mutated a frozen Output 03!"
        assert len(changes) == 0, "DeterministicHealer.heal_output_3 emitted changes for a frozen Output 03!"

    def test_heal_out3_preserves_indent_and_sha_on_dict_input(self) -> None:
        sm = SelectiveRemediationStateMachine()
        data = {"schema_version": "5.7", "evidence_index": {}}
        expected_json = json.dumps(data, indent=2)
        sm.freeze_artifact("output_3", expected_json)
        meta = {"state_machine": sm}

        healed, changes = DeterministicHealer._heal_out3(data, meta)
        assert healed == expected_json, "Frozen Output 03 dict serialization did not match indent=2 frozen string!"
        valid, err = check_frozen_dependency_validity(
            "output_3",
            sm.get_frozen_content("output_3"),
            sm,
            metadata=meta,
            candidate_text=healed,
        )
        assert valid is True, f"SHA mismatch on frozen Output 03: {err}"

    def test_stage03_checkpoint_returns_state_machine_frozen_content(self) -> None:
        sm = SelectiveRemediationStateMachine()
        frozen_projection = json.dumps({"schema_version": "5.7", "frozen": True}, indent=2)
        sm.freeze_artifact("output_3", frozen_projection)
        artifacts: Dict[str, Any] = {}  # Notice: 03_canonical_signal_projection.json is NOT in artifacts yet
        rec = MagicMock()

        res = evaluate_stage_03_checkpoint(
            pipeline=MagicMock(),
            canonical_projection='{"schema_version": "5.7", "unfrozen": "dirty"}',
            forensic_transcript="some text",
            metadata={},
            state_machine=sm,
            artifacts=artifacts,
            recorder=rec,
            notify_step=MagicMock(),
            attempt=1,
            is_notes_mode=False,
        )
        assert res == frozen_projection, "Stage 03 checkpoint returned dirty candidate instead of frozen content!"
        assert artifacts["03_canonical_signal_projection.json"] == frozen_projection


class TestUnnecessaryRegeneration:
    """Verifies that valid frozen artifacts are not unfrozen or regenerated prematurely."""

    def test_resolve_output_2_preserves_frozen_when_checkpoint_file_absent(self) -> None:
        sm = SelectiveRemediationStateMachine()
        raw_f2 = "## Objective\nVerified output 2 content with sufficient length to pass validation check.\n" + ("x" * 600)
        sm.freeze_artifact("output_2", raw_f2)

        strat = FullMeetingSynthesisStrategy(provider=None)
        with tempfile.TemporaryDirectory() as td:
            ckpt_dir = Path(td) / "attempts" / "attempt_1"
            pa_context = {"state_machine": sm, "metadata": {"state_machine": sm}}

            out_2 = strat._resolve_output_2(
                forensic_transcript="### [00:01:00]\n**Alice:** Verified turn.",
                pa_context=pa_context,
                attempt=1,
                remediation_directive=None,
                progress_notify=None,
                ckpt_dir=ckpt_dir,
            )
            assert out_2 == raw_f2
            assert sm.is_frozen("output_2"), "Output 02 was unfrozen simply because checkpoint file was absent!"

    def test_resolve_output_3_preserves_frozen_when_checkpoint_file_absent(self) -> None:
        sm = SelectiveRemediationStateMachine()
        raw_f3 = json.dumps({"schema_version": "5.7", "title": "Verified Projection"}, indent=2)
        sm.freeze_artifact("output_3", raw_f3)

        strat = FullMeetingSynthesisStrategy(provider=None)
        with tempfile.TemporaryDirectory() as td:
            ckpt_dir = Path(td) / "attempts" / "attempt_1"
            pa_context = {"state_machine": sm, "metadata": {"state_machine": sm}}

            out_3 = strat._resolve_output_3(
                forensic_transcript="### [00:01:00]\n**Alice:** Verified turn.",
                executive_recap="## Objective\nSome recap.",
                pa_context=pa_context,
                attempt=1,
                remediation_directive=None,
                progress_notify=None,
                ckpt_dir=ckpt_dir,
            )
            assert out_3 == raw_f3
            assert sm.is_frozen("output_3"), "Output 03 was unfrozen simply because checkpoint file was absent!"

    def test_notes_mode_does_not_unfreeze_downstream_outputs(self) -> None:
        sm = SelectiveRemediationStateMachine()
        sm.freeze_artifact("output_2", "verified recap")
        sm.freeze_artifact("output_3", "verified graph")

        final_decision = {"status": "PASS"}
        judge_report = {
            "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "cross_artifact_reconciliation": {"passed": True},
        }
        qa_report = {
            "output_02_executive_recap": {"passed": True},
            "output_03_canonical_signal_projection": {"passed": True},
        }

        all_passed, failed = sm.evaluate_attempt(
            final_decision,
            judge_report,
            qa_report,
            artifacts={"output_2": "verified recap", "output_3": "verified graph"},
            is_notes_mode=True,
        )
        assert "output_2" not in failed, "Notes mode treated skipped output 1 as failure, unfreezing output 2!"
        assert "output_3" not in failed, "Notes mode treated skipped output 1 as failure, unfreezing output 3!"
        assert sm.is_frozen("output_2"), "Output 02 was unfrozen in notes mode!"
        assert sm.is_frozen("output_3"), "Output 03 was unfrozen in notes mode!"


class TestEvidenceLossAndFidelity:
    """Verifies verbatim quote preservation and non-destructive evidence handling."""

    def test_clean_spliced_quote_preserves_authentic_transcript_verbatim(self) -> None:
        f1_text = (
            "### [00:02:15]\n"
            "**Amit Ayer:** We will migrate Maximo to cloud infrastructure next quarter after security clearance."
        )
        # Spliced quote with ellipsis tokens
        ev = {
            "exact_text": "We will migrate Maximo [...] next quarter",
            "locator": {"kind": "timestamp", "value": "02:15"},
        }
        changes: list[str] = []
        _clean_spliced_quote(ev, "ev-01", changes, f1_text=f1_text)

        # The cleaned exact_text MUST exist verbatim in f1_text!
        cleaned = ev["exact_text"]
        assert "[...]" not in cleaned, "Ellipsis tokens were not cleaned!"
        assert cleaned in f1_text, f"Cleaned quote '{cleaned}' was synthesized and does NOT exist in f1_text!"

    def test_evidence_index_healing_reclassifies_cited_metadata_without_citation_loss(self) -> None:
        from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection

        data = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-header-01": {
                    "statement_id": "ev-header-01",
                    "speaker": "header",
                    "text": "Meeting Metadata and Schedule Details",
                    "exact_text": "Meeting Metadata and Schedule Details",
                    "locator": {"kind": "section", "value": "header"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:test_sys",
                        "type": "system",
                        "name": "Test System",
                        "mutation": "create",
                        "evidence_ids": ["ev-header-01"],
                        "description": "Test system description.",
                    },
                    {
                        "id": "system:target_sys",
                        "type": "system",
                        "name": "Target System",
                        "mutation": "create",
                        "evidence_ids": ["ev-header-01"],
                        "description": "Target system description.",
                    },
                ],
                "relationship_signals": [
                    {
                        "from_id": "system:test_sys",
                        "to_id": "system:target_sys",
                        "type": "CONNECTS_TO",
                        "mutation": "create",
                        "evidence_ids": ["ev-header-01"],
                    }
                ],
            },
        }
        changes: list[str] = []
        heal_graph_projection(data, changes)

        # ev-header-01 must NOT be dropped, leaving the entity without evidence
        ev_idx = data["evidence_index"]
        assert "ev-header-01" in ev_idx, "Cited evidence item was popped from evidence_index, causing evidence loss!"
        ent = data["graph_mutations"]["entity_upserts"][0]
        assert "ev-header-01" in ent["evidence_ids"], "Evidence citation was stripped from entity!"


class TestEndlessRepairLoopsAndIdempotency:
    """Verifies bounded surgical remediation, invariance protection, and idempotency."""

    def test_surgical_patcher_auto_heal_preserves_protected_sections(self) -> None:
        original = (
            "# Subject: Technical Review\n\n"
            "**Date:** 09/25/2026\n\n"
            "## Architecture Overview\n"
            "Protected architecture section with exact wording and formatting.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "Initial draft appendix.\n"
        )
        new_appendix = (
            "### Amit Ayer - PartnerAdvisory, Lead Architect\n"
            "- **Methodology**: Detailed explanation.\n"
        )

        reconstructed, audit = SurgicalPatcher.apply_surgical_patch(
            original_text=original,
            target_section="Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
            new_section_content=new_appendix,
            protected_sections=["Architecture Overview"],
            auto_heal=True,
            metadata={"meeting_date": "09/25/2026"},
        )
        assert "Protected architecture section" in reconstructed
        assert "Amit Ayer" in reconstructed

    def test_deterministic_healers_strict_idempotency(self) -> None:
        f1_raw = "**Alice**: Hello world\n**Bob**: Status update on 09/18/2026.\n"
        h1_first, _ = DeterministicHealer.heal_output_1(f1_raw)
        h1_second, changes_second = DeterministicHealer.heal_output_1(h1_first)
        assert h1_first == h1_second, "heal_output_1 is not idempotent!"
        assert len(changes_second) == 0, "heal_output_1 produced changes on already-healed text!"

        f2_raw = (
            "# Subject: Review\n\n"
            "**Date:** 09/18/2026\n\n"
            "## Objective\nMeeting objective.\n\n"
            "## Findings\nMeeting findings.\n"
        )
        h2_first, _ = DeterministicHealer.heal_output_2(f2_raw)
        h2_second, changes_2_second = DeterministicHealer.heal_output_2(h2_first)
        assert h2_first == h2_second, "heal_output_2 is not idempotent!"
        assert len(changes_2_second) == 0, "heal_output_2 produced changes on already-healed text!"
