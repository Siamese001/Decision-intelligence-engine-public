"""
dual_pipeline/transcript_qa_core/role_consistency_validator.py

Pre-Freeze Role Consistency Validator for Stage 01 (Artifact 01).
Extracts rendered attendee roles from Output 01 header and body, verifies them against
the canonical GraphDB and C0.6 receipts, and deterministically heals hallucinated roles
before the artifact is finalized and frozen.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RoleDiscrepancy:
    attendee: str
    rendered_role: str
    canonical_role: str
    source: str
    healed: bool = False


@dataclass
class RoleConsistencyReport:
    is_consistent: bool
    roles_checked: int
    roles_verified: int
    roles_healed: int
    status: str = "PASS"
    discrepancies: List[RoleDiscrepancy] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["discrepancies"] = [asdict(disc) for disc in self.discrepancies]
        d["status"] = self.status
        return d


class RoleConsistencyValidator:
    """
    Validates and heals attendee roles rendered in Output 01 before artifact freezing.
    """

    @classmethod
    def validate_and_heal(
        cls, transcript: str, metadata: Dict[str, Any]
    ) -> Tuple[str, RoleConsistencyReport]:
        if not transcript:
            return transcript, RoleConsistencyReport(
                is_consistent=True, roles_checked=0, roles_verified=0, roles_healed=0, status="NOT_EVALUATED"
            )

        rendered_roles = cls._extract_roles_from_transcript(transcript)
        discrepancies: List[RoleDiscrepancy] = []
        healed_transcript = transcript
        roles_checked = len(rendered_roles)
        roles_verified = 0
        roles_healed = 0

        from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import (
            _is_placeholder,
        )

        for name, rendered_role in rendered_roles.items():
            canon_role, source = cls._resolve_canonical_role(name, metadata)
            if not canon_role or _is_placeholder(canon_role):
                continue

            # Compare clean role strings
            clean_rendered = cls._clean_role(rendered_role)
            clean_canon = cls._clean_role(canon_role)

            if clean_rendered.lower() == clean_canon.lower():
                roles_verified += 1
                continue

            # Discrepancy detected: Rendered role differs from GraphDB SSOT
            logger.warning(
                "RoleConsistencyValidator: Role hallucination detected for '%s'. "
                "Rendered: '%s' vs GraphDB Canonical: '%s' (source=%s).",
                name, rendered_role, canon_role, source,
            )

            # Deterministic healing: replace rendered role with canonical role in transcript
            new_text, did_heal = cls._heal_role_in_transcript(
                healed_transcript, name, rendered_role, canon_role
            )
            if did_heal:
                healed_transcript = new_text
                roles_healed += 1
                logger.info(
                    "RoleConsistencyValidator: Deterministically healed role for '%s' to '%s'.",
                    name, canon_role,
                )

            discrepancies.append(
                RoleDiscrepancy(
                    attendee=name,
                    rendered_role=rendered_role,
                    canonical_role=canon_role,
                    source=source,
                    healed=did_heal,
                )
            )

        is_consistent = len(discrepancies) == 0 or all(d.healed for d in discrepancies)
        if roles_checked == 0:
            status = "NOT_EVALUATED"
        else:
            status = "PASS" if is_consistent else "FAIL"
        report = RoleConsistencyReport(
            is_consistent=is_consistent,
            roles_checked=roles_checked,
            roles_verified=roles_verified,
            roles_healed=roles_healed,
            status=status,
            discrepancies=discrepancies,
        )
        return healed_transcript, report

    @classmethod
    def _extract_roles_from_transcript(cls, text: str) -> Dict[str, str]:
        """Extracts {attendee_name: role} from header Participants section or tables."""
        roles: Dict[str, str] = {}
        # Locate Participants block
        m_part = re.search(r"\*\*Participants:\*\*\s*\n([\s\S]*?)(?=\n---|##|\Z)", text, re.IGNORECASE)
        search_block = m_part.group(1) if m_part else text[:2500]

        for line in search_block.splitlines():
            clean_l = line.strip().lstrip("*-#").strip()
            if not clean_l:
                continue
            # If line has org grouping with colon, extract after colon
            if ":" in clean_l:
                prefix, remainder = clean_l.split(":", 1)
                if any(org in prefix.lower() for org in ("partner_advisory", "apex_global", "nea", "apex", "client", "google", "partner", "attendee", "participant")):
                    clean_l = remainder

            item_matches = re.findall(r"([A-Z][a-zA-Z\s\.\-']{1,40})\s*\(([^)\n]{2,80})\)", clean_l)
            for name, role in item_matches:
                clean_name = name.strip().lstrip("*-#").strip()
                clean_role = role.strip()
                if clean_role.upper() in ("NEA", "APEX", "partner_advisory", "GOOGLE", "PARTNER", "CLIENT"):
                    continue
                if clean_name and clean_role and clean_name.lower() not in ("date", "platform reference", "participants"):
                    roles[clean_name] = clean_role

        return roles

    @classmethod
    def _resolve_canonical_role(
        cls, name: str, metadata: Dict[str, Any]
    ) -> Tuple[Optional[str], str]:
        """Resolves canonical role from C0.6 verification receipt, HITL receipts, or GraphDB."""
        # 1. C0.6 GraphDB Role Verification receipt
        c06_receipt = metadata.get("graph_role_verification_receipt", {})
        att_verified = c06_receipt.get("attendees_verified", {})
        if name in att_verified:
            entry = att_verified[name]
            role = entry.get("role")
            if role:
                return role, f"c06_receipt:{entry.get('source', 'graph')}"

        # 2. HITL resolutions or receipts
        hitl_roles = metadata.get("hitl_role_resolutions", {})
        if name in hitl_roles and hitl_roles[name]:
            v = hitl_roles[name]
            if isinstance(v, dict):
                r = v.get("role") or v.get("resolved_role") or ""
                if r:
                    return str(r).strip(), "hitl_role_resolutions"
            elif isinstance(v, str) and v.strip():
                return v.strip(), "hitl_role_resolutions"

        hitl_receipts = metadata.get("hitl_role_receipts", {})
        if name in hitl_receipts:
            rcpt = hitl_receipts[name]
            r = rcpt.get("resolved_role") or rcpt.get("role")
            if r:
                return r, "hitl_role_receipts"

        # 3. Roster entry in metadata
        roster_role = cls._resolve_from_roster(name, metadata.get("roster"))
        if roster_role:
            return roster_role, "metadata_roster"

        # 4. Direct PreSynthesisEntityResolver query (GraphDB SSOT)
        try:
            from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
            ent = resolve_person_entity(
                name,
                repo_root=metadata.get("repo_root"),
                runtime_root=metadata.get("runtime_root"),
                generation_id=metadata.get("generation_id"),
                as_of_timestamp=metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date"),
            )
            if ent and ent.role:
                return ent.role, ent.source
        except Exception as e:
            logger.debug(f"RoleConsistencyValidator entity resolution note: {e}")

        return None, "none"

    @classmethod
    def _resolve_from_roster(cls, name: str, roster: Any) -> Optional[str]:
        if isinstance(roster, list):
            for item in roster:
                if isinstance(item, dict) and item.get("name") == name and item.get("role"):
                    return item["role"]
        elif isinstance(roster, dict):
            for att_list in roster.values():
                if isinstance(att_list, list):
                    for item in att_list:
                        if isinstance(item, dict) and item.get("name") == name and item.get("role"):
                            return item["role"]
        return None

    @classmethod
    def _heal_role_in_transcript(
        cls, transcript: str, name: str, old_role: str, new_role: str
    ) -> Tuple[str, bool]:
        """Substitutes hallucinated role with canonical role in Output 01."""
        target_pattern = re.compile(
            rf"(\b{re.escape(name)}\b[^(]{{0,30}}\()\s*{re.escape(old_role)}\s*(\))",
            re.IGNORECASE,
        )
        if target_pattern.search(transcript):
            healed = target_pattern.sub(rf"\g<1>{new_role}\g<2>", transcript)
            return healed, True

        # Fallback exact name + paren replacement
        alt_pattern = re.compile(
            rf"(\b{re.escape(name)}\b[^(]{{0,30}}\()[^)]*?(\))",
            re.IGNORECASE,
        )
        if alt_pattern.search(transcript):
            healed = alt_pattern.sub(rf"\g<1>{new_role}\g<2>", transcript)
            return healed, True

        return transcript, False

    @staticmethod
    def _clean_role(role_str: str) -> str:
        clean = re.sub(r"\[.*?\]", "", role_str)
        clean = re.sub(r"[,\-\/]", " ", clean)
        return " ".join(clean.split())
