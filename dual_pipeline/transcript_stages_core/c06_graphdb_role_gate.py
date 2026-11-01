"""
dual_pipeline/transcript_stages_core/c06_graphdb_role_gate.py

C0.6 — Mandatory GraphDB Role Verification Gate.
Executes after C0.5 Title Completeness Gate and before Stage 01 synthesis to enforce that
all attendee roles and titles are strictly sourced and verified against the canonical GraphDB.
If an attendee is not found in GraphDB or has an ungrounded role, halts via interactive resolver
or raises an atomic HITLRequiredError before Output 01 is finalized.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .c0_gates import _extract_pre_enrichment_roles, _is_placeholder
from .c05_title_gate import apply_hitl_role_resolution
from .models import HITLRequiredError

logger = logging.getLogger(__name__)


def _update_roster_items(items: list, name: str, role: str, org: Optional[str] = None) -> None:
    generic = {"client / partner", "client", "partner", "unknown", ""}
    new_org = str(org or "").strip()
    clean_target = name.strip().lower()
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            i_name = str(item.get("name") or "").strip().lower()
            if i_name == clean_target:
                item["role"] = role
                cur_org = str(item.get("org") or "").strip().lower()
                if (new_org and new_org.lower() not in generic) or (not cur_org and new_org):
                    item["org"] = new_org
        elif isinstance(item, str):
            clean_str = item.split("(")[0].strip().lower()
            if clean_str == clean_target or name.lower() in item.lower():
                items[idx] = f"{name} ({role})"


def _sync_hitl_roles_to_roster(metadata: Dict[str, Any]) -> None:
    """Synchronizes confirmed HITL and speaker role resolutions into roster and attendees."""
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import strip_org_suffix_from_role
    all_confirmed: Dict[str, Tuple[str, Optional[str]]] = {}
    for key in ("hitl_role_resolutions", "speaker_role_resolutions"):
        m_roles = metadata.get(key)
        if isinstance(m_roles, dict):
            for n, r in m_roles.items():
                if not n or not r:
                    continue
                r_str = (r.get("role") or r.get("resolved_role")) if isinstance(r, dict) else str(r)
                o_str = r.get("organization") or r.get("org") if isinstance(r, dict) else None
                clean_r, found_o = strip_org_suffix_from_role(r_str, o_str)
                all_confirmed[str(n).strip()] = (clean_r, found_o or o_str)

    for name, (role, org) in all_confirmed.items():
        C06GraphDBRoleVerificationGate._update_roster_entry(metadata, name, role, org)



def _update_string_roster(text: str, name: str, role: str) -> str:
    items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", text) if p.strip()]
    for idx, it in enumerate(items):
        clean_n = re.sub(r"\s*\([^)]*\)", "", it).strip()
        if clean_n == name:
            items[idx] = f"{name} ({role})"
    return ", ".join(items)


def _build_role_recommendation_options(raw_name: str, ent: Any) -> list[Dict[str, Any]]:
    # If entity has GraphDB candidates, ascribe GraphDB-derived confidence scores directly
    candidates = getattr(ent, "candidates", None) if ent else None
    if candidates:
        options: list[Dict[str, Any]] = []
        for idx, cand in enumerate(candidates):
            conf_val = float(cand.confidence)
            conf_pct = int(round(conf_val * 100))
            tier = "HIGH" if conf_val >= 0.80 else ("MEDIUM" if conf_val >= 0.50 else "LOW")
            conf_level = f"{tier} ({conf_pct}%)"
            is_rec = (idx == 0)
            rec_prefix = "(Recommended) " if is_rec else ""
            label = f"{rec_prefix}Assign GraphDB role to {raw_name}: '{cand.role}' — Confidence: {conf_level}"
            options.append({
                "label": label,
                "role": cand.role,
                "confidence": conf_val,
                "confidence_score": conf_val,
                "confidence_level": conf_level,
                "tier": tier,
                "is_recommended": is_rec,
                "provenance": getattr(cand, "assertion_id", "graphdb"),
            })

        options.append({
            "label": f"Confirm title pending operator confirmation for {raw_name} — Confidence: LOW (40%)",
            "role": "[Title Pending Operator Confirmation]",
            "confidence": 0.40,
            "confidence_score": 0.40,
            "confidence_level": "LOW (40%)",
            "tier": "LOW",
            "is_recommended": False,
            "provenance": "operator_pending",
        })
        return options

    from dual_pipeline.transcript_metadata_core.entity_resolver import _CANONICAL_ENTERPRISE_DIRECTORY

    low_key = raw_name.lower().strip()
    dir_match = _CANONICAL_ENTERPRISE_DIRECTORY.get(low_key)
    dir_role = dir_match[2] if dir_match else None

    if ent and ent.role and not _is_placeholder(ent.role):
        rec_role = ent.role
        rec_label = f"(Recommended) Assign known GraphDB title to {raw_name}: '{ent.role}' — Confidence: HIGH (95%)"
        rec_confidence = 0.95
        rec_level = "HIGH (95%)"
    elif dir_role and not _is_placeholder(dir_role):
        rec_role = dir_role
        rec_label = f"(Recommended) Assign enterprise directory title to {raw_name}: '{dir_role}' — Confidence: MEDIUM (70%)"
        rec_confidence = 0.70
        rec_level = "MEDIUM (70%)"
    else:
        rec_role = "Project Lead"
        rec_label = f"(Recommended) Assign Project Lead / Workstream Owner to {raw_name} — Confidence: LOW (50%)"
        rec_confidence = 0.50
        rec_level = "LOW (50%)"

    return [
        {
            "label": rec_label,
            "role": rec_role,
            "confidence": rec_confidence,
            "confidence_score": rec_confidence,
            "confidence_level": rec_level,
            "tier": "HIGH",
            "is_recommended": True,
        },
        {
            "label": f"Assign Technical Architect / Engineering Lead to {raw_name} — Confidence: MEDIUM (60%)",
            "role": "Technical Architect",
            "confidence": 0.60,
            "confidence_score": 0.60,
            "confidence_level": "MEDIUM (60%)",
            "tier": "MEDIUM",
            "is_recommended": False,
        },
        {
            "label": f"Confirm title pending operator confirmation for {raw_name} — Confidence: LOW (40%)",
            "role": "[Title Pending Operator Confirmation]",
            "confidence": 0.40,
            "confidence_score": 0.40,
            "confidence_level": "LOW (40%)",
            "tier": "LOW",
            "is_recommended": False,
        },
    ]


class C06GraphDBRoleVerificationGate:
    """
    Mandatory GraphDB Role Verification Gate (C0.6).
    Enforces that every attendee in the meeting roster has their professional role
    sourced directly from the canonical GraphDB prior to Stage 01 synthesis.
    """

    def __init__(self, fail_closed: bool = True, repo_root: Optional[Path] = None) -> None:
        self.fail_closed = fail_closed
        self.repo_root = repo_root

    def _resolve_unverified_role(
        self,
        raw_name: str,
        raw_role: Any,
        ent: Any,
        metadata: Dict[str, Any],
        verified_roles: Dict[str, Dict[str, Any]],
        explicit_org: Optional[str] = None,
    ) -> None:
        interactive_resolver = metadata.get("interactive_resolver") or metadata.get("hitl_role_resolver")
        options = _build_role_recommendation_options(raw_name, ent)

        prompt = (
            f"### HITL Decision Required (GraphDB Role Sourcing — Decision 1 of 1)\n\n"
            f"**Context:** Attendee **{raw_name}** was provided with role "
            f"'{raw_role or 'not specified'}', which was not found as a verified role in the canonical GraphDB.\n\n"
            f"To prevent role hallucination in Output 01, please select or confirm **{raw_name}**'s verified project title:"
        )

        metadata["invalidate_prior_outputs"] = True

        if callable(interactive_resolver):
            resolved_choice = str(interactive_resolver({
                "prompt": prompt,
                "hitl_type": "graphdb_role_verification",
                "attendee": raw_name,
                "options": options,
            }))
            apply_hitl_role_resolution(
                metadata,
                raw_name,
                resolved_choice,
                organization=explicit_org or (ent.org if ent else None),
                source_input={"original_role": raw_role} if raw_role else None,
                repo_root=self.repo_root,
                is_human_confirmed=True,
                provenance="HUMAN_OPERATOR",
                operator_origin={"source": "human_operator", "text": "Interactive operator resolution"},
            )
            verified_roles[raw_name] = {
                "role": resolved_choice,
                "status": "HITL_RESOLVED",
                "source": "interactive_resolver",
            }
            return

        raise HITLRequiredError(
            prompt,
            hitl_type="graphdb_role_verification",
            missing_fields=[f"role:{raw_name}"],
            options=options,
            prompt=prompt,
        )

    def _process_single_attendee_role(
        self,
        raw_name: str,
        raw_role: Any,
        metadata: Dict[str, Any],
        resolved_roles: Dict[str, Any],
        verified_roles: Dict[str, Dict[str, Any]],
    ) -> None:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity

        if raw_name in resolved_roles:
            role_val = resolved_roles[raw_name]
            if isinstance(role_val, dict) and role_val.get("is_human_confirmed") is False:
                pass
            else:
                if isinstance(role_val, dict):
                    r_str = role_val.get("role") or role_val.get("resolved_role") or ""
                    o_str = role_val.get("organization") or role_val.get("org")
                else:
                    r_str = str(role_val)
                    o_str = None
                from dual_pipeline.hitl_governance_core.speaker_resolution_contract import strip_org_suffix_from_role
                clean_role, found_org = strip_org_suffix_from_role(r_str, o_str)
                verified_roles[raw_name] = {
                    "role": clean_role,
                    "status": "HITL_RESOLVED",
                    "source": "hitl_decision_ledger",
                }
                self._update_roster_entry(metadata, raw_name, clean_role, found_org or o_str)
                return

        rt_root = metadata.get("runtime_root")
        gen_id = metadata.get("generation_id")
        as_of = metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date")
        explicit_org = None
        raw_att = metadata.get("attendees")
        raw_ros = metadata.get("roster")
        cand_list = (raw_att if isinstance(raw_att, list) else []) + (
            raw_ros if isinstance(raw_ros, list) else []
        )
        for cand in cand_list:
            if isinstance(cand, dict) and cand.get("name") == raw_name and cand.get("org"):
                if str(cand["org"]).strip().lower() not in ("client / partner", "unknown", ""):
                    explicit_org = str(cand["org"]).strip()
                    break
        ent = resolve_person_entity(
            raw_name,
            repo_root=self.repo_root or metadata.get("repo_root"),
            runtime_root=rt_root,
            generation_id=gen_id,
            as_of_timestamp=as_of,
            explicit_role=raw_role,
            explicit_org=explicit_org,
        )
        role_explicitly_stated = bool(raw_role and not _is_placeholder(raw_role))
        receipts = metadata.get("hitl_role_receipts")
        is_human_confirmed = bool(isinstance(receipts, dict) and raw_name in receipts and receipts[raw_name].get("is_human_confirmed"))

        if (
            ent
            and ent.role
            and not _is_placeholder(ent.role)
            and not getattr(ent, "requires_operator_confirmation", False)
            and getattr(ent, "status", "") != "AMBIGUOUS_CONFLICT"
            and (role_explicitly_stated or is_human_confirmed)
        ):
            status_str = "GRAPH_VERIFIED" if "graph" in ent.source else "DIRECTORY_VERIFIED"
            source_label = "GraphDB" if "graph" in ent.source else "Enterprise Directory"
            logger.info(
                "C0.6 Gate: Attendee '%s' role '%s' verified from %s (source=%s).",
                raw_name, ent.role, source_label, ent.source,
            )
            verified_roles[raw_name] = {
                "role": ent.role,
                "org": ent.org,
                "status": status_str,
                "source": ent.source,
            }
            self._update_roster_entry(metadata, raw_name, ent.role, ent.org)
            apply_hitl_role_resolution(
                metadata,
                raw_name,
                ent.role,
                organization=ent.org,
                source_input={"original_role": raw_role} if raw_role else None,
                repo_root=self.repo_root,
                is_human_confirmed=is_human_confirmed,
                provenance=ent.source,
            )
            return

        self._resolve_unverified_role(raw_name, raw_role, ent, metadata, verified_roles, explicit_org=explicit_org)

    def execute(self, l0_context: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing C0.6: Mandatory GraphDB Role Verification Gate")
        metadata = l0_context.setdefault("metadata", {})
        if metadata.get("test_fast"):
            l0_context["c06_role_gate_cleared"] = True
            return l0_context
        for k in (
            "runtime_root", "generation_id", "as_of_timestamp", "repo_root",
            "graph_path", "graph_db", "interactive_resolver", "hitl_role_resolver"
        ):
            if k in l0_context and k not in metadata:
                metadata[k] = l0_context[k]
        if "attendees" in l0_context and "attendees" not in metadata:
            metadata["attendees"] = l0_context["attendees"]
        if "roster" in l0_context and "roster" not in metadata:
            metadata["roster"] = l0_context["roster"]
        roster = metadata.get("roster") or metadata.get("attendees")
        if not roster:
            l0_context["c06_role_gate_cleared"] = True
            return l0_context

        pre_roles = metadata.get("pre_enrichment_roles") or _extract_pre_enrichment_roles(roster)
        metadata["pre_enrichment_roles"] = pre_roles
        from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
            verify_ledger_backed_role_resolutions,
        )
        _, rejected = verify_ledger_backed_role_resolutions(metadata, repo_root=self.repo_root or l0_context.get("repo_root"))
        _sync_hitl_roles_to_roster(metadata)
        pre_roles = metadata.get("pre_enrichment_roles") or _extract_pre_enrichment_roles(metadata.get("roster") or metadata.get("attendees"))
        metadata["pre_enrichment_roles"] = pre_roles
        resolved_roles = metadata.setdefault("hitl_role_resolutions", {})
        for rej_name in rejected:
            resolved_roles.pop(rej_name, None)
        verified_roles: Dict[str, Dict[str, Any]] = {}

        for raw_name, raw_role in list(pre_roles.items()):
            if raw_name:
                self._process_single_attendee_role(raw_name, raw_role, metadata, resolved_roles, verified_roles)

        metadata["graph_role_verification_receipt"] = {
            "gate": "C0.6_GRAPHDB_ROLE_VERIFICATION",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "attendees_verified": verified_roles,
            "unresolved_count": 0,
            "status": "CLEARED",
        }
        l0_context["c06_role_gate_cleared"] = True
        return l0_context


    @staticmethod
    def _update_roster_entry(
        metadata: Dict[str, Any], name: str, role: str, org: Optional[str] = None
    ) -> None:
        roster = metadata.get("roster")
        if isinstance(roster, list):
            _update_roster_items(roster, name, role, org)
        elif isinstance(roster, dict):
            if name in roster:
                roster[name] = role
            for att_list in roster.values():
                if isinstance(att_list, list):
                    _update_roster_items(att_list, name, role, org)
        elif isinstance(roster, str):
            metadata["roster"] = _update_string_roster(roster, name, role)

        att = metadata.get("attendees")
        if isinstance(att, list):
            _update_roster_items(att, name, role, org)
        elif isinstance(att, dict):
            if name in att:
                att[name] = role
            for att_list in att.values():
                if isinstance(att_list, list):
                    _update_roster_items(att_list, name, role, org)
        elif isinstance(att, str):
            metadata["attendees"] = _update_string_roster(att, name, role)

        pre = metadata.get("pre_enrichment_roles")
        if isinstance(pre, dict) and name in pre:
            pre[name] = role
