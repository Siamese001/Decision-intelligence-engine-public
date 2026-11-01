"""
Sub-second deterministic cross-artifact reconciler (Provider-Free).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class CrossArtifactReconciler:
    """Sub-second deterministic cross-artifact reconciler (Provider-Free)."""

    DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")

    def _resolve_budget_ceiling(
        self,
        f3_data: Dict[str, Any],
        metadata: Dict[str, Any] | None = None,
        f1_text: str = "",
    ) -> tuple[int, int]:
        from dual_pipeline.transcript_duration_helper import (
            count_roster_scaffolding_mutations,
            resolve_duration_mutation_budget,
        )
        mutations = f3_data.get("graph_mutations", {})
        total_mutations = (
            len(mutations.get("entity_upserts", []))
            + len(mutations.get("relationship_signals", []))
            + len(mutations.get("decision_updates", []))
            + len(mutations.get("proposal_updates", []))
            + len(mutations.get("action_updates", []))
            + len(mutations.get("risk_updates", []))
            + len(mutations.get("strategic_intent_signals", []))
        )
        meta = dict(metadata or f3_data.get("metadata") or {})
        base_budget = resolve_duration_mutation_budget(metadata=meta, text=f1_text, base_budget=30)
        roster_exempt = count_roster_scaffolding_mutations(mutations)
        return total_mutations, base_budget + roster_exempt

    def validate_mutation_budget(
        self,
        f3_data: Dict[str, Any],
        metadata: Dict[str, Any] | None = None,
        f1_text: str = "",
    ) -> List[str]:
        """Validates that total graph mutations do not exceed the materiality + roster ceiling."""
        total_mutations, effective_ceiling = self._resolve_budget_ceiling(f3_data, metadata, f1_text)
        failures: List[str] = []
        if total_mutations > effective_ceiling:
            failures.append(
                f"Total graph mutations ({total_mutations}) exceeds maximum budget of {effective_ceiling}."
            )
        return failures

    def reconcile(
        self,
        f1_text: str,
        f2_text: str,
        f3_data: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        failures: List[str] = []

        # 1. Strict Date Enforcement: MM/DD/YYYY in metadata & headers
        date_str = str(metadata.get("date", "") or "").strip()
        if re.match(r"^\d{2}-\d{2}-\d{4}$", date_str):
            date_str = date_str.replace("-", "/")
            metadata["date"] = date_str
        if date_str and not self.DATE_PATTERN.match(date_str):
            failures.append(f"Metadata date '{date_str}' does not conform to strict MM/DD/YYYY format.")

        # 2. Schema version verification
        if f3_data.get("schema_version") != "5.7":
            failures.append("Output 3 schema_version must be '5.7'.")

        # 3. Mutation budget enforcement (materiality + duration + roster exemption)
        mutations = f3_data.get("graph_mutations", {})
        total_mutations, effective_ceiling = self._resolve_budget_ceiling(f3_data, metadata, f1_text)
        if total_mutations > effective_ceiling:
            failures.append(
                f"Total graph mutations ({total_mutations}) exceeds maximum budget of {effective_ceiling}."
            )

        # 4. Action owners in Output 2 matched to valid graph entities or allowed prefixes
        allowed_prefixes = (
            "person:", "org:", "system:", "workstream:", "project:",
            "metric:", "role:", "policy:", "decision:", "risk:", "dependency:", "action:", "document:",
            "deliverable:", "milestone:", "forum:", "model:", "initiative:", "capability:", "pattern:", "proposal:", "stakeholder:",
        )
        for ent in mutations.get("entity_upserts", []):
            eid = ent.get("id", "")
            if not any(eid.startswith(p) for p in allowed_prefixes):
                failures.append(f"Entity '{eid}' does not use an allowed prefix.")

        person_entities = [
            ent for ent in mutations.get("entity_upserts", [])
            if isinstance(ent, dict) and str(ent.get("id", "")).startswith("person:")
        ]
        if (
            f2_text
            and person_entities
            and re.search(r"^#{1,3}\s*Action\s*&\s*Governance\s*Ledger\b", f2_text, re.MULTILINE | re.IGNORECASE)
        ):
            f3_person_tokens: set[str] = set()
            for ent in person_entities:
                pid = str(ent.get("id", "")).lower().replace("person:", "").replace("_", " ").strip()
                pname = str(ent.get("canonical_name", "") or ent.get("label", "")).lower().strip()
                for tok in re.findall(r"[a-z]{2,}", f"{pid} {pname}"):
                    f3_person_tokens.add(tok)
            ev_idx = f3_data.get("evidence_index", {})
            if isinstance(ev_idx, dict):
                for ev_item in ev_idx.values():
                    if isinstance(ev_item, dict):
                        spk = str(ev_item.get("speaker", "")).lower().replace("person:", "").replace("_", " ").strip()
                        for tok in re.findall(r"[a-z]{2,}", spk):
                            f3_person_tokens.add(tok)
            if metadata and isinstance(metadata, dict):
                att = metadata.get("attendees")
                if isinstance(att, dict):
                    for names in att.values():
                        if isinstance(names, list):
                            for name in names:
                                for tok in re.findall(r"[a-z]{2,}", str(name).lower()):
                                    f3_person_tokens.add(tok)
                elif isinstance(att, list):
                    for name in att:
                        for tok in re.findall(r"[a-z]{2,}", str(name).lower()):
                            f3_person_tokens.add(tok)
            sec_match = re.search(
                r"^#{1,3}\s*Action\s*&\s*Governance\s*Ledger\b(.*?)(?=^#{1,3}\s+|\Z)",
                f2_text,
                re.MULTILINE | re.DOTALL | re.IGNORECASE,
            )
            sec_body = sec_match.group(1) if sec_match else ""
            owner_col_idx = 3
            for line in sec_body.splitlines():
                s = line.strip()
                if not (s.startswith("|") and s.endswith("|")):
                    continue
                cols = [c.strip() for c in s.split("|")[1:-1]]
                if not cols or all(set(c) <= {"-", ":", " "} for c in cols):
                    continue
                lower_cols = [c.lower() for c in cols]
                is_data_row = bool(re.match(r"^(?:[A-Za-z]+[-_]?\d+\.?|\d+\.?)$", cols[0]))
                if not is_data_row and any("owner" in c or "accountable" in c or "dri" in c for c in lower_cols):
                    for idx, c in enumerate(lower_cols):
                        if "owner" in c or "accountable" in c or "dri" in c:
                            owner_col_idx = idx
                    continue
                if not is_data_row:
                    continue
                owner_cell = cols[owner_col_idx] if len(cols) > owner_col_idx else (cols[1] if len(cols) > 1 else "")
                owner_clean = re.sub(r"\*+|`+", "", owner_cell).strip()
                paren_match = re.search(r"\(([^)]+)\)", owner_clean)
                paren_text = paren_match.group(1).strip() if paren_match else ""
                outside_text = re.sub(r"\s*\([^)]*\)", "", owner_clean).strip()
                role_words = {"lead", "partner", "architect", "director", "engineer", "manager", "officer", "specialist", "executive"}
                if paren_text and any(rw in outside_text.lower() for rw in role_words) and not any(rw in paren_text.lower() for rw in role_words):
                    name_to_check = paren_text
                else:
                    name_to_check = outside_text

                name_tokens = [
                    t for t in re.findall(r"[a-z]{2,}", name_to_check.lower())
                    if t not in {"tbd", "unassigned", "pending", "owner", "dri", "role", "named", "none", "proposed"}
                ]
                if name_tokens and not any(tok in f3_person_tokens for tok in name_tokens):
                    failures.append(
                        f"Output 02 Action owner '{owner_clean}' does not resolve to any person:* entity in Output 03."
                    )

        # 5. Overwrite binding verification
        prior_ids = set(metadata.get("prior_entity_ids", []))
        for ent in mutations.get("entity_upserts", []):
            eid = ent.get("id")
            if eid in prior_ids and ent.get("mutation") == "CREATE":
                failures.append(f"Prior context entity '{eid}' cannot be mutated with 'CREATE'; must overwrite.")

        # 6. Rule C-19 Career Leverage Firewall enforcement across Outputs 01, 02, and 03
        c19_leak_markers = ["$1.2M", "$1.2m", "INTERNAL_ADVISORY", "networking_wedge", "political_rationale"]
        f3_str = json.dumps(f3_data) if isinstance(f3_data, dict) else str(f3_data)
        for name, text in [("Output 01 (Transcript)", f1_text), ("Output 02 (Recap)", f2_text), ("Output 03 (Graph)", f3_str)]:
            for marker in c19_leak_markers:
                if marker in text:
                    failures.append(f"RULE C-19 VIOLATION: {name} contains prohibited career leverage / internal advisory marker '{marker}'.")

        status = "PASS" if not failures else "FAIL"
        return {
            "status": status,
            "passed": not failures,
            "total_mutations": total_mutations,
            "mutation_budget_cleared": total_mutations <= effective_ceiling,
            "budget_ceiling": effective_ceiling,
            "failures": failures,
        }
