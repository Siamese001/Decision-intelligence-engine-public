"""
dual_pipeline/transcript_pipeline_core/stakeholder_validator.py

Stakeholder Resolution Validator & GraphDB SSOT Synchronizer.
Enforces that all stakeholder names, titles, and roles across Outputs 1, 2, and 3
are sourced directly from GraphDB (Single Source of Truth) with zero cross-output discrepancies.
Fails closed if Output 2 and Output 3 present conflicting titles or roles for the same entity.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.transcript_pipeline_core.stakeholder_validator")


class StakeholderInconsistencyError(ValueError):
    """Raised when stakeholder names, titles, or roles conflict across outputs or with GraphDB SSOT."""
    pass


@dataclass
class ObservedStakeholderRole:
    """An observed stakeholder role in a specific deliverable."""
    person_name: str
    observed_role: str
    source_output: str  # "OUTPUT_1", "OUTPUT_2", "OUTPUT_3"
    canonical_graph_role: Optional[str] = None
    canonical_graph_org: Optional[str] = None


@dataclass
class StakeholderConsistencyReport:
    """Audit report detailing cross-output stakeholder consistency."""
    is_consistent: bool
    stakeholders_checked: List[str]
    discrepancies: List[Dict[str, Any]] = field(default_factory=list)
    ssot_alignments: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_consistent": self.is_consistent,
            "stakeholders_checked_count": len(self.stakeholders_checked),
            "stakeholders_checked": self.stakeholders_checked,
            "discrepancies_count": len(self.discrepancies),
            "discrepancies": self.discrepancies,
            "ssot_alignments": self.ssot_alignments,
        }


class StakeholderResolutionValidator:
    """
    Validates cross-output stakeholder consistency against the GraphDB Single Source of Truth.
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]

    def validate_deliverables(
        self,
        output_1_text: str = "",
        output_2_text: str = "",
        output_3_payload: Optional[Dict[str, Any]] = None,
        fail_closed: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StakeholderConsistencyReport:
        """
        Scans Output 1, Output 2, and Output 3 and asserts zero role/title discrepancies.
        """
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity

        o1_roles = self._extract_roles_from_output_1(output_1_text)
        o2_roles = self._extract_roles_from_output_2(output_2_text)
        o3_roles = self._extract_roles_from_output_3(output_3_payload or {})

        all_names = set(o1_roles.keys()) | set(o2_roles.keys()) | set(o3_roles.keys())
        discrepancies: List[Dict[str, Any]] = []
        alignments: List[Dict[str, Any]] = []

        for name in sorted(all_names):
            r1 = o1_roles.get(name)
            r2 = o2_roles.get(name)
            r3 = o3_roles.get(name)

            # Query GraphDB SSOT
            ent = resolve_person_entity(name, repo_root=self.repo_root)
            ssot_role = ent.role if ent else None
            ssot_org = ent.org if ent else None

            # Compare non-empty roles between Output 2 and Output 3
            roles_present = {}
            if r2:
                roles_present["Output 2"] = r2
            if r3:
                roles_present["Output 3"] = r3
            if r1:
                roles_present["Output 1"] = r1

            unique_roles = set(roles_present.values())

            # Check for conflict between outputs
            if len(unique_roles) > 1:
                # Normalize minor punctuation/spacing differences
                norm_roles = {self._normalize_role(r) for r in unique_roles}
                if len(norm_roles) > 1:
                    roles_list = list(unique_roles)
                    all_compat = all(
                        self._is_compatible_role(roles_list[i], roles_list[j])
                        for i in range(len(roles_list))
                        for j in range(i + 1, len(roles_list))
                    )
                    if not all_compat:
                        disc = {
                            "person_name": name,
                            "discrepancy_type": "CROSS_OUTPUT_ROLE_CONFLICT",
                            "observed_roles": roles_present,
                            "graphdb_ssot_role": ssot_role,
                            "error_message": (
                                f"Stakeholder '{name}' has conflicting roles across deliverables: "
                                f"{roles_present}. Canonical GraphDB SSOT role is '{ssot_role}'."
                            ),
                        }
                        discrepancies.append(disc)
                        continue

            # Check if an explicit HITL resolution authorizes this updated title
            has_hitl_receipt = False
            if metadata:
                hitl_receipts = metadata.get("hitl_role_receipts", {})
                hitl_resolutions = metadata.get("hitl_role_resolutions", {})
                if name in hitl_receipts or name in hitl_resolutions:
                    has_hitl_receipt = True

            # Check if observed role diverges from GraphDB SSOT
            chosen_role = list(unique_roles)[0] if unique_roles else ssot_role
            if ssot_role and chosen_role and not has_hitl_receipt:
                if self._normalize_role(chosen_role) != self._normalize_role(ssot_role):
                    # Flag warning or discrepancy if completely mismatched (and not generic participant/attendee)
                    if chosen_role.lower().strip() not in ("participant", "attendee", "speaker", "member"):
                        if not self._is_compatible_role(chosen_role, ssot_role):
                            disc = {
                                "person_name": name,
                                "discrepancy_type": "GRAPHDB_SSOT_DIVERGENCE",
                                "observed_role": chosen_role,
                                "graphdb_ssot_role": ssot_role,
                                "error_message": (
                                    f"Stakeholder '{name}' role '{chosen_role}' diverges from "
                                    f"canonical GraphDB SSOT role '{ssot_role}'."
                                ),
                            }
                            discrepancies.append(disc)
                            continue
            elif ssot_role and chosen_role and has_hitl_receipt:
                logger.info(
                    "SSOT divergence bypassed for '%s': authorized by C0.5 HITL resolution ('%s')",
                    name, chosen_role,
                )

            alignments.append({
                "person_name": name,
                "aligned_role": chosen_role or ssot_role or "Participant",
                "org": ssot_org or "Internal",
                "sources": list(roles_present.keys()),
            })

        is_consistent = len(discrepancies) == 0
        report = StakeholderConsistencyReport(
            is_consistent=is_consistent,
            stakeholders_checked=sorted(all_names),
            discrepancies=discrepancies,
            ssot_alignments=alignments,
        )

        if fail_closed and not is_consistent:
            err_details = "\n".join(d["error_message"] for d in discrepancies)
            raise StakeholderInconsistencyError(
                f"Master Invariant 6 Violation: Stakeholder roles are inconsistent across outputs!\n{err_details}\n"
                "All names, titles, and roles must strictly derive from the GraphDB Single Source of Truth."
            )

        return report

    @staticmethod
    def _normalize_role(role: str) -> str:
        """Strips whitespace, slashes, and standardizes casing for comparison."""
        clean = re.sub(r"[/\-_,]", " ", role.lower())
        return " ".join(clean.split())

    @staticmethod
    def _is_compatible_role(role_a: str, role_b: str) -> bool:
        """Checks if two role descriptions are semantically compatible subsets."""
        norm_a = StakeholderResolutionValidator._normalize_role(role_a)
        norm_b = StakeholderResolutionValidator._normalize_role(role_b)
        if norm_a == norm_b:
            return True
        words_a = set(norm_a.split())
        words_b = set(norm_b.split())
        if not words_a or not words_b:
            return True
        overlap = words_a.intersection(words_b)
        return len(overlap) >= min(len(words_a), len(words_b)) * 0.60

    def _extract_roles_from_output_1(self, text: str) -> Dict[str, str]:
        """Extracts speaker names and roles from Output 1 header or participant table."""
        roles: Dict[str, str] = {}
        if not text:
            return roles
        # Match markdown table rows or lists: | Amit Ayer | PartnerAdvisory | Lead Enterprise Architect |
        table_matches = re.findall(r"\|\s*([A-Z][a-zA-Z\s]{1,30})\s*\|\s*[^|]*\s*\|\s*([^|\n]+)\s*\|", text)
        for name, role in table_matches:
            name_clean = name.strip()
            role_clean = role.strip()
            if name_clean.lower() not in ("name", "attendee", "speaker", "participant"):
                roles[name_clean] = role_clean
        return roles

    def _extract_roles_from_output_2(self, text: str) -> Dict[str, str]:
        """Extracts attendee roles from Output 2 Executive Recap."""
        roles: Dict[str, str] = {}
        if not text:
            return roles
        # Match: - **Amit Ayer** (Lead Enterprise Architect) or - Amit Ayer: Lead Architect
        patterns = [
            r"[-*]\s*\*\*([A-Z][a-zA-Z\s]{1,30})\*\*\s*\(([^)]+)\)",
            r"[-*]\s*([A-Z][a-zA-Z\s]{1,30})\s*\(([^)]+)\)",
            r"[-*]\s*\*\*([A-Z][a-zA-Z\s]{1,30})\*\*:\s*([^\n]+)",
        ]
        for pat in patterns:
            for name, role in re.findall(pat, text):
                clean_name = name.strip()
                clean_role = role.strip()
                if "," in clean_role:
                    parts = [p.strip() for p in clean_role.split(",") if p.strip()]
                    if len(parts) >= 2 and any(org_kw in parts[0].lower() for org_kw in ("partner_advisory", "apex_global", "nea", "client", "internal", "partner")):
                        clean_role = parts[1]
                if clean_name.lower() not in ("date", "time", "meeting", "attendees"):
                    roles[clean_name] = clean_role
        return roles

    def _extract_roles_from_output_3(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts entity roles from Output 3 canonical JSON projection."""
        roles: Dict[str, str] = {}
        if not payload:
            return roles

        # 1. From source_metadata / attendees
        src_meta = payload.get("source_metadata", {})
        roster = src_meta.get("roster") or src_meta.get("attendees")
        if isinstance(roster, list):
            for itm in roster:
                if isinstance(itm, dict) and itm.get("name") and itm.get("role"):
                    roles[itm["name"].strip()] = itm["role"].strip()
        elif isinstance(roster, dict):
            for att_list in roster.values():
                if isinstance(att_list, list):
                    for itm in att_list:
                        if isinstance(itm, dict) and itm.get("name") and itm.get("role"):
                            roles[itm["name"].strip()] = itm["role"].strip()

        # 2. From entities array
        for ent in payload.get("entities", []):
            if isinstance(ent, dict):
                name = ent.get("canonical_name") or ent.get("name") or ent.get("display_name")
                role = ent.get("role") or ent.get("title")
                if name and role:
                    roles[name.strip()] = role.strip()

        return roles


def sync_stakeholder_to_graph(
    person_name: str,
    role: str,
    org: str = "Internal",
    db_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> bool:
    """
    Safely writes or updates a stakeholder node in the active GraphDB SQLite database.
    """
    from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
        resolve_active_graph_path,
    )

    root = repo_root or Path(__file__).resolve().parents[2]
    target_db = db_path or resolve_active_graph_path(root) or (root / "graph" / "nee_graph.sqlite")

    if not target_db.exists():
        logger.warning(f"GraphDB not found at {target_db}; skipping physical write.")
        return False

    try:
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(target_db, readonly=False)
        cur = conn.cursor()

        # Enforce canonical entity resolution prior to node creation/update
        raw_input_name = person_name
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        ent = resolve_person_entity(person_name)
        if ent:
            person_name = ent.canonical_name
            org = ent.org or org
            role = ent.role or role

        # Check if node exists
        cur.execute(
            "SELECT node_id, description FROM node WHERE canonical_name = ? AND node_type IN ('person', 'stakeholder', 'internal_person')",
            (person_name,),
        )
        row = cur.fetchone()
        now_iso = "2026-09-24T06:30:00Z"

        if row:
            node_id, old_desc = row
            new_desc = f"Affiliation label(s): {org}. Source roles: {role}. Updated via HITL sync."
            cur.execute(
                "UPDATE node SET description = ?, updated_at = ? WHERE node_id = ?",
                (new_desc, now_iso, node_id),
            )
            desc_for_fts = new_desc
            logger.info(f"Updated GraphDB node '{node_id}' for '{person_name}' with role '{role}'.")
        else:
            slug = re.sub(r"[^a-z0-9]+", "_", person_name.lower().strip())
            node_id = f"person_{slug}"
            desc = f"Affiliation label(s): {org}. Source roles: {role}. Created via HITL sync."
            cur.execute(
                "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) "
                "VALUES (?, 'person', ?, ?, ?, 'ACTIVE', 1, ?, ?)",
                (node_id, person_name, person_name, desc, now_iso, now_iso),
            )
            desc_for_fts = desc
            logger.info(f"Inserted new GraphDB node '{node_id}' for '{person_name}' with role '{role}'.")

        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_fts'")
            if cur.fetchone():
                del_fts_sql = "DELETE FROM node_fts WHERE node_id = ?"
                cur.execute(del_fts_sql, (node_id,))
                ins_fts_sql = "INSERT INTO node_fts (node_id, canonical_name, display_name, description) VALUES (?, ?, ?, ?)"
                cur.execute(ins_fts_sql, (node_id, person_name, person_name, desc_for_fts))
        except Exception as e:
            logger.debug("Failed syncing node_fts: %s", e)

        # If raw name differed from canonical, register alias in alias table if present
        if raw_input_name != person_name:
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alias'")
                if cur.fetchone():
                    cur.execute(
                        "INSERT OR IGNORE INTO alias (alias_name, canonical_name, active) VALUES (?, ?, 1)",
                        (raw_input_name, person_name),
                    )
                    logger.info("Registered alias '%s' -> '%s' in GraphDB alias table.", raw_input_name, person_name)
            except Exception as e:
                logger.debug("Failed registering alias: %s", e)

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to sync stakeholder '{person_name}' to GraphDB: {e}")
        return False


StakeholderValidator = StakeholderResolutionValidator
