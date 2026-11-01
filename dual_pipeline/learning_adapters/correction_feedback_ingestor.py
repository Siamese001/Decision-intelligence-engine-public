"""
dual_pipeline/learning_adapters/correction_feedback_ingestor.py

Transforms confirmed human operator corrections (speaker attributions, entity resolution overrides,
and quarantine reversals) into versioned, holdout-evaluated learning change proposals.
Enforces multi-confirmation thresholds, conflict detection, durable persistence, repeated
correction deduplication, operator retraction, and cross-engagement isolation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ROOT, stable_id
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)
from dual_pipeline.system_learning.holdout_replay_evaluator import (
    HoldoutCase,
    HoldoutEvaluationReport,
    HoldoutReplayEvaluator,
)

logger = logging.getLogger("dual_pipeline.learning_adapters.correction_ingestor")


@dataclass(frozen=True)
class OperatorCorrection:
    """A verified, human-operator correction captured across pipeline stages."""

    correction_id: str
    category: str
    source_identifier: str
    corrected_identifier: str
    operator_id: str
    session_id: Optional[str] = None
    engagement_id: Optional[str] = None
    rationale: str = ""
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CorrectionCohort:
    """An aggregated group of corrections for a single entity, speaker, or rule."""

    cohort_key: str
    category: str
    source_identifier: str
    target_identifier: str
    confirmations: int
    corrections: List[OperatorCorrection] = field(default_factory=list)
    has_conflicts: bool = False
    conflict_targets: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cohort_key": self.cohort_key,
            "category": self.category,
            "source_identifier": self.source_identifier,
            "target_identifier": self.target_identifier,
            "confirmations": self.confirmations,
            "has_conflicts": self.has_conflicts,
            "conflict_targets": self.conflict_targets,
            "corrections": [c.to_dict() for c in self.corrections],
        }


def _deduplicate_corrections(corrections: Sequence[OperatorCorrection]) -> List[OperatorCorrection]:
    """Deduplicates repeated corrections submitted by the same operator within a session."""
    seen: Set[Tuple[str, str, str]] = set()
    unique: List[OperatorCorrection] = []
    for c in corrections:
        key = (c.operator_id, c.session_id or "", c.corrected_identifier)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _build_cohort(
    cat: str,
    src_id: str,
    eng_id: Optional[str],
    corr_list: List[OperatorCorrection],
    min_conf: int,
) -> Optional[CorrectionCohort]:
    """Builds a single cohort from grouped corrections, detecting conflicts."""
    unique = _deduplicate_corrections(corr_list)
    targets = {c.corrected_identifier for c in unique}
    has_conflicts = len(targets) > 1
    most_frequent_target = max(targets, key=lambda t: sum(1 for c in unique if c.corrected_identifier == t))
    confirmations = sum(1 for c in unique if c.corrected_identifier == most_frequent_target)
    if confirmations < min_conf:
        return None

    eng_prefix = f"{eng_id}:" if eng_id else ""
    ckey = f"correction:{cat}:{eng_prefix}{src_id}"
    return CorrectionCohort(
        cohort_key=ckey,
        category=cat,
        source_identifier=src_id,
        target_identifier=most_frequent_target,
        confirmations=confirmations,
        corrections=unique,
        has_conflicts=has_conflicts,
        conflict_targets=sorted(list(targets)) if has_conflicts else [],
    )


class CorrectionFeedbackIngestor:
    """Ingests human operator corrections and synthesizes holdout-validated proposals."""

    def __init__(
        self,
        runtime_root: Optional[Path] = None,
        min_confirmations: int = 2,
        log_path: Optional[Path] = None,
    ) -> None:
        self.runtime_root = Path(runtime_root or ROOT)
        self.min_confirmations = max(1, min_confirmations)
        ledger_dir = self.runtime_root / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_path or (ledger_dir / "operator_corrections.jsonl")
        self._corrections: List[OperatorCorrection] = []
        self._retracted_ids: Set[str] = set()
        self._load_persisted_corrections()

    def _load_persisted_corrections(self) -> None:
        if not self.log_path.is_file():
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        d = json.loads(line_str)
                        if d.get("retracted"):
                            self._retracted_ids.add(d["correction_id"])
                        else:
                            self._corrections.append(
                                OperatorCorrection(
                                    correction_id=d["correction_id"],
                                    category=d["category"],
                                    source_identifier=d["source_identifier"],
                                    corrected_identifier=d["corrected_identifier"],
                                    operator_id=d["operator_id"],
                                    session_id=d.get("session_id"),
                                    engagement_id=d.get("engagement_id"),
                                    rationale=d.get("rationale", ""),
                                    recorded_at=d.get("recorded_at", datetime.now(timezone.utc).isoformat()),
                                )
                            )
                    except Exception:
                        continue
        except OSError as err:
            logger.warning("Failed to load corrections from %s: %s", self.log_path, err)

    def _persist_correction(self, corr: OperatorCorrection) -> None:
        try:
            line = json.dumps(corr.to_dict(), sort_keys=True) + "\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as err:
            logger.warning("Failed to persist operator correction: %s", err)

    def retract_correction(
        self,
        correction_id: str,
        operator_id: str,
        rationale: str = "Operator retracted correction",
    ) -> bool:
        """Retracts a previously recorded correction, excluding it from future cohorts."""
        self._retracted_ids.add(correction_id)
        try:
            retract_entry = {
                "correction_id": correction_id,
                "retracted": True,
                "retracted_by": operator_id,
                "retraction_rationale": rationale,
                "retracted_at": datetime.now(timezone.utc).isoformat(),
            }
            line = json.dumps(retract_entry, sort_keys=True) + "\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
            return True
        except OSError as err:
            logger.warning("Failed to persist retraction: %s", err)
            return False

    def record_speaker_correction(
        self,
        raw_speaker: str,
        corrected_speaker: str,
        operator_id: str,
        session_id: Optional[str] = None,
        engagement_id: Optional[str] = None,
        rationale: str = "Operator confirmed speaker attribution",
    ) -> OperatorCorrection:
        """Captures a human-verified speaker attribution correction."""
        cid = stable_id("corr-spk", raw_speaker, corrected_speaker, str(len(self._corrections)), length=24)
        corr = OperatorCorrection(
            correction_id=cid,
            category="speaker_attribution",
            source_identifier=raw_speaker.strip(),
            corrected_identifier=corrected_speaker.strip(),
            operator_id=operator_id.strip(),
            session_id=session_id,
            engagement_id=engagement_id,
            rationale=rationale,
        )
        self._corrections.append(corr)
        self._persist_correction(corr)
        return corr

    def record_entity_override(
        self,
        ambiguous_entity: str,
        canonical_entity: str,
        operator_id: str,
        session_id: Optional[str] = None,
        engagement_id: Optional[str] = None,
        rationale: str = "Operator confirmed canonical identity resolution",
    ) -> OperatorCorrection:
        """Captures a human-verified entity disambiguation or merge override."""
        cid = stable_id("corr-ent", ambiguous_entity, canonical_entity, str(len(self._corrections)), length=24)
        corr = OperatorCorrection(
            correction_id=cid,
            category="entity_resolution",
            source_identifier=ambiguous_entity.strip(),
            corrected_identifier=canonical_entity.strip(),
            operator_id=operator_id.strip(),
            session_id=session_id,
            engagement_id=engagement_id,
            rationale=rationale,
        )
        self._corrections.append(corr)
        self._persist_correction(corr)
        return corr

    def record_phonetic_drift_candidate(
        self,
        asr_token: str,
        canonical_token: str,
        operator_id: str,
        session_id: Optional[str] = None,
        engagement_id: Optional[str] = None,
        rationale: str = "Learned from speaker hint phonetic alignment",
    ) -> OperatorCorrection:
        """Captures an ASR phonetic drift candidate pair from a verified operator quote hint."""
        cid = stable_id("corr-phon", asr_token, canonical_token, str(len(self._corrections)), length=24)
        corr = OperatorCorrection(
            correction_id=cid,
            category="phonetic_alias",
            source_identifier=asr_token.strip(),
            corrected_identifier=canonical_token.strip(),
            operator_id=operator_id.strip(),
            session_id=session_id,
            engagement_id=engagement_id,
            rationale=rationale,
        )
        self._corrections.append(corr)
        self._persist_correction(corr)
        return corr

    def record_quarantine_reversal(
        self,
        quarantined_record_id: str,
        operator_id: str,
        session_id: Optional[str] = None,
        engagement_id: Optional[str] = None,
        rationale: str = "Operator cleared false-positive security quarantine",
    ) -> OperatorCorrection:
        """Captures a human-verified quarantine false-positive reversal."""
        cid = stable_id("corr-quar", quarantined_record_id, str(len(self._corrections)), length=24)
        corr = OperatorCorrection(
            correction_id=cid,
            category="quarantine_reversal",
            source_identifier=quarantined_record_id.strip(),
            corrected_identifier="CLIENT_SAFE",
            operator_id=operator_id.strip(),
            session_id=session_id,
            engagement_id=engagement_id,
            rationale=rationale,
        )
        self._corrections.append(corr)
        self._persist_correction(corr)
        return corr

    def aggregate_cohorts(
        self,
        min_confirmations: Optional[int] = None,
        engagement_id: Optional[str] = None,
    ) -> List[CorrectionCohort]:
        """Groups active corrections into cohorts, detecting conflicts and deduplicating repeats."""
        min_conf = min_confirmations if min_confirmations is not None else self.min_confirmations
        active = [
            c for c in self._corrections
            if c.correction_id not in self._retracted_ids
            and (engagement_id is None or c.engagement_id == engagement_id)
        ]
        groups: Dict[Tuple[str, str, Optional[str]], List[OperatorCorrection]] = {}
        for c in active:
            groups.setdefault((c.category, c.source_identifier, c.engagement_id), []).append(c)

        cohorts: List[CorrectionCohort] = []
        for (cat, src_id, eng_id), corr_list in groups.items():
            ch = _build_cohort(cat, src_id, eng_id, corr_list, min_conf)
            if ch is not None:
                cohorts.append(ch)
        return sorted(cohorts, key=lambda c: c.confirmations, reverse=True)

    def synthesize_proposals(
        self,
        evaluator: Optional[HoldoutReplayEvaluator] = None,
        holdout_cases: Optional[Sequence[HoldoutCase]] = None,
        min_confirmations: Optional[int] = None,
        engagement_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[HoldoutEvaluationReport]]:
        """Synthesizes signed, holdout-evaluated learning proposals from verified cohorts."""
        cohorts = self.aggregate_cohorts(min_confirmations=min_confirmations, engagement_id=engagement_id)
        proposals: List[Dict[str, Any]] = []
        evaluation_reports: List[HoldoutEvaluationReport] = []
        now = datetime.now(timezone.utc).isoformat()

        for ch in cohorts:
            if ch.has_conflicts:
                logger.warning("Skipping cohort %s: Conflicting targets %s", ch.cohort_key, ch.conflict_targets)
                continue

            p_id = stable_id("learning-proposal", "correction", ch.cohort_key, length=24)
            evidence_events = [c.correction_id for c in ch.corrections]
            run_ids = sorted(list({c.session_id for c in ch.corrections if c.session_id})) or ["session-operator-hitl"]
            proposed_change = f"Register learned alias '{ch.source_identifier}' -> '{ch.target_identifier}' ({ch.confirmations} operator confirmations)."

            ext: Dict[str, Any] = {
                "com.partner_advisory.nee.learning": {
                    "cohort_key": ch.cohort_key,
                    "category": ch.category,
                    "target_type": "learned_aliases",
                    "from_id": ch.source_identifier,
                    "to_id": ch.target_identifier,
                    "confirmations": ch.confirmations,
                }
            }
            replay_def = {
                "evaluator": "HoldoutReplayEvaluator.evaluate_proposal",
                "cohort_key": ch.cohort_key,
                "min_confirmations": ch.confirmations,
                "simulated_action": proposed_change,
            }
            prov = build_signed_provenance(
                proposal_id=p_id,
                evidence_event_ids=evidence_events,
                run_ids=run_ids,
                proposed_change=proposed_change,
                replay_definition=replay_def,
            )
            ext[PROVENANCE_EXTENSION_KEY] = prov

            prop = create_artifact(
                "learning-change-proposal",
                p_id,
                proposal_id=p_id,
                run_ids=run_ids,
                proposal_type="alias_addition",
                status="proposed",
                problem=f"Confirmed operator corrections for {ch.source_identifier} ({ch.category}).",
                evidence_event_ids=evidence_events,
                proposed_change=proposed_change,
                evaluation_plan=["Holdout replay accuracy verification", "False merge regression audit"],
                risk="low",
                approval_required=True,
                auto_apply=False,
                created_at=now,
                extensions=ext,
            )

            if evaluator is not None and holdout_cases:
                report = evaluator.evaluate_proposal(prop, holdout_cases)
                evaluation_reports.append(report)
                if report.verdict == "REJECT":
                    logger.warning("Proposal %s rejected by holdout evaluation: %s", p_id, report.rejection_reasons)
                    continue

            proposals.append(prop)

        return proposals, evaluation_reports
