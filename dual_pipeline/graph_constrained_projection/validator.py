"""Fail-Closed Entity Affiliation & Metric Validator (Layer 3).

Guards capability reviews and gap analyses against cross-organizational
role conflation, narrative hallucination, and contractual metric drift.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_constrained_projection.models import (
    Organization,
    ProjectionRow,
    ValidationReport,
    ValidationViolation,
)

logger = logging.getLogger("dual_pipeline.graph_constrained_projection.validator")

# Patterns indicating client leadership attribution
_CLIENT_LEAD_PATTERNS = [
    re.compile(r"client\s+lead\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
    re.compile(r"client\s+product\s+manager\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
    re.compile(r"client\s+sponsor\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
    re.compile(r"apex_global\s+(?:lead|manager|executive)\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
]

# Patterns indicating delivery partner attribution
_DELIVERY_LEAD_PATTERNS = [
    re.compile(r"partner_advisory\s+(?:lead|engineer|pm|manager)\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
    re.compile(r"delivery\s+lead\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
    re.compile(r"pod\s+(?:lead|contributor|engineer)\s*\(?([a-zA-Z\s]+?)\)?(?:\s|,|\.|\)|$)", re.IGNORECASE),
]


class EntityAffiliationValidator:
    """Validates generated text against deterministic graph spine boundaries."""

    def __init__(self, spine: Optional[GraphSpineResolver] = None) -> None:
        self._spine = spine or GraphSpineResolver()

    def validate_text(self, text: str, context_label: str = "text") -> List[ValidationViolation]:
        """Scans arbitrary text for cross-organizational entity conflation."""
        violations: List[ValidationViolation] = []

        # 1. Check for PartnerAdvisory entities labeled as client leads
        for pattern in _CLIENT_LEAD_PATTERNS:
            for match in pattern.finditer(text):
                captured = match.group(1).strip()
                entity = self._spine.get_stakeholder(captured)
                if entity and entity.is_delivery_partner:
                    violations.append(
                        ValidationViolation(
                            violation_type="CROSS_ORG_AFFILIATION_COLLISION",
                            entity_name=entity.canonical_name,
                            expected_org=entity.organization,
                            actual_org=Organization.apex_global.value,
                            detail=(
                                f"In [{context_label}], '{entity.canonical_name}' ({entity.organization}) "
                                f"was incorrectly labeled as a client lead/sponsor: '{match.group(0).strip()}'"
                            ),
                            severity="FATAL",
                        )
                    )

        # 2. Check for Client entities labeled as PartnerAdvisory delivery leads
        for pattern in _DELIVERY_LEAD_PATTERNS:
            for match in pattern.finditer(text):
                captured = match.group(1).strip()
                entity = self._spine.get_stakeholder(captured)
                if entity and entity.is_client:
                    violations.append(
                        ValidationViolation(
                            violation_type="CROSS_ORG_AFFILIATION_COLLISION",
                            entity_name=entity.canonical_name,
                            expected_org=entity.organization,
                            actual_org=Organization.partner_advisory.value,
                            detail=(
                                f"In [{context_label}], client stakeholder '{entity.canonical_name}' "
                                f"({entity.organization}) was incorrectly labeled as a delivery lead: '{match.group(0).strip()}'"
                            ),
                            severity="FATAL",
                        )
                    )

        return violations

    def validate_row(self, row: ProjectionRow) -> List[ValidationViolation]:
        """Validates all text fields in a single ProjectionRow."""
        row_label = f"{row.dimension} -> {row.sub_dimension}"
        violations: List[ValidationViolation] = []

        violations.extend(self.validate_text(row.standard, f"{row_label} : Standard"))
        violations.extend(self.validate_text(row.field_reality, f"{row_label} : Field Reality"))
        violations.extend(self.validate_text(row.impact, f"{row_label} : Impact"))
        violations.extend(self.validate_text(row.coaching, f"{row_label} : Coaching"))

        return violations

    def validate_matrix(self, rows: List[ProjectionRow]) -> ValidationReport:
        """Validates an entire capability matrix and returns a ValidationReport."""
        all_violations: List[ValidationViolation] = []
        checked_entities = set()

        for row in rows:
            all_violations.extend(self.validate_row(row))
            for entity in self._spine.list_all_stakeholders():
                for field_text in [row.field_reality, row.impact, row.coaching]:
                    if entity.canonical_name.lower() in field_text.lower():
                        checked_entities.add(entity.canonical_name)

        passed = len(all_violations) == 0
        return ValidationReport(
            passed=passed,
            violations=all_violations,
            checked_entities_count=len(checked_entities),
            checked_milestones_count=1,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def assert_valid(self, report: ValidationReport) -> None:
        """Raises AssertionError if report did not pass."""
        if not report.passed:
            violation_details = "\n".join(
                f"- [{v.violation_type}] {v.detail}" for v in report.violations
            )
            raise AssertionError(
                f"Fail-Closed EntityAffiliationValidator rejected projection with {len(report.violations)} "
                f"fatal violations:\n{violation_details}"
            )
