"""
dual_pipeline/remediation_core/state_machine.py

Selective Remediation & Retry State Machine.
Preserves passing artifacts across attempts, enforces monotonic score progression,
and directs selective retries for failing stages with targeted diff injection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
from dual_pipeline.remediation_core.monotonic_governor import BestCandidateLedger, CandidateRecord

logger = logging.getLogger(__name__)

PRODUCTION_PASS_THRESHOLD: int = CANONICAL_RELEASE_POLICY.output1_pass_threshold
OUTPUT2_PRODUCTION_PASS_THRESHOLD: int = CANONICAL_RELEASE_POLICY.output2_pass_threshold
OUTPUT3_PRODUCTION_PASS_THRESHOLD: int = CANONICAL_RELEASE_POLICY.output3_pass_threshold
from .stage_sealer import (
    apply_terminal_rollback,
    sync_candidate_to_artifacts_and_disk,
)
from .stagnation_detector import (
    build_stagnant_diagnosis,
    compute_failure_signature as _compute_sig,
    record_regression_healing_event,
)


class SelectiveRemediationStateMachine:
    """State machine governing selective retries, artifact freezing, and monotonic score progression."""

    def __init__(self, max_attempts: int = 3, fail_fast: bool = False, checkpoint_dir: Optional[Path | str] = None) -> None:
        self.max_attempts = 1 if fail_fast else max_attempts
        self.current_attempt: int = 1
        self.frozen_artifacts: Dict[str, str] = {}
        self.frozen_manifests: Dict[str, Any] = {}
        self.permanently_frozen: Set[str] = set()
        self.passing_stages: Set[str] = set()
        self.ledger: BestCandidateLedger = BestCandidateLedger()
        self.failure_signatures: List[str] = []
        self.stagnant_failure_detected: bool = False
        self.stagnant_failure_diagnosis: Optional[Dict[str, Any]] = None
        self.checkpoint_dir: Optional[Path] = Path(checkpoint_dir) if checkpoint_dir else None

    def seed_from_output_dir(self, output_dir: str, meeting_name: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Seed frozen passing artifacts from prior run in output_dir if present and passing."""
        p = Path(output_dir)
        if not p.is_dir():
            return
        from .stage_sealer import build_slug_candidates, seed_single_artifact_from_dir

        slug_candidates = build_slug_candidates(meeting_name)
        specs = [("output_1", "01_", ".md", 95), ("output_2", "02_", ".md", 90), ("output_3", "03_", ".json", 90)]
        for art_key, pref, suff, def_score in specs:
            res = seed_single_artifact_from_dir(p, slug_candidates, art_key, pref, suff, metadata=metadata)
            if res:
                k, content = res
                self.freeze_artifact(k, content)
                self.passing_stages.add(k)
                if hasattr(self, "record_candidate"):
                    self.record_candidate(k, content, def_score, verdict="PASS", qa_passed=True, deterministic_gates_pass=True, full_scorecard={"composite_score": def_score, "verdict": "PASS", "reason": "FROZEN_PRIOR_ARTIFACT"})
                logger.info(f"Seeded candidate {k} from prior output directory.")

    def seed_for_selective_regeneration(
        self,
        output_dir: str | Path,
        regenerate_spec: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Seeds and freezes artifacts excluded from the --regenerate deliverable list."""
        from .stage_sealer import seed_selective_regeneration

        return seed_selective_regeneration(self, output_dir, regenerate_spec, metadata)


    def register_deliverable_pass(
        self,
        artifact_key: str,
        content: str,
        score: Optional[float | int] = None,
        checkpoint_dir: Optional[Path | str] = None,
        deterministic_gates_pass: bool = True,
    ) -> None:
        """Monotonically registers a passing deliverable and freezes it permanently."""
        self.freeze_artifact(artifact_key, content)
        self.permanently_frozen.add(artifact_key)
        self.passing_stages.add(artifact_key)

        threshold = OUTPUT2_PRODUCTION_PASS_THRESHOLD if artifact_key == "output_2" else (OUTPUT3_PRODUCTION_PASS_THRESHOLD if artifact_key == "output_3" else PRODUCTION_PASS_THRESHOLD)
        resolved_score = int(score) if score is not None else threshold

        self.record_candidate(
            artifact_key=artifact_key,
            content=content,
            composite_score=resolved_score,
            verdict="PASS",
            qa_passed=True,
            deterministic_gates_pass=deterministic_gates_pass,
        )

        if checkpoint_dir is not None:
            from .stage_sealer import persist_stage_checkpoint_seal

            persist_stage_checkpoint_seal(checkpoint_dir, artifact_key, content)

        logger.info(f"Monotonically registered passing deliverable '{artifact_key}' (score={resolved_score}, permanently_frozen=True).")

    def freeze_artifact(self, artifact_key: str, content: str, manifest: Optional[Any] = None, **kwargs: Any) -> None:
        """Freezes a passing artifact in memory so it is preserved across retries."""
        self.frozen_artifacts[artifact_key] = content
        if manifest is not None:
            self.frozen_manifests[artifact_key] = manifest
        threshold = OUTPUT2_PRODUCTION_PASS_THRESHOLD if artifact_key == "output_2" else (OUTPUT3_PRODUCTION_PASS_THRESHOLD if artifact_key == "output_3" else PRODUCTION_PASS_THRESHOLD)
        logger.info(f"Artifact '{artifact_key}' frozen with score >= {threshold}.")

    def unfreeze_artifact(self, artifact_key: str, force: bool = False) -> None:
        """Unfreezes an artifact so it will be regenerated in subsequent retries."""
        if artifact_key in self.permanently_frozen and not force:
            logger.info(f"Artifact '{artifact_key}' is permanently frozen (selective regeneration); skipping unfreeze.")
            return
        self.permanently_frozen.discard(artifact_key)
        self.frozen_artifacts.pop(artifact_key, None)
        self.frozen_manifests.pop(artifact_key, None)
        self.passing_stages.discard(artifact_key)
        logger.info(f"Artifact '{artifact_key}' unfrozen for remediation.")

    def is_frozen(self, artifact_key: str) -> bool:
        """Checks if an artifact is already frozen from a prior passing attempt."""
        return artifact_key in self.frozen_artifacts

    def get_frozen_content(self, artifact_key: str) -> Optional[str]:
        """Retrieves frozen artifact content if present."""
        return self.frozen_artifacts.get(artifact_key)

    def get_frozen_sha256(self, artifact_key: str) -> Optional[str]:
        """Returns the SHA-256 digest of the frozen artifact content if present."""
        content = self.get_frozen_content(artifact_key)
        return hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None

    def record_candidate(
        self, artifact_key: str, content: str, composite_score: int, verdict: str = "FAIL",
        dimension_scores: Optional[Dict[str, Any]] = None, qa_passed: bool = True,
        deterministic_gates_pass: bool = True, attempt: Optional[int] = None,
        full_scorecard: Optional[Dict[str, Any]] = None, epoch_id: Optional[str] = None,
    ) -> CandidateRecord:
        """Registers a candidate in the best candidate ledger for monotonic tracking."""
        att = attempt if attempt is not None else self.current_attempt
        return self.ledger.record_candidate(
            artifact_key=artifact_key,
            attempt=att,
            content=content,
            composite_score=composite_score,
            verdict=verdict,
            dimension_scores=dimension_scores,
            qa_passed=qa_passed,
            deterministic_gates_pass=deterministic_gates_pass,
            full_scorecard=full_scorecard,
            epoch_id=epoch_id,
        )

    def get_best_candidate(self, artifact_key: str) -> Optional[CandidateRecord]:
        """Returns the CandidateRecord with highest score for artifact_key."""
        return self.ledger.get_best_candidate(artifact_key)

    def get_best_candidate_content(self, artifact_key: str) -> Optional[str]:
        """Returns content of the best candidate or frozen content."""
        return self.ledger.get_best_content(artifact_key) or self.get_frozen_content(artifact_key)

    def get_prior_candidate(self, artifact_key: str) -> Optional[str]:
        """
        Returns the content of the candidate from the previous attempt (or best candidate)
        for targeted differential patching.
        """
        if self.has_regressed(artifact_key):
            return self.get_best_candidate_content(artifact_key)
        latest = self.ledger.get_latest_candidate(artifact_key)
        return latest.content if (latest and latest.content) else self.get_best_candidate_content(artifact_key)

    def get_high_water_mark_score(self, artifact_key: str) -> int:
        """Returns highest score recorded for artifact_key."""
        return self.ledger.get_high_water_mark_score(artifact_key)

    def has_regressed(self, artifact_key: str) -> bool:
        """Checks if artifact score regressed in the latest attempt."""
        return self.ledger.has_regressed(artifact_key)

    def restore_high_water_mark(self, artifact_key: str) -> Optional[str]:
        """Restores high-water mark candidate content and re-freezes in memory."""
        res = self.ledger.restore_best_candidate(artifact_key)
        if res:
            content, _ = res
            self.frozen_artifacts[artifact_key] = content
            return content
        return None

    def _extract_artifact_content(
        self,
        artifacts: Optional[Dict[str, str]],
        key: str,
        filename_pattern: str,
    ) -> Optional[str]:
        if not artifacts:
            return self.frozen_artifacts.get(key)
        if key in artifacts:
            return artifacts[key]
        for k, v in artifacts.items():
            if filename_pattern in k:
                return v
        return self.frozen_artifacts.get(key)

    def _record_attempt_candidates(
        self, final_decision: Dict[str, Any], judge_report: Dict[str, Any],
        qa_report: Dict[str, Any], c1: Optional[str], c2: Optional[str], c3: Optional[str],
    ) -> None:
        evals = [
            ("output_1", c1, judge_report.get("output_1_evaluation", {}), qa_report.get("output_01_forensic_transcript", {}).get("passed", False), final_decision.get("output_1_deterministic_gates_pass", qa_report.get("output_01_forensic_transcript", {}).get("passed", True))),
            ("output_2", c2, judge_report.get("output_2_evaluation", {}), qa_report.get("output_02_executive_recap", {}).get("passed", False), final_decision.get("output_2_deterministic_gates_pass", qa_report.get("output_02_executive_recap", {}).get("passed", True))),
            ("output_3", c3, judge_report.get("output_3_evaluation", {}), qa_report.get("output_03_canonical_signal_projection", {}).get("passed", False), final_decision.get("output_3_deterministic_gates_pass", qa_report.get("output_03_canonical_signal_projection", {}).get("passed", True))),
        ]
        for key, content, ev, qa_p, det_p in evals:
            if content and content.strip():
                score, verdict = self._resolve_eval_score(key, ev)
                self.record_candidate(
                    key, content, score, verdict=verdict, dimension_scores=ev.get("dimension_scores"),
                    qa_passed=qa_p, deterministic_gates_pass=det_p, full_scorecard=ev,
                )

    def _resolve_eval_score(self, key: str, ev: Dict[str, Any]) -> Tuple[int, str]:
        score, verdict = int(ev.get("composite_score") or 0), ev.get("verdict", "FAIL")
        is_frozen = self.is_frozen(key)
        if score == 0 and (ev.get("reason") == "FROZEN_PRIOR_ARTIFACT" or is_frozen):
            from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
            hwm = self.get_high_water_mark_score(key)
            score = int(hwm) if hwm > 0 else (CANONICAL_RELEASE_POLICY.output1_pass_threshold if key == "output_1" else 90)
            if verdict == "FAIL" and is_frozen:
                verdict = "PASS"
        return score, verdict

    def invalidate_epoch(self, new_epoch_id: str, reason: str) -> None:
        """Invalidates evaluation epoch across ledger and frozen artifacts."""
        self.ledger.invalidate_epoch(new_epoch_id, reason)
        self.frozen_artifacts.clear()
        self.frozen_manifests.clear()
        self.passing_stages.clear()

    def get_accepted_trajectory(self, artifact_key: str) -> List[int]:
        """Returns non-decreasing trajectory of accepted composite scores."""
        return self.ledger.get_accepted_trajectory(artifact_key)

    def _restore_if_regressed(self, key: str, pat: str, artifacts: Optional[Dict[str, str]]) -> None:
        if not self.has_regressed(key):
            return
        logger.warning(f"MONOTONIC INVARIANT VIOLATION: {key} score regressed on Attempt {self.current_attempt}. Restoring best candidate.")
        restored = self.restore_high_water_mark(key)
        sync_candidate_to_artifacts_and_disk(key, pat, restored, artifacts, self.checkpoint_dir)
        record_regression_healing_event(key, self.current_attempt, restored, self.get_high_water_mark_score(key))

    def _check_monotonic_regressions(self, artifacts: Optional[Dict[str, str]]) -> None:
        self._restore_if_regressed("output_1", "01_", artifacts)
        self._restore_if_regressed("output_2", "02_", artifacts)
        self._restore_if_regressed("output_3", "03_", artifacts)

    def _update_stage_freeze_state(
        self,
        key: str,
        is_pass: bool,
        content: Optional[str],
        failed_stages: Set[str],
    ) -> None:
        if not is_pass:
            if key in self.permanently_frozen:
                logger.info(f"Stage '{key}' is permanently frozen; preserving pass state.")
                self.passing_stages.add(key)
                return
            failed_stages.add(key)
            self.unfreeze_artifact(key)
        else:
            self.passing_stages.add(key)
            if content is not None:
                self.freeze_artifact(key, content)

    def _apply_stage_transitions(
        self,
        o1_pass: bool,
        o2_pass: bool,
        o3_pass: bool,
        c1: Optional[str],
        c2: Optional[str],
        c3: Optional[str],
        failed_stages: Set[str],
        is_notes_mode: bool,
    ) -> None:
        if not is_notes_mode:
            self._update_stage_freeze_state("output_1", o1_pass, c1, failed_stages)
        self._update_stage_freeze_state("output_2", o2_pass, c2, failed_stages)
        self._update_stage_freeze_state("output_3", o3_pass, c3, failed_stages)

    def _evaluate_stage_pass_flags(
        self, scores: Tuple[int, int, int], verdicts: Tuple[str, str, str],
        qa_passes: Tuple[bool, bool, bool], recon_pass: bool, is_notes_mode: bool,
        final_decision: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, bool, bool]:
        s1, s2, s3 = scores
        v1, v2, v3 = verdicts
        q1_pass, q2_pass, q3_pass = qa_passes
        fd = final_decision or {}
        o1_pass = True if is_notes_mode else (
            "output_1" in self.permanently_frozen or "output_1" in self.passing_stages or bool(fd.get("output_1_pass"))
            or (q1_pass and (v1 == "PASS_AUTHORIZED_EXCEPTION" or (s1 >= PRODUCTION_PASS_THRESHOLD and v1 in ("PASS", "PASS_ADVISORY"))))
        )
        o2_pass = (
            "output_2" in self.permanently_frozen or "output_2" in self.passing_stages or bool(fd.get("output_2_pass"))
            or (q2_pass and (v2 == "PASS_AUTHORIZED_EXCEPTION" or (s2 >= OUTPUT2_PRODUCTION_PASS_THRESHOLD and v2 in ("PASS", "PASS_ADVISORY", "HITL_REQUIRED"))))
        )
        o3_pass = (
            "output_3" in self.permanently_frozen or "output_3" in self.passing_stages or bool(fd.get("output_3_pass"))
            or (q3_pass and recon_pass and (v3 == "PASS_AUTHORIZED_EXCEPTION" or (s3 >= OUTPUT3_PRODUCTION_PASS_THRESHOLD and v3 in ("PASS", "PASS_ADVISORY"))))
        )
        return o1_pass, o2_pass, o3_pass

    def _register_newly_passing_artifacts(
        self,
        passes: Tuple[bool, bool, bool],
        contents: Tuple[Optional[str], Optional[str], Optional[str]],
        scores: Tuple[int, int, int],
    ) -> None:
        keys = ("output_1", "output_2", "output_3")
        for key, passed, content, score in zip(keys, passes, contents, scores):
            if passed and content and key not in self.permanently_frozen:
                self.register_deliverable_pass(key, content, score=score, checkpoint_dir=self.checkpoint_dir)

    def evaluate_attempt(
        self, final_decision: Dict[str, Any], judge_report: Dict[str, Any], qa_report: Dict[str, Any],
        artifacts: Optional[Dict[str, str]] = None, is_notes_mode: bool = False,
    ) -> Tuple[bool, Set[str]]:
        """
        Evaluates current attempt against strict production release criteria.
        Registers candidate artifacts in monotonic ledger and intercepts regressions.
        """
        failed_stages: Set[str] = set()

        o1_eval = judge_report.get("output_1_evaluation", {})
        o2_eval = judge_report.get("output_2_evaluation", {})
        o3_eval = judge_report.get("output_3_evaluation", {})

        s1 = int(o1_eval.get("composite_score") or 0)
        s2 = int(o2_eval.get("composite_score") or 0)
        s3 = int(o3_eval.get("composite_score") or 0)
        v1, v2, v3 = o1_eval.get("verdict", "FAIL"), o2_eval.get("verdict", "FAIL"), o3_eval.get("verdict", "FAIL")
        q1_pass = qa_report.get("output_01_forensic_transcript", {}).get("passed", True)
        q2_pass = qa_report.get("output_02_executive_recap", {}).get("passed", True)
        q3_pass = qa_report.get("output_03_canonical_signal_projection", {}).get("passed", True)
        recon_pass = judge_report.get("cross_artifact_reconciliation", {}).get("passed", True)

        o1_pass, o2_pass, o3_pass = self._evaluate_stage_pass_flags(
            (s1, s2, s3), (v1, v2, v3), (q1_pass, q2_pass, q3_pass), recon_pass, is_notes_mode, final_decision
        )

        c1 = None if is_notes_mode else self._extract_artifact_content(artifacts, "output_1", "01_")
        c2 = self._extract_artifact_content(artifacts, "output_2", "02_")
        c3 = self._extract_artifact_content(artifacts, "output_3", "03_")

        self._register_newly_passing_artifacts((o1_pass, o2_pass, o3_pass), (c1, c2, c3), (s1, s2, s3))

        self._record_attempt_candidates(final_decision, judge_report, qa_report, c1, c2, c3)
        self._check_monotonic_regressions(artifacts)
        self._apply_stage_transitions(o1_pass, o2_pass, o3_pass, c1, c2, c3, failed_stages, is_notes_mode)

        all_passed = len(failed_stages) == 0 and final_decision.get("status", "PASS") in ("PASS", "PASS_AUTHORIZED_EXCEPTION")

        if not all_passed:
            sig = self.compute_failure_signature(failed_stages, qa_report, judge_report, final_decision)
            if sig in self.failure_signatures:
                self.stagnant_failure_detected = True
                self.stagnant_failure_diagnosis = build_stagnant_diagnosis(self.current_attempt, sig, failed_stages)
                logger.warning(
                    "Monotonic progress guard: Identical failure signature %s detected twice. "
                    "Terminating remediation loop early.",
                    sig[:12],
                )
            self.failure_signatures.append(sig)
            if self.current_attempt >= self.max_attempts:
                apply_terminal_rollback(self, artifacts)

        logger.info(
            f"Attempt {self.current_attempt}/{self.max_attempts} evaluated: "
            f"all_passed={all_passed}, failed_stages={failed_stages}, "
            f"scores=(O1:{s1}, O2:{s2}, O3:{s3})"
        )
        return all_passed, failed_stages

    def compute_failure_signature(
        self, failed_stages: Set[str], qa_report: Dict[str, Any], judge_report: Dict[str, Any], final_decision: Dict[str, Any]
    ) -> str:
        """Computes a deterministic signature of failure modes to detect remediation stagnation."""
        return _compute_sig(failed_stages, qa_report, judge_report, final_decision)

    def should_retry(self) -> bool:
        """Determines whether another retry attempt is permitted."""
        if getattr(self, "stagnant_failure_detected", False):
            logger.warning("SelectiveRemediationStateMachine: Terminating retries early due to identical failure signature twice (remediation stagnation).")
            return False
        return self.current_attempt < self.max_attempts

    def get_stagnant_diagnosis(self) -> Optional[Dict[str, Any]]:
        """Returns diagnostic payload if remediation stagnation was detected."""
        return self.stagnant_failure_diagnosis

    def advance_attempt(self) -> int:
        """Advances attempt counter."""
        self.current_attempt += 1
        return self.current_attempt

    def compute_differential_patch_scope(self, qa_report: Dict[str, Any], judge_report: Dict[str, Any]) -> Dict[str, Any]:
        """Determines whether remediation can be achieved via targeted micro-patching."""
        from .diff_injector import compute_differential_patch_scope
        return compute_differential_patch_scope(qa_report, judge_report)


RemediationStateMachine = SelectiveRemediationStateMachine
