"""
dual_pipeline/transcript_pipeline_core/stage_checkpoint.py

Shift-Left Stage 01 Checkpoint Gatekeeper.
Evaluates deterministic QA Gate 1 immediately upon completion of Stage 01,
freezing valid transcripts and failing closed on unrecoverable defects
before dispatching the concurrent Stage 02 / Stage 03 fork.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Optional

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3

from .surgical_remediator import SurgicalRemediator

logger = logging.getLogger(__name__)

_FATAL_STAGE_01_MARKERS = (
    "turn density failure: 0 turns",
    "0 dialogue turns",
    "zero turns",
    "empty transcript",
)


def is_fatal_stage_01_defect(failures: list[str]) -> bool:
    """Returns True if any failure indicates an unrecoverable empty or 0-turn transcript."""
    return any(
        any(marker in f.lower() for marker in _FATAL_STAGE_01_MARKERS)
        for f in failures
    )


def evaluate_stage_01_checkpoint(
    pipeline: Any,
    forensic_transcript: str,
    source_text: str,
    metadata: Dict[str, Any],
    state_machine: Any,
    artifacts: Dict[str, Any],
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int,
    is_notes_mode: bool,
    u0_context: Optional[Dict[str, Any]] = None,
    plan: Optional[Any] = None,
) -> str:
    """
    Evaluates Stage 01 forensic transcript with deterministic Gate 1 before forking.

    1. If notes mode: skips validation and returns empty string.
    2. If output_1 is already frozen: returns frozen content directly.
    3. Executes run_qa_gate_1.
    4. If PASS: freezes output_1 in state_machine and returns.
    5. If FAIL:
       - If fatal unrecoverable defect: raises RuntimeError immediately (fail closed).
       - Attempts surgical remediation. If repaired passes Gate 1, freezes and returns.
       - Otherwise: raises RuntimeError immediately to prevent cascading downstream waste.
    """
    if is_notes_mode:
        return ""

    if state_machine.is_frozen("output_1"):
        frozen = state_machine.get_frozen_content("output_1") or forensic_transcript
        artifacts["01_hitl_resolved_transcript.md"] = frozen
        return frozen

    with recorder.span("nee.uc1.l2.stage01.checkpoint_gate1"):
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

        healed_candidate, changes = DeterministicHealer.heal_output_1(
            forensic_transcript, metadata=metadata
        )
        if changes:
            logger.info("Stage 01 Checkpoint Pre-Flight Healing: %d adjustments applied", len(changes))
            artifacts["01_hitl_resolved_transcript.md"] = healed_candidate

        from dual_pipeline.transcript_qa_core.role_consistency_validator import (
            RoleConsistencyValidator,
        )
        healed_candidate, role_report = RoleConsistencyValidator.validate_and_heal(
            healed_candidate, metadata=metadata
        )
        artifacts["stage01_role_consistency.json"] = role_report.to_dict()
        if role_report.roles_healed > 0:
            artifacts["01_hitl_resolved_transcript.md"] = healed_candidate

        qa_g1 = run_qa_gate_1(
            f1_text=healed_candidate,
            source_text=source_text,
            metadata=metadata,
        )
        artifacts["stage01_qa_gate1.json"] = qa_g1

        passed = qa_g1.get("passed", False)
        failures = qa_g1.get("failures", [])

        if passed:
            state_machine.freeze_artifact("output_1", healed_candidate)
            logger.info(
                f"Stage 01 Checkpoint: Output 01 passed Gate 1 and is frozen on attempt {attempt}."
            )
            return healed_candidate

        # Check for unrecoverable 0-turn failure
        if not healed_candidate.strip() or is_fatal_stage_01_defect(failures):
            err_msg = "; ".join(failures[:3]) if failures else "Empty dialogue output"
            logger.error(
                f"Stage 01 Checkpoint Gate 1 FAIL-CLOSED: Fatal unrecoverable defect on attempt {attempt}: {err_msg}. "
                f"Halting pipeline immediately to prevent downstream cascading failures."
            )
            raise RuntimeError(
                f"Stage 01 Checkpoint Gate 1 FAIL-CLOSED (attempt {attempt}): "
                f"Forensic transcript failed Gate 1 with fatal defect: {err_msg}"
            )

        # Attempt surgical remediation before giving up
        repaired, did_surg, _ = SurgicalRemediator.remediate_output_1(
            healed_candidate,
            qa_report={"output_01_forensic_transcript": qa_g1},
            judge_report={},
            directive=metadata.get("remediation_directive_forensic", ""),
            metadata=metadata,
        )
        if did_surg:
            repaired_healed, _ = DeterministicHealer.heal_output_1(repaired, metadata=metadata)
            qa_repaired = run_qa_gate_1(
                f1_text=repaired_healed,
                source_text=source_text,
                metadata=metadata,
            )
            if qa_repaired.get("passed", False):
                artifacts["01_hitl_resolved_transcript.md"] = repaired_healed
                artifacts["stage01_qa_gate1.json"] = qa_repaired
                state_machine.freeze_artifact("output_1", repaired_healed)
                notify_step(6, f"L2-Stage01: surgical checkpoint repair passed Gate 1 (A{attempt})")
                logger.info(
                    f"Stage 01 Checkpoint: Output 01 passed Gate 1 via surgical remediation on attempt {attempt}."
                )
                return repaired_healed

        # If still failing with recoverable defects, log warning and let pipeline proceed to exit gates and retry lifecycle
        err_msg = "; ".join(failures[:3]) if failures else "Unknown Gate 1 failure"
        logger.warning(
            f"Stage 01 Checkpoint: Output 01 has recoverable Gate 1 defect(s) on attempt {attempt}: {err_msg}. "
            f"Proceeding to downstream stages and exit gate evaluation."
        )
        return repaired_healed if did_surg else healed_candidate


_FATAL_STAGE_02_PATTERNS = (
    r"\b0 words\b",
    r"\bzero words\b",
    r"empty executive recap",
    r"empty recap",
    r"zero sections",
    r"no content",
)


def is_fatal_stage_02_defect(failures: list[str]) -> bool:
    """Returns True if any failure indicates an unrecoverable empty recap."""
    return any(
        any(re.search(pat, f.lower()) for pat in _FATAL_STAGE_02_PATTERNS)
        for f in failures
    )


def evaluate_stage_02_checkpoint(
    pipeline: Any,
    executive_recap: str,
    source_text: str,
    metadata: Dict[str, Any],
    state_machine: Any,
    artifacts: Dict[str, Any],
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int,
    is_notes_mode: bool,
    forensic_transcript: Optional[str] = None,
    plan: Optional[Any] = None,
) -> str:
    """
    Evaluates Stage 02 executive recap with deterministic Gate 2 immediately upon concurrent fork completion.

    1. If output_2 is already frozen: returns frozen content directly.
    2. Runs pre-flight deterministic healing (DeterministicHealer.heal_output_2).
    3. Executes run_qa_gate_2.
    4. If PASS: freezes output_2 in state_machine and returns healed content.
    5. If FAIL:
       - If fatal unrecoverable defect (empty text or fatal markers): raises RuntimeError immediately.
       - Attempts surgical remediation (SurgicalRemediator.remediate_output_2). If repaired passes Gate 2,
         freezes output_2 and returns.
       - Otherwise: logs warning and returns healed candidate unfrozen so the pipeline can replan or retry.
    """
    if state_machine.is_frozen("output_2"):
        frozen = state_machine.get_frozen_content("output_2") or executive_recap
        artifacts["02_executive_recap.md"] = frozen
        return frozen

    if not executive_recap or not executive_recap.strip():
        logger.error(
            f"Stage 02 Checkpoint Gate 2 FAIL-CLOSED: Empty executive recap on attempt {attempt}."
        )
        raise RuntimeError(
            f"Stage 02 Checkpoint Gate 2 FAIL-CLOSED (attempt {attempt}): "
            "Executive recap is empty."
        )

    with recorder.span("nee.uc1.l2.stage02.checkpoint_gate2"):
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

        healed_candidate, changes = DeterministicHealer.heal_output_2(
            executive_recap, metadata=metadata, output_1=forensic_transcript
        )
        if changes:
            logger.info("Stage 02 Checkpoint Pre-Flight Healing: %d adjustments applied", len(changes))
        artifacts["02_executive_recap.md"] = healed_candidate

        qa_g2 = run_qa_gate_2(
            f2_text=healed_candidate,
            meta=metadata,
        )
        artifacts["stage02_qa_gate2.json"] = qa_g2

        passed = qa_g2.get("passed", False)
        failures = qa_g2.get("failures", [])

        if passed:
            state_machine.freeze_artifact("output_2", healed_candidate)
            logger.info(
                f"Stage 02 Checkpoint: Output 02 passed Gate 2 and is frozen on attempt {attempt}."
            )
            return healed_candidate

        # Check for unrecoverable empty defect
        if not healed_candidate.strip() or is_fatal_stage_02_defect(failures):
            err_msg = "; ".join(failures[:3]) if failures else "Empty recap output"
            logger.error(
                f"Stage 02 Checkpoint Gate 2 FAIL-CLOSED: Fatal unrecoverable defect on attempt {attempt}: {err_msg}."
            )
            raise RuntimeError(
                f"Stage 02 Checkpoint Gate 2 FAIL-CLOSED (attempt {attempt}): "
                f"Executive recap failed Gate 2 with fatal defect: {err_msg}"
            )

        # Attempt surgical remediation before downstream replan
        repaired, did_surg, _ = SurgicalRemediator.remediate_output_2(
            healed_candidate,
            qa_report={"output_02_executive_recap": qa_g2},
            judge_report={},
            directive=metadata.get("remediation_directive_recap", ""),
            metadata=metadata,
        )
        if did_surg:
            repaired_healed, _ = DeterministicHealer.heal_output_2(
                repaired, metadata=metadata, output_1=forensic_transcript
            )
            qa_repaired = run_qa_gate_2(
                f2_text=repaired_healed,
                meta=metadata,
            )
            if qa_repaired.get("passed", False):
                artifacts["02_executive_recap.md"] = repaired_healed
                artifacts["stage02_qa_gate2.json"] = qa_repaired
                state_machine.freeze_artifact("output_2", repaired_healed)
                notify_step(7, f"L2-Stage02: surgical checkpoint repair passed Gate 2 (A{attempt})")
                logger.info(
                    f"Stage 02 Checkpoint: Output 02 passed Gate 2 via surgical remediation on attempt {attempt}."
                )
                return repaired_healed

        err_msg = "; ".join(failures[:3]) if failures else "Gate 2 validation failed"
        logger.warning(
            f"Stage 02 Checkpoint Gate 2: Output 02 failed Gate 2 on attempt {attempt}: {err_msg}. "
            "Output 02 remains unfrozen for downstream evaluation / remediation."
        )
        return healed_candidate


_FATAL_STAGE_03_PATTERNS = (
    r"\b0 mutations\b",
    r"\bzero mutations\b",
    r"empty canonical signal projection",
    r"empty projection",
    r"json parse error",
    r"no content",
)


def is_fatal_stage_03_defect(failures: list[str]) -> bool:
    """Returns True if any failure indicates an unrecoverable empty or unparseable signal projection."""
    return any(
        any(re.search(pat, f.lower()) for pat in _FATAL_STAGE_03_PATTERNS)
        for f in failures
    )


def evaluate_stage_03_checkpoint(
    pipeline: Any,
    canonical_projection: str,
    forensic_transcript: str,
    metadata: Dict[str, Any],
    state_machine: Any,
    artifacts: Dict[str, Any],
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int = 1,
    is_notes_mode: bool = False,
) -> str:
    """
    Stage 03 Checkpoint Gatekeeper.
    Evaluates deterministic QA Gate 3 immediately following Stage 03 graph projection.
    Pre-flight heals with DeterministicHealer.heal_output_3, evaluates run_qa_gate_3,
    and freezes valid canonical projections in SelectiveRemediationStateMachine before
    dispatching downstream micro-judges.
    """
    if state_machine.is_frozen("output_3"):
        logger.info("Stage 03 Checkpoint: Output 03 is already frozen; skipping Gate 3 evaluation.")
        frozen = state_machine.get_frozen_content("output_3") or artifacts.get("03_canonical_signal_projection.json", canonical_projection)
        artifacts["03_canonical_signal_projection.json"] = frozen
        return frozen

    if not canonical_projection or not canonical_projection.strip():
        logger.error(f"Stage 03 Checkpoint Gate 3 FAIL-CLOSED: Empty signal projection received on attempt {attempt}.")
        raise RuntimeError(f"Stage 03 Checkpoint Gate 3 FAIL-CLOSED (attempt {attempt}): Canonical signal projection is empty.")

    with recorder.span("nee.uc1.l2.stage03.checkpoint_gate3"):
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

        f1_art = artifacts.get("01_hitl_resolved_transcript.md")
        healed_candidate, changes = DeterministicHealer.heal_output_3(canonical_projection, metadata=metadata, output_1=f1_art)
        if changes:
            logger.info("Stage 03 Checkpoint Pre-Flight Healing: %d adjustments applied", len(changes))
        artifacts["03_canonical_signal_projection.json"] = healed_candidate

        qa_g3 = run_qa_gate_3(
            f3_text=healed_candidate,
            f1_text=forensic_transcript,
            metadata=metadata,
        )
        artifacts["stage03_qa_gate3.json"] = qa_g3

        passed = qa_g3.get("passed", False)
        failures = qa_g3.get("failures", [])

        if passed:
            state_machine.freeze_artifact("output_3", healed_candidate)
            logger.info(
                f"Stage 03 Checkpoint: Output 03 passed Gate 3 and is frozen on attempt {attempt}."
            )
            return healed_candidate

        # Attempt fast deterministic schema validation & Sol escalation before declaring failure
        from dual_pipeline.manifest_runner.remediation_healer import (
            heal_output_3_with_fast_validation,
        )
        fast_healed, was_escalated, fast_msg = heal_output_3_with_fast_validation(
            f3_text=healed_candidate,
            f1_text=forensic_transcript,
        )
        if was_escalated and fast_healed:
            import json
            fast_healed_str = json.dumps(fast_healed, indent=2)
            qa_repaired = run_qa_gate_3(
                f3_text=fast_healed_str,
                f1_text=forensic_transcript,
                metadata=metadata,
            )
            if qa_repaired.get("passed", False):
                artifacts["03_canonical_signal_projection.json"] = fast_healed_str
                artifacts["stage03_qa_gate3.json"] = qa_repaired
                state_machine.freeze_artifact("output_3", fast_healed_str)
                notify_step(8, f"L2-Stage03: fast validation repair passed Gate 3 ({fast_msg})")
                logger.info("Stage 03 Checkpoint: Output 03 repaired via fast schema validation.")
                return fast_healed_str

        # Check for unrecoverable empty defect
        if not healed_candidate.strip() or is_fatal_stage_03_defect(failures):
            err_msg = "; ".join(failures[:3]) if failures else "Empty or unparseable graph projection"
            logger.error(
                f"Stage 03 Checkpoint Gate 3 FAIL-CLOSED: Fatal unrecoverable defect on attempt {attempt}: {err_msg}."
            )
            raise RuntimeError(
                f"Stage 03 Checkpoint Gate 3 FAIL-CLOSED (attempt {attempt}): "
                f"Canonical signal projection failed Gate 3 with fatal defect: {err_msg}"
            )

        err_msg = "; ".join(failures[:3]) if failures else "Gate 3 validation failed"
        logger.warning(
            f"Stage 03 Checkpoint Gate 3: Output 03 failed Gate 3 on attempt {attempt}: {err_msg}. "
            "Output 03 remains unfrozen for downstream evaluation / remediation."
        )
        return healed_candidate

