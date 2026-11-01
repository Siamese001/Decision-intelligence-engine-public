"""
tests/end_to_end/test_resume_frozen_output1_e2e.py

End-to-end resume lifecycle verification:
Guarantees that a healed Deliverable 01 (with sanitized header and restored [00:21:59] turn)
is memoized, frozen, preserved against unconstrained re-synthesis, and protected from
overwrite across multi-attempt --resume execution cycles.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.synthesis_loop_support import check_deliverable_01_deploy_action
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy, SynthesisResult
from dual_pipeline.llm_judge_core.forensic_checks import (
    apply_dimension1_penalty,
    assign_forensic_verdict,
    check_dimension1_grounding,
)
from dual_pipeline.remediation_core import RemediationStateMachine
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer


SOURCE_TEXT = """
[00:21:51] **Amit Ayer:** So, not not code signal.

[00:21:53] **Kannan Chandraprakash:** Not good, sorry.

[00:21:56] **Drew Oakley:** Mm.

[00:21:59] **Amit Ayer:** Sounds good. Any.

[00:21:59] **Kannan Chandraprakash:** That was, I was trying to look for the link in that in the chat. It's code interview.

[00:22:05] **Amit Ayer:** We've had a success rate of the, I mean, less than 50%.
"""

HEALED_OUTPUT_1 = """# Forensic Transcript

**Date:** 09/14/2026
**Project:** Client Interview Prep
**Attendees:** Amit Ayer, Kannan Chandraprakash, Drew Oakley

## Forensic Transcript

[00:21:51] **Amit Ayer:** So, not not code signal.

[00:21:53] **Kannan Chandraprakash:** Not good, sorry.

[00:21:56] **Drew Oakley:** Mm.

[00:21:59] **Amit Ayer:** Sounds good. Any.

[00:21:59] **Kannan Chandraprakash:** That was, I was trying to look for the link in that in the chat. It's code interview.

[00:22:05] **Amit Ayer:** We've had a success rate of the, I mean, less than 50%.

## Ambiguity Lock Register

| Locator | Spoken Term | Flag Type | Resolution / Evidence | Rule Applied |
| :--- | :--- | :--- | :--- | :--- |
| [00:21:59] | code interview | Domain Term | Verified technical interview platform | RULE-LOSSLESS-SYNTAX |
"""


def _setup_ingestion_fixture(tmp_dir: Path) -> Dict[str, Any]:
    """Sets up a realistic ingestion output directory with healed Deliverable 01 and historical failure decision."""
    out_dir = tmp_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    att_dir = out_dir / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True, exist_ok=True)

    f1_path = out_dir / "01_NEA_CI_Prep_Kannan_Debrief_09-14-2026_hitl_resolved_transcript.md"
    f1_path.write_text(HEALED_OUTPUT_1, encoding="utf-8")
    f1_sha = hashlib.sha256(HEALED_OUTPUT_1.encode("utf-8")).hexdigest()

    # Stage seals indicating Output 01 passed
    seals = {
        "output_1": {
            "status": "PASS",
            "score": 95.0,
            "sha256": f1_sha,
            "timestamp": 1726340000.0,
        }
    }
    (out_dir / "stage_seals.json").write_text(json.dumps(seals, indent=2), encoding="utf-8")
    (att_dir / "stage_seals.json").write_text(json.dumps(seals, indent=2), encoding="utf-8")

    # Historical attempt decision where output_1_pass was false due to short-circuit QA failure on Output 02
    stale_dec = {
        "output_1_pass": False,
        "output_2_pass": False,
        "output_3_pass": False,
        "status": "FAIL",
        "composite_score": 87.0,
        "artifact_manifest": {
            "output_1_sha256": f1_sha,
        },
    }
    (att_dir / "final_release_decision.json").write_text(json.dumps(stale_dec, indent=2), encoding="utf-8")
    (out_dir / "final_release_decision.json").write_text(json.dumps(stale_dec, indent=2), encoding="utf-8")

    # Checkpoint
    (att_dir / "output_1.ckpt.md").write_text(HEALED_OUTPUT_1, encoding="utf-8")

    return {
        "out_dir": out_dir,
        "att_dir": att_dir,
        "f1_path": f1_path,
        "f1_sha": f1_sha,
    }


def test_resume_preserves_and_freezes_healed_deliverable_01() -> None:
    """Verifies that --resume accurately freezes Deliverable 01 and preserves it from re-synthesis and overwrite."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        fx = _setup_ingestion_fixture(tmp_dir)
        out_dir = fx["out_dir"]
        f1_path = fx["f1_path"]
        initial_sha = fx["f1_sha"]

        metadata = {
            "resume": True,
            "source_text": SOURCE_TEXT,
            "output_dir": str(out_dir),
            "meeting_date": "09/14/2026",
            "project": "Client Interview Prep",
            "attendees": ["Amit Ayer", "Kannan Chandraprakash", "Drew Oakley"],
        }

        # 1. State machine initialization
        sm = RemediationStateMachine(max_attempts=3)
        metadata["state_machine"] = sm

        # 2. Checkpoint memoization
        memoized = memoize_substage_checkpoints(
            out_p=out_dir,
            state_machine=sm,
            metadata=metadata,
            modality=CanonicalModality.MEETING_TRANSCRIPT,
        )

        assert memoized["output_1"] is True, "memoize_substage_checkpoints must memoize Output 01 on --resume"
        assert sm.is_frozen("output_1") is True, "Deliverable 01 must be frozen in state machine"
        assert sm.get_frozen_sha256("output_1") == initial_sha, "Frozen SHA must match disk file"

        # 3. Strategy resolution
        strategy = FullMeetingSynthesisStrategy(
            provider=object(),
            l2_forensic=object(),
            l2_recap=object(),
            l2_graph=object(),
        )
        resolved_1 = strategy._resolve_output_1(
            source_text=SOURCE_TEXT,
            metadata=metadata,
            attempt=2,
            remediation_directive=None,
            progress_notify=None,
            ckpt_dir=fx["att_dir"],
            pa_context={"metadata": metadata, "output_dir": str(out_dir), "state_machine": sm},
        )

        assert resolved_1 == HEALED_OUTPUT_1, "Resolved Deliverable 01 must match healed content"

        # 4. Lifecycle deployment protection
        synthesis_result = SynthesisResult(
            output_1=resolved_1,
            output_2="Recap",
            output_3="Graph",
            attempt=2,
            metadata=metadata,
        )
        should_write, content_to_write = check_deliverable_01_deploy_action(
            f1_path=f1_path,
            out_p=out_dir,
            synthesis_result=synthesis_result,
            meta=metadata,
        )

        assert should_write is False, "deploy_deliverables must not overwrite frozen Deliverable 01"
        assert f1_path.read_text(encoding="utf-8") == HEALED_OUTPUT_1
        assert hashlib.sha256(f1_path.read_bytes()).hexdigest() == initial_sha


def test_deterministic_dimension_1_penalizes_and_heals_defects() -> None:
    """Verifies that ungrounded editorial notes and dropped short turns drop D1 to 12 (<14), and healing restores 25."""
    # Defective transcript: injected ungrounded platform & diarization headers, dropped turn [00:21:59] Amit Ayer
    defective_text = """# Live Meeting Transcript

**Date:** 09/14/2026
**Platform Reference:** Microsoft Teams Commercial v2.1
**Diarization Limitation Notice:** Whisper single-channel diarization bounds applied.
**Project:** Client Interview Prep

## Forensic Transcript

[00:21:50] **Editorial Commentary:** Whisper single-channel diarization bounds applied.

[00:21:51] **Amit Ayer:** So, not not code signal.

[00:21:53] **Kannan Chandraprakash:** Not good, sorry.

[00:21:56] **Drew Oakley:** Mm.

[00:21:59] **Kannan Chandraprakash:** That was, I was trying to look for the link in that in the chat. It's code interview.

[00:22:05] **Amit Ayer:** We've had a success rate of the, I mean, less than 50%.
"""
    meta = {
        "source_text": SOURCE_TEXT,
        "meeting_date": "09/14/2026",
        "project": "Client Interview Prep",
        "attendees": ["Amit Ayer", "Kannan Chandraprakash", "Drew Oakley"],
    }

    # 1. Deterministic check flags both defects
    passed, defect_codes, rationales, total_deduct = check_dimension1_grounding(defective_text, metadata=meta)
    assert passed is False
    assert "ERR_UNGROUNDED_EDITORIAL_COMMENTARY" in defect_codes
    assert "ERR_OMITTED_SOURCE_TURNS" in defect_codes
    assert total_deduct == 13  # 10 for editorial + 3 for dropped turn

    # 2. Applying penalty drops Dimension 1 to 12 (<14 floor) and verdict to FAIL
    scorecard: Dict[str, Any] = {
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 25,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
    }
    apply_dimension1_penalty(scorecard, defective_text, metadata=meta)
    assert scorecard["dimension_1_forensic_grounding_and_fidelity"] == 12

    verdict_card = assign_forensic_verdict(scorecard, defective_text, metadata=meta)
    assert verdict_card["verdict"] == "FAIL"
    assert "PER-DIMENSION FLOOR FAILURE" in verdict_card["forensic_rationale"]

    # 3. DeterministicHealer heals both defects automatically
    healed, changes = DeterministicHealer.heal_output_1(defective_text, metadata=meta)

    assert "Platform Reference" not in healed
    assert "Diarization Limitation Notice" not in healed
    assert "[00:21:59] **Amit Ayer:** Sounds good. Any." in healed

    # Verify order: Amit Ayer's turn appears BEFORE Kannan's [00:21:59] turn
    amit_pos = healed.find("[00:21:59] **Amit Ayer:** Sounds good. Any.")
    kannan_pos = healed.find("[00:21:59] **Kannan Chandraprakash:**")
    assert amit_pos != -1
    assert kannan_pos != -1
    assert amit_pos < kannan_pos, "Amit Ayer's turn must precede Kannan's turn at the same timestamp"

    # 4. Re-evaluation passes cleanly with 25/25
    passed_healed, defects_healed, _, deduct_healed = check_dimension1_grounding(healed, metadata=meta)
    assert passed_healed is True
    assert len(defects_healed) == 0
    assert deduct_healed == 0

    clean_card: Dict[str, Any] = {
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 25,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
    }
    apply_dimension1_penalty(clean_card, healed, metadata=meta)
    assert clean_card["dimension_1_forensic_grounding_and_fidelity"] == 25


def test_multi_attempt_retry_preserves_frozen_output1_across_retries() -> None:
    """Verifies that across attempts 2 and 3, Deliverable 01 remains frozen and unmutated."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        fx = _setup_ingestion_fixture(tmp_dir)
        out_dir = fx["out_dir"]
        f1_path = fx["f1_path"]
        initial_sha = fx["f1_sha"]

        metadata = {
            "resume": True,
            "source_text": SOURCE_TEXT,
            "output_dir": str(out_dir),
            "meeting_date": "09/14/2026",
            "project": "Client Interview Prep",
            "attendees": ["Amit Ayer", "Kannan Chandraprakash", "Drew Oakley"],
        }
        sm = RemediationStateMachine(max_attempts=3)
        metadata["state_machine"] = sm
        # Attempt 1: memoize
        memoized = memoize_substage_checkpoints(out_dir, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)
        assert memoized["output_1"] is True
        assert sm.is_frozen("output_1") is True

        # Attempt 2: simulate Output 02 failing QA while Output 01 remains passing
        res_att2 = SynthesisResult(output_1=HEALED_OUTPUT_1, output_2="Bad Recap", output_3="Graph", attempt=2, metadata=metadata)
        should_write, _ = check_deliverable_01_deploy_action(f1_path, out_dir, res_att2, metadata)
        assert should_write is False
        assert sm.is_frozen("output_1") is True

        # Attempt 3: retry continues, Deliverable 01 is still preserved
        res_att3 = SynthesisResult(output_1=HEALED_OUTPUT_1, output_2="Healed Recap", output_3="Graph", attempt=3, metadata=metadata)
        should_write_3, _ = check_deliverable_01_deploy_action(f1_path, out_dir, res_att3, metadata)
        assert should_write_3 is False
        assert hashlib.sha256(f1_path.read_bytes()).hexdigest() == initial_sha
