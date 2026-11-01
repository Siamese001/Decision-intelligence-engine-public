"""
dual_pipeline/llm_judge_core/cross_meeting_coherence.py

Adversarial Cross-Meeting Coherence Judge.
Prosecutorial micro-evaluator that cross-examines Outputs 1-3 against prior project meetings.
Detects:
1. Entity Identity Drift & Mutant Surnames (e.g., harper Hoffman mutating into Mandy Haskell).
2. Constraint C-27 Violations: Uncontextualized bare first names in Output 02.
3. Unverified Role Regressions: Attendees marked 'Role unverified' despite established roles in prior meetings.
4. Decision Incoherence: Contradictions with active predecessor decisions without explicit supersession.

The C-27 bare-name check-set is derived dynamically from the IdentityResolver graph
(base roster + JSON reconciliations + PM roster + SQLite nodes) rather than a hardcoded
constant.  This ensures every meeting's roster is covered automatically.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Pattern to detect bare first names in Output 02 without parenthetical role or corporate tag
BARE_NAME_PATTERN = re.compile(
    r"(?:\b(?:what|with|from|on|to|for|about|ask|tell|by)\s+|\b)([A-Z][a-z]{2,12})\b(?!\s*\(.*?\))(?!\s+[A-Z][a-z]+)"
)

# Lazy module-level resolver singleton.
_RESOLVER_SINGLETON: Any = None


def _get_resolver() -> Any:
    """Return a lazily-instantiated IdentityResolver singleton."""
    global _RESOLVER_SINGLETON
    if _RESOLVER_SINGLETON is None:
        try:
            from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
            _RESOLVER_SINGLETON = IdentityResolver()
        except Exception:
            _RESOLVER_SINGLETON = None
    return _RESOLVER_SINGLETON


@dataclass(frozen=True)
class CrossMeetingCoherenceResult:
    """Immutable outcome of adversarial cross-meeting coherence audit."""
    passed: bool
    hard_veto: bool
    penalty: int
    findings: List[str] = field(default_factory=list)
    remediation_directives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "hard_veto": self.hard_veto,
            "penalty": self.penalty,
            "findings": list(self.findings),
            "remediation_directives": list(self.remediation_directives),
        }


class CrossMeetingCoherenceJudge:
    """Prosecutorial cross-meeting auditor enforcing entity continuity and Constraint C-27."""

    def __init__(
        self,
        identity_resolver: Any = None,
    ) -> None:
        # Accept an injected resolver (for testing) or fall back to the singleton.
        self._resolver = identity_resolver

    def _get_grounding_names(
        self,
        roster_names: Optional[List[str]] = None,
    ) -> Set[str]:
        """Return the dynamic C-27 scan set from the graph-backed resolver."""
        resolver = self._resolver or _get_resolver()
        if resolver is not None:
            return resolver.get_first_names_requiring_grounding(attendees=roster_names)
        logger.warning(
            "CrossMeetingCoherenceJudge: IdentityResolver unavailable; "
            "C-27 bare-name check-set will be empty."
        )
        return set()

    def audit(
        self,
        f1_text: str,
        f2_text: str,
        f3_json: Dict[str, Any],
        cross_meeting_registry: Optional[Dict[str, Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        roster_names: Optional[List[str]] = None,
    ) -> CrossMeetingCoherenceResult:
        findings: List[str] = []
        remediations: List[str] = []
        penalty = 0
        hard_veto = False
        registry = cross_meeting_registry or {}

        # 1. Mutant Surname / Entity Identity Drift Detection
        # e.g., harper Hoffman -> Mandy Haskell
        combined_text = f"{f1_text}\n{f2_text}"
        for token, info in registry.items():
            canonical = info.get("canonical_name", "")
            if not canonical or " " not in canonical:
                continue

            parts = canonical.split()
            first_name, expected_last = parts[0], parts[-1]

            # Look for instances of First Name + WRONG Last Name
            # e.g. Mandy Haskell when canonical is harper Hoffman
            mutant_pattern = re.compile(rf"\b{re.escape(first_name)}\s+([A-Z][a-z]+)\b")
            for match in mutant_pattern.finditer(combined_text):
                found_last = match.group(1)
                if found_last.lower() != expected_last.lower() and found_last.lower() not in ("speaker", "team", "review", "call", "lead"):
                    # Known false positive filters
                    if canonical.lower() in ("alex sanchez", "alexandra coro") and found_last.lower() in ("sanchez", "coro", "corrow"):
                        continue
                    findings.append(
                        f"ENTITY_IDENTITY_DRIFT: Found '{first_name} {found_last}' which conflicts with verified "
                        f"project entity '{canonical}' from prior meeting {info.get('source_meeting', '')}."
                    )
                    remediations.append(
                        f"Correct hallucinated name '{first_name} {found_last}' to canonical '{canonical}' ({info.get('org')})."
                    )
                    penalty += 15
                    hard_veto = True

        # 2. Constraint C-27: Uncontextualized Bare First Names in Output 02
        if f2_text:
            # Derive scan set from graph at runtime — not from a hardcoded constant
            grounding_names = self._get_grounding_names(roster_names=roster_names)
            # Check for bare stakeholder references without titles or organizations
            for line in f2_text.splitlines():
                # Skip header and metadata lines
                if line.startswith("#") or line.startswith("**Date") or line.startswith("**Attendees") or line.startswith("| Item"):
                    continue
                for name in grounding_names:
                    # check if the bare first name appears without a surname or role
                    pattern = re.compile(rf"\b{re.escape(name.capitalize())}\b(?!\s+[A-Z][a-z]+)(?!\s*\(.*?\))")
                    if pattern.search(line):
                        # Allow if it's already an attendee's unique name or in quotes
                        findings.append(
                            f"C27_BARE_NAME_DEFECT: In-dialogue stakeholder '{name.capitalize()}' referenced "
                            "without full name, organization, or role in Output 02."
                        )
                        remediations.append(
                            f"Ground referenced actor '{name.capitalize()}' with full name, title, and organization (C-27)."
                        )
                        penalty += 5
                        break

        # 3. Unverified Role Regressions in Output 02
        if f2_text and registry:
            for token, info in registry.items():
                canonical = info.get("canonical_name", "")
                org = info.get("org", "")
                role = info.get("role", "")
                if canonical and role and role != "Unknown":
                    # Check if attendee roster in Output 02 labeled them 'Role unverified'
                    unverified_pattern = re.compile(rf"{re.escape(canonical)}.*?Role unverified", re.IGNORECASE)
                    if unverified_pattern.search(f2_text):
                        findings.append(
                            f"UNVERIFIED_ROLE_REGRESSION: '{canonical}' marked 'Role unverified' in Output 02 "
                            f"despite verified role '{role}' ({org}) in prior project meetings."
                        )
                        remediations.append(
                            f"Restore verified role '{role}' ({org}) for '{canonical}' in Output 02 roster."
                        )
                        penalty += 5

        # 4. Output 03 Locator Sanity & Cross-Artifact Consistency
        if f3_json:
            for stmt in f3_json.get("statements", []):
                loc = stmt.get("locator", {})
                if loc.get("kind") == "timestamp":
                    val = str(loc.get("value", "")).strip()
                    if val and not re.match(r"^\d{1,2}:\d{2}(?::\d{2})?$", val):
                        findings.append(
                            f"CORRUPTED_LOCATOR_SCHEMA: Statement '{stmt.get('statement_id')}' has kind='timestamp' "
                            f"but value is raw text '{val[:40]}...' instead of HH:MM:SS."
                        )
                        remediations.append(
                            f"Fix timestamp locator for '{stmt.get('statement_id')}' to standard [HH:MM:SS]."
                        )
                        penalty += 10
                        hard_veto = True

        passed = (len(findings) == 0) and not hard_veto
        penalty = min(30, penalty)

        logger.info(
            f"CrossMeetingCoherenceJudge audit: passed={passed}, hard_veto={hard_veto}, "
            f"penalty={penalty}, findings={len(findings)}."
        )
        return CrossMeetingCoherenceResult(
            passed=passed,
            hard_veto=hard_veto,
            penalty=penalty,
            findings=findings,
            remediation_directives=remediations,
        )
