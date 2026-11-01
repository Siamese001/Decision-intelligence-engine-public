"""dual_pipeline.query_core.diagnostics

Implements requirement-level diagnostic records separating missing source evidence,
retrieval omission, policy restriction, and incorrectly constructed requirements.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .constants import _COVERAGE_OUTPUT_ONLY

logger = logging.getLogger(__name__)


class CoverageDiagnosticCategory(str, Enum):
    MISSING_SOURCE_EVIDENCE = "MISSING_SOURCE_EVIDENCE"
    RETRIEVAL_OMISSION = "RETRIEVAL_OMISSION"
    POLICY_RESTRICTION = "POLICY_RESTRICTION"
    MALFORMED_REQUIREMENT = "MALFORMED_REQUIREMENT"


class RequirementDisposition(str, Enum):
    GENUINE_GAP = "GENUINE_GAP"
    RECOVERABLE_VIA_SUPPLEMENTAL = "RECOVERABLE_VIA_SUPPLEMENTAL"
    FORMATTING_DIRECTIVE_EXCLUDED = "FORMATTING_DIRECTIVE_EXCLUDED"


@dataclass
class RequirementDiagnosticRecord:
    requirement_id: str
    requirement_label: str
    category: CoverageDiagnosticCategory
    disposition: RequirementDisposition
    root_cause_explanation: str
    is_defect: bool
    evidence_refs: list[str]
    remediation_code_delta: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement_label": self.requirement_label,
            "category": self.category.value,
            "disposition": self.disposition.value,
            "root_cause_explanation": self.root_cause_explanation,
            "is_defect": self.is_defect,
            "evidence_refs": list(self.evidence_refs),
            "remediation_code_delta": self.remediation_code_delta,
        }


def diagnose_coverage_requirement(
    requirement: dict[str, Any],
    corpus_evidence: list[dict[str, Any]] | None = None,
    policy_restrictions: list[str] | None = None,
) -> RequirementDiagnosticRecord:
    """Classifies a failed or missing requirement into the 4 diagnostic categories."""
    req_id = str(requirement.get("requirement_id", "req-unknown"))
    label = str(requirement.get("label") or requirement.get("text") or "")
    corpus_evidence = corpus_evidence or []
    policy_restrictions = policy_restrictions or []

    # 1. Check for malformed / output formatting directive
    if _COVERAGE_OUTPUT_ONLY.search(label):
        return RequirementDiagnosticRecord(
            requirement_id=req_id,
            requirement_label=label,
            category=CoverageDiagnosticCategory.MALFORMED_REQUIREMENT,
            disposition=RequirementDisposition.FORMATTING_DIRECTIVE_EXCLUDED,
            root_cause_explanation="Output presentation or formatting rule was miscompiled as a material factual requirement.",
            is_defect=True,
            evidence_refs=[],
            remediation_code_delta="Exclude formatting rules from material requirement extraction via _COVERAGE_OUTPUT_ONLY filter.",
        )

    # 2. Check for policy restriction
    for pol in policy_restrictions:
        if pol.lower() in label.lower():
            return RequirementDiagnosticRecord(
                requirement_id=req_id,
                requirement_label=label,
                category=CoverageDiagnosticCategory.POLICY_RESTRICTION,
                disposition=RequirementDisposition.GENUINE_GAP,
                root_cause_explanation=f"Evidence is restricted by tenant boundary or security firewall policy: {pol}",
                is_defect=False,
                evidence_refs=[],
                remediation_code_delta=None,
            )

    # 3. Check for retrieval omission (evidence exists in corpus but was omitted from initial packet)
    matching_corpus = [
        doc for doc in corpus_evidence
        if any(term in str(doc.get("text", "")).lower() for term in label.lower().split() if len(term) > 3)
    ]
    if matching_corpus:
        doc_refs = [str(d.get("doc_id") or d.get("id", "doc")) for d in matching_corpus]
        return RequirementDiagnosticRecord(
            requirement_id=req_id,
            requirement_label=label,
            category=CoverageDiagnosticCategory.RETRIEVAL_OMISSION,
            disposition=RequirementDisposition.RECOVERABLE_VIA_SUPPLEMENTAL,
            root_cause_explanation="Grounded evidence exists in corpus but was omitted in initial retrieval pass.",
            is_defect=True,
            evidence_refs=doc_refs,
            remediation_code_delta="Invoke coverage_supplemental_retrieval seeded from requirement label.",
        )

    # 4. Genuine missing source evidence
    return RequirementDiagnosticRecord(
        requirement_id=req_id,
        requirement_label=label,
        category=CoverageDiagnosticCategory.MISSING_SOURCE_EVIDENCE,
        disposition=RequirementDisposition.GENUINE_GAP,
        root_cause_explanation="Corpus contains no grounded evidence for this material requirement.",
        is_defect=False,
        evidence_refs=[],
        remediation_code_delta=None,
    )
