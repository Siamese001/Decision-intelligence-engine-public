"""
dual_pipeline/transcript_qa_core/gate3_roles.py

Gate 3.11 & 3.12: Role, person description, and organizational consistency linters.
Prevents generic 'Review participant.' placeholder descriptions and cross-org copy-paste defects.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

GENERIC_PERSON_DESCRIPTIONS = [
    re.compile(r"^(?:review participant|meeting attendee|participant|attendee|contributor|team member)[\s.]*$", re.I),
]


def lint_person_descriptions(entity_upserts: List[Dict[str, Any]]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Gate 3.11: Enforce that person:* entities do not have generic placeholder descriptions."""
    failures: List[str] = []
    checked_count = 0
    for e in entity_upserts:
        e_id = str(e.get("id") or "")
        if not e_id.startswith("person:"):
            continue
        checked_count += 1
        desc = (e.get("description") or "").strip()
        if not desc or any(pat.match(desc) for pat in GENERIC_PERSON_DESCRIPTIONS):
            failures.append(
                f"Gate 3.11: Person entity '{e_id}' has generic placeholder description '{desc}'. "
                "Substantive role or project context required."
            )
    passed = len(failures) == 0
    return passed, failures, {
        "passed": passed,
        "person_entities_checked": checked_count,
        "violations_count": len(failures),
    }


def lint_member_of_consistency(relationship_signals: List[Dict[str, Any]]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Gate 3.12: Asserts MEMBER_OF relationship descriptions do not contradict target organization."""
    failures: List[str] = []
    checked_count = 0
    for r in relationship_signals:
        pred = (r.get("predicate") or r.get("relationship") or "").upper()
        if pred != "MEMBER_OF":
            continue
        checked_count += 1
        target = (r.get("target_node_id") or r.get("target") or "").lower()
        desc = (r.get("description") or "").lower()
        # Check contradictory org mentions
        if "partner_advisory" in target and any(client_token in desc for client_token in ("apex", "apex_global", "client team")):
            failures.append(
                f"Gate 3.12: Relationship target '{target}' contradicts description '{desc}'."
            )
        elif any(client_token in target for client_token in ("apex", "nea", "apex_global")) and "partner_advisory team member" in desc:
            failures.append(
                f"Gate 3.12: Relationship target '{target}' contradicts description '{desc}'. "
                "Client attendee misattributed as 'PartnerAdvisory team member'."
            )
    passed = len(failures) == 0
    return passed, failures, {
        "passed": passed,
        "member_of_checked": checked_count,
        "contradictions_count": len(failures),
    }
