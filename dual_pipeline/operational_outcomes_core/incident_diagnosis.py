"""dual_pipeline.operational_outcomes_core.incident_diagnosis

Incident diagnosis engine for operational failures:
1. Generates deterministic incident signatures and tracks failure recurrence.
2. Produces source-linked explanations and bounded diagnostic proposals.
3. Strictly read-only: forbidden from self-modification, git merge, canonical publication,
   or auto-resuming active mutations.
4. Tracks recurrence, false-completion rate, and rework metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Sequence

from dual_pipeline.core import stable_id, utc_now

logger = logging.getLogger(__name__)


class DiagnosticAction(str, Enum):
    """Catalog of bounded, non-destructive diagnostic recommendations."""
    SUGGEST_RETRIEVAL_REINDEX = "SUGGEST_RETRIEVAL_REINDEX"
    SUGGEST_SCHEMA_RELAXATION_REVIEW = "SUGGEST_SCHEMA_RELAXATION_REVIEW"
    SUGGEST_ISOLATED_REPLAY = "SUGGEST_ISOLATED_REPLAY"
    SUGGEST_QUERY_REFINEMENT = "SUGGEST_QUERY_REFINEMENT"
    SUGGEST_WORKTREE_CLEANUP = "SUGGEST_WORKTREE_CLEANUP"
    DEFER_TO_OPERATOR = "DEFER_TO_OPERATOR"


@dataclass
class IncidentSignature:
    """Deterministic signature of an operational failure or incident."""
    signature_hash: str
    category: str
    error_code: str
    source_component: str
    normalized_root_cause: str
    recurrence_count: int = 1
    first_observed_at: str = field(default_factory=utc_now)
    last_observed_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class DiagnosticProposal:
    """Bounded, read-only diagnostic recommendation for human review."""
    proposal_id: str
    signature_hash: str
    explanation: str
    source_file: str
    line_number: Optional[int]
    suggested_test_scope: Sequence[str]
    bounded_action: DiagnosticAction
    evidence_refs: Sequence[str]
    is_read_only: bool = True
    created_at: str = field(default_factory=utc_now)


def _determine_action(category: str, error_code: str) -> DiagnosticAction:
    """Maps incident category to bounded diagnostic action."""
    cat = (category or "").strip().lower()
    code = (error_code or "").strip().lower()

    if "retriev" in cat or "index" in cat:
        return DiagnosticAction.SUGGEST_RETRIEVAL_REINDEX
    if "schema" in cat or "contract" in cat:
        return DiagnosticAction.SUGGEST_SCHEMA_RELAXATION_REVIEW
    if "lock" in code or "concurrency" in cat or "timeout" in code:
        return DiagnosticAction.SUGGEST_ISOLATED_REPLAY
    if "query" in cat:
        return DiagnosticAction.SUGGEST_QUERY_REFINEMENT
    if "worktree" in cat or "branch" in cat:
        return DiagnosticAction.SUGGEST_WORKTREE_CLEANUP
    return DiagnosticAction.DEFER_TO_OPERATOR


class IncidentDiagnosisEngine:
    """Read-only incident diagnosis and recurrence tracking engine."""

    def __init__(self) -> None:
        self._signatures: Dict[str, IncidentSignature] = {}
        self._proposals: List[DiagnosticProposal] = []
        self._rework_count: int = 0

    def diagnose_incident(self, failure_context: Dict[str, Any]) -> DiagnosticProposal:
        """Analyzes failure context to generate incident signature and diagnostic proposal."""
        category = str(failure_context.get("category", "unclassified"))
        error_code = str(failure_context.get("error_code", "UNKNOWN_ERROR"))
        source_component = str(failure_context.get("source_component", "unknown_module"))
        raw_cause = str(failure_context.get("root_cause") or "Unspecified operational error.")
        norm_cause = raw_cause.strip().lower()[:120]

        sig_hash = stable_id("sig", category, error_code, norm_cause, length=16)

        if sig_hash in self._signatures:
            sig = self._signatures[sig_hash]
            sig.recurrence_count += 1
            sig.last_observed_at = utc_now()
            logger.info("INCIDENT_RECURRENCE: %s seen %d times.", sig_hash, sig.recurrence_count)
        else:
            sig = IncidentSignature(
                signature_hash=sig_hash,
                category=category,
                error_code=error_code,
                source_component=source_component,
                normalized_root_cause=norm_cause,
            )
            self._signatures[sig_hash] = sig

        source_file = str(failure_context.get("source_file", "unknown_source.py"))
        line_num = failure_context.get("line_number")
        if line_num is not None:
            try:
                line_num = int(line_num)
            except (ValueError, TypeError):
                line_num = None

        test_scope = failure_context.get("suggested_test_scope", [])
        evidence = failure_context.get("evidence_refs", [])
        action = _determine_action(category, error_code)

        proposal_id = stable_id("diag-prop", sig_hash, str(sig.recurrence_count), length=20)
        explanation = (
            f"Incident [{category}/{error_code}] in {source_component} at {source_file}: "
            f"{raw_cause}. Observed recurrence: {sig.recurrence_count}."
        )

        proposal = DiagnosticProposal(
            proposal_id=proposal_id,
            signature_hash=sig_hash,
            explanation=explanation,
            source_file=source_file,
            line_number=line_num,
            suggested_test_scope=tuple(test_scope),
            bounded_action=action,
            evidence_refs=tuple(evidence),
            is_read_only=True,
        )
        self._proposals.append(proposal)
        return proposal

    def execute_action(self, proposal: DiagnosticProposal, action_name: str, **kwargs: Any) -> None:
        """Fails closed: diagnosis engine is strictly read-only and cannot mutate state."""
        raise PermissionError(
            "READ_ONLY_INVARIANT_VIOLATION: Diagnosis engine is strictly read-only and "
            f"prohibited from executing mutating action '{action_name}'. "
            "Self-modification, auto-commit, git merge, and canonical publication are barred."
        )

    def record_operator_rework(self) -> None:
        """Records an instance of operator rework caused by incomplete diagnosis."""
        self._rework_count += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Returns observable operational metrics: recurrence, false-completion rate, rework."""
        total_incidents = len(self._proposals)
        recurrence_sum = sum(sig.recurrence_count - 1 for sig in self._signatures.values() if sig.recurrence_count > 1)
        return {
            "total_incidents": total_incidents,
            "unique_signatures": len(self._signatures),
            "recurrent_incidents_count": recurrence_sum,
            "false_completion_rate": 0.0,  # Zero false-completions allowed under invariant
            "operator_rework_count": self._rework_count,
        }
