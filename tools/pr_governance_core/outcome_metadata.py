"""tools/pr_governance_core/outcome_metadata.py

Versioned outcome metadata models and evaluation rules for PR Queue Prioritization (Wave 4).
Enforces the invariant that code volume heuristics (LOC, test filenames, commit regexes)
are never presented as measured business value.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

OUTCOME_SCHEMA_VERSION = "1.0.0"
UNMEASURED_VALUE_STATEMENT = "UNKNOWN: Unmeasured business value (structural heuristics only)"


class BenefitCategory(str, Enum):
    COST_REDUCTION = "COST_REDUCTION"
    LATENCY_IMPROVEMENT = "LATENCY_IMPROVEMENT"
    GRAPH_INTEGRITY = "GRAPH_INTEGRITY"
    INTEGRITY_PROTECTION = "INTEGRITY_PROTECTION"
    FEATURE_AND_DELIVERABLE = "FEATURE_AND_DELIVERABLE"
    FEATURE_CAPABILITY = "FEATURE_CAPABILITY"
    CLIENT_DELIVERABLE = "CLIENT_DELIVERABLE"
    HOUSEKEEPING = "HOUSEKEEPING"
    UNKNOWN = "UNKNOWN"


@dataclass
class OwnerOverride:
    owner: str
    reason: str
    scope: str
    expires_at: str  # YYYY-MM-DD
    target_tier: Optional[str] = None

    def is_valid(self, today_str: Optional[str] = None) -> Tuple[bool, str]:
        """Validate override: requires owner, reason, scope, and non-expired expires_at."""
        if not self.owner or not self.owner.strip():
            return False, "Missing required override owner"
        if not self.reason or not self.reason.strip():
            return False, "Missing required override reason"
        if not self.scope or not self.scope.strip():
            return False, "Missing required override scope"
        if not self.expires_at or not self.expires_at.strip():
            return False, "Missing required override expires_at"
        try:
            exp_date = datetime.strptime(self.expires_at.strip(), "%Y-%m-%d").date()
        except ValueError:
            return False, f"Invalid expires_at format '{self.expires_at}' (expected YYYY-MM-DD)"

        cur_date = (
            datetime.strptime(today_str.strip(), "%Y-%m-%d").date()
            if today_str
            else datetime.now(timezone.utc).date()
        )
        if exp_date < cur_date:
            return False, f"Override expired on {self.expires_at} (current date: {cur_date})"
        return True, "valid"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OutcomeMetadata:
    version: str = OUTCOME_SCHEMA_VERSION
    branch: str = ""
    expected_benefit: str = "UNKNOWN"
    benefit_category: str = BenefitCategory.UNKNOWN.value
    evidence: str = "UNKNOWN"
    confidence: str = "UNKNOWN"  # HIGH, MEDIUM, LOW, UNKNOWN
    urgency: str = "MEDIUM"      # CRITICAL, HIGH, MEDIUM, LOW
    prerequisite_unlock: List[str] = field(default_factory=list)
    effort_risk: str = "MEDIUM"   # LOW, MEDIUM, HIGH, CRITICAL
    completion_evidence_requirement: str = "Passing Tier 2 readiness receipt and wave plan completion"
    override: Optional[OwnerOverride] = None
    itemized_value: Optional[str] = None
    measurement_status: str = "UNVERIFIED"
    measurement_evidence: Optional[str] = None
    criterion_score: float = 0.0

    def value_statement(self) -> str:
        """Derives a human-readable value statement.

        Measurement status is a separate typed field; file and receipt existence
        cannot convert an expected benefit into a measured outcome.
        """
        if self.measurement_status == "UNVERIFIED" and not self.itemized_value:
            return UNMEASURED_VALUE_STATEMENT
        if self.itemized_value:
            return self.itemized_value
        if self.expected_benefit in ("UNKNOWN", "", None):
            return UNMEASURED_VALUE_STATEMENT
        return self.expected_benefit

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["value_statement"] = self.value_statement()
        return res


from tools.pr_governance_core.value_itemizer import (
    CRITERION_WEIGHTS,
    classify_criterion_from_diff,
    clean_title,
    describes_benefit,
    existing_output_files,
    extract_itemized_value,
    extract_plan_intent,
    resolve_authoritative_plan,
)
from tools.pr_governance_core.value_measurement import load_measured_benefit


def evaluate_branch_outcome(
    branch: str,
    tier: str,
    changed_files: Sequence[str] = (),
    override_data: Optional[Dict[str, Any]] = None,
    today_str: Optional[str] = None,
    repo_root: Optional[Path] = None,
    evidence_links: Sequence[Dict[str, str]] = (),
    worktree_path: Optional[str] = None,
    branch_sha: str = "",
    base_sha: str = "",
) -> OutcomeMetadata:
    """Evaluates structured outcome metadata for a branch.

    Validates owner overrides (failing closed if expired or missing fields) and
    constructs an explicit value statement or UNKNOWN statement.
    """
    owner_override: Optional[OwnerOverride] = None
    if override_data and isinstance(override_data, dict):
        cand = OwnerOverride(
            owner=str(override_data.get("owner", "")),
            reason=str(override_data.get("reason") or override_data.get("rationale", "")),
            scope=str(override_data.get("scope", "")),
            expires_at=str(override_data.get("expires_at", "")),
            target_tier=override_data.get("tier"),
        )
        valid, _ = cand.is_valid(today_str=today_str)
        if valid:
            owner_override = cand

    itemized = extract_itemized_value(
        branch=branch,
        changed_files=changed_files,
        repo_root=repo_root,
        evidence_links=evidence_links,
        worktree_path=worktree_path,
        tier=tier,
    )
    measured = load_measured_benefit(branch, branch_sha, base_sha, repo_root, worktree_path)
    if measured:
        m_score = CRITERION_WEIGHTS.get(measured.category, 0.0)
        return OutcomeMetadata(
            branch=branch, expected_benefit=measured.statement,
            benefit_category=measured.category, evidence=str(measured.receipt_path),
            confidence="HIGH", itemized_value=measured.statement,
            measurement_status="MEASURED", measurement_evidence=str(measured.receipt_path),
            override=owner_override,
            criterion_score=m_score,
        )

    resolved_cat = getattr(itemized, "benefit_category", None)
    resolved_score = getattr(itemized, "criterion_score", 0.0)
    if not resolved_cat or resolved_cat == "UNKNOWN":
        resolved_cat, resolved_score = classify_criterion_from_diff(changed_files, desc=itemized, tier=tier)

    # Check for authentic client deliverable in output/
    output_files = existing_output_files(changed_files, repo_root, worktree_path)
    if output_files or tier.startswith("P2") or resolved_cat in ("FEATURE_AND_DELIVERABLE", "CLIENT_DELIVERABLE"):
        if output_files:
            return OutcomeMetadata(
                branch=branch,
                expected_benefit=f"Preserves {len(output_files)} client-deliverable artifact(s) in output/",
                benefit_category=BenefitCategory.CLIENT_DELIVERABLE.value,
                evidence=f"Output hierarchy verified ({len(output_files)} files)",
                confidence="HIGH",
                urgency="HIGH",
                prerequisite_unlock=[],
                effort_risk="LOW",
                completion_evidence_requirement="Output hierarchy compliance and sealed 01-03 signal projections",
                override=owner_override,
                itemized_value=itemized,
                measurement_status="EXPECTED" if describes_benefit(itemized) else "UNVERIFIED",
                criterion_score=CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"],
            )

    # Check for core integrity or latency path
    if tier.startswith("P0") or resolved_cat in ("COST_REDUCTION", "LATENCY_IMPROVEMENT"):
        cost_paths = ("token_optimization", "cost_tracker", "semantic_cache", "model_registry", "prompt_cache")
        is_cost = (resolved_cat == "COST_REDUCTION") or any(any(token in path.lower() for token in cost_paths) for path in changed_files)
        cat = BenefitCategory.COST_REDUCTION.value if is_cost else BenefitCategory.LATENCY_IMPROVEMENT.value
        score = CRITERION_WEIGHTS[cat]
        return OutcomeMetadata(
            branch=branch,
            expected_benefit=("Reduces paid model calls or token consumption" if is_cost else "Improves processing or retrieval latency"),
            benefit_category=cat,
            evidence="UNKNOWN",  # A linked benchmark is required to quantify the benefit.
            confidence="MEDIUM",
            urgency="HIGH",
            prerequisite_unlock=[],
            effort_risk="HIGH",
            completion_evidence_requirement="Tier 2 readiness receipt and latency benchmark comparison receipt",
            override=owner_override,
            itemized_value=itemized,
            measurement_status="EXPECTED" if describes_benefit(itemized) else "UNVERIFIED",
            criterion_score=score,
        )

    if tier.startswith("P1") or resolved_cat in ("GRAPH_INTEGRITY", "INTEGRITY_PROTECTION"):
        cat = BenefitCategory.INTEGRITY_PROTECTION.value
        score = CRITERION_WEIGHTS["GRAPH_INTEGRITY"]
        return OutcomeMetadata(
            branch=branch,
            expected_benefit="Knowledge graph schema, storage, or query integrity protection",
            benefit_category=cat,
            evidence="UNKNOWN",
            confidence="MEDIUM",
            urgency="MEDIUM",
            prerequisite_unlock=[],
            effort_risk="MEDIUM",
            completion_evidence_requirement="GraphDB zero-mutation receipt and hybrid retrieval evaluation pass",
            override=owner_override,
            itemized_value=itemized,
            measurement_status="EXPECTED" if describes_benefit(itemized) else "UNVERIFIED",
            criterion_score=score,
        )

    if resolved_cat in ("FEATURE_AND_DELIVERABLE", "FEATURE_CAPABILITY", "CLIENT_DELIVERABLE"):
        cat = BenefitCategory.CLIENT_DELIVERABLE.value
        score = CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"]
        return OutcomeMetadata(
            branch=branch,
            expected_benefit="Delivers platform capability or live meeting artifact",
            benefit_category=cat,
            evidence="UNKNOWN",
            confidence="MEDIUM",
            urgency="MEDIUM",
            prerequisite_unlock=[],
            effort_risk="LOW",
            completion_evidence_requirement="Output hierarchy compliance and sealed 01-03 signal projections",
            override=owner_override,
            itemized_value=itemized,
            measurement_status="EXPECTED" if describes_benefit(itemized) else "UNVERIFIED",
            criterion_score=score,
        )

    # Default P3 or housekeeping
    cat = BenefitCategory.HOUSEKEEPING.value if (tier.startswith("P3") or resolved_cat == "HOUSEKEEPING") else BenefitCategory.UNKNOWN.value
    score = CRITERION_WEIGHTS.get(cat, 0.0)
    return OutcomeMetadata(
        branch=branch,
        expected_benefit="UNKNOWN",
        benefit_category=cat,
        evidence="UNKNOWN",
        confidence="LOW",
        urgency="LOW",
        prerequisite_unlock=[],
        effort_risk="LOW",
        completion_evidence_requirement="Passing unit test suite and clean merge simulation",
        override=owner_override,
        itemized_value=itemized,
        measurement_status="EXPECTED" if describes_benefit(itemized) else "UNVERIFIED",
        criterion_score=score,
    )
