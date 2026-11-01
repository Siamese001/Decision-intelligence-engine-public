"""C0 — Context Gates (Conformance and Roster Context Gate)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Tuple

from .attendee_gate import verify_mandatory_attendee_confirmation
from .hitl_guard import assert_no_u0_hitl_bypass
from .models import HITLRequiredError

logger = logging.getLogger(__name__)


class C0ConformanceGate:
    """Native / already-diarized path: lightweight pass-through conformance check."""

    def execute(self, l0_context: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing C0 (NATIVE): Conformance Gate")
        metadata = l0_context.get("metadata", {})
        verify_mandatory_attendee_confirmation(metadata, l0_context.get("transcript_text", ""))
        l0_context["conformance_checked"] = True
        l0_context["roster_gate_cleared"] = True
        return l0_context
from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import (
    _is_placeholder,
)


def _extract_role_from_string(item: str) -> Tuple[str, str]:
    m = re.match(r"^([^(]+)(?:\(([^)]+)\))?", item)
    if m:
        return m.group(1).strip(), (m.group(2) or "").strip()
    return item.strip(), ""


def _extract_roles_from_list(items: List[Any], roles: Dict[str, str]) -> None:
    for item in items:
        if isinstance(item, dict):
            name = item.get("name", "")
            if name:
                roles[name] = item.get("role", "")
        elif isinstance(item, str):
            name, role = _extract_role_from_string(item)
            if name:
                roles[name] = role


def _extract_pre_enrichment_roles(roster: Any) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    if isinstance(roster, str):
        items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", roster) if p.strip()]
        _extract_roles_from_list(items, roles)
    elif isinstance(roster, list):
        _extract_roles_from_list(roster, roles)
    elif isinstance(roster, dict):
        for att_list in roster.values():
            if isinstance(att_list, list):
                _extract_roles_from_list(att_list, roles)
            elif isinstance(att_list, str):
                items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", att_list) if p.strip()]
                _extract_roles_from_list(items, roles)
    return roles


def _build_c0_role_options(raw_name: str, ent: Optional[Any]) -> List[Dict[str, Any]]:
    candidates = getattr(ent, "candidates", None) if ent else None
    if candidates:
        role_options: List[Dict[str, Any]] = []
        for idx, cand in enumerate(candidates):
            conf_val = float(cand.confidence)
            conf_pct = int(round(conf_val * 100))
            tier = "HIGH" if conf_val >= 0.80 else ("MEDIUM" if conf_val >= 0.50 else "LOW")
            conf_level = f"{tier} ({conf_pct}%)"
            is_rec = (idx == 0)
            rec_prefix = "(Recommended) " if is_rec else ""
            role_options.append({
                "label": f"{rec_prefix}Assign GraphDB role to {raw_name}: '{cand.role}' — Confidence: {conf_level}",
                "role": cand.role,
                "confidence": conf_val,
                "confidence_score": conf_val,
                "confidence_level": conf_level,
                "tier": tier,
                "is_recommended": is_rec,
                "provenance": getattr(cand, "assertion_id", "graphdb"),
            })
        role_options.append({
            "label": f"Confirm title pending operator confirmation for {raw_name} — Confidence: LOW (40%)",
            "role": "[Title Pending Operator Confirmation]",
            "confidence": 0.40,
            "confidence_score": 0.40,
            "confidence_level": "LOW (40%)",
            "tier": "LOW",
            "is_recommended": False,
            "provenance": "operator_pending",
        })
        return role_options

    if ent and ent.role and not _is_placeholder(ent.role):
        conf_val = float(getattr(ent, "confidence", 0.90) or 0.90)
        conf_pct = int(round(conf_val * 100))
        tier = "HIGH" if conf_val >= 0.80 else ("MEDIUM" if conf_val >= 0.50 else "LOW")
        conf_level = f"{tier} ({conf_pct}%)"
        prov = getattr(ent, "assertion_id", None) or getattr(ent, "source", "graphdb")
        return [
            {
                "label": f"(Recommended) Assign GraphDB role to {raw_name}: '{ent.role}' — Confidence: {conf_level}",
                "role": ent.role,
                "confidence": conf_val,
                "confidence_score": conf_val,
                "confidence_level": conf_level,
                "tier": tier,
                "is_recommended": True,
                "provenance": prov,
            },
            {
                "label": f"Confirm title pending operator confirmation for {raw_name} — Confidence: LOW (40%)",
                "role": "[Title Pending Operator Confirmation]",
                "confidence": 0.40,
                "confidence_score": 0.40,
                "confidence_level": "LOW (40%)",
                "tier": "LOW",
                "is_recommended": False,
                "provenance": "operator_pending",
            },
        ]

    specs = [
        ("(Recommended) Assign Project Lead / Workstream Owner to", "Project Lead", 0.70, "HIGH (70%)", "HIGH", True),
        ("Assign Technical Architect / Engineering Lead to", "Technical Architect", 0.55, "MEDIUM (55%)", "MEDIUM", False),
        ("Assign Client Sponsor / Business Stakeholder to", "Client Sponsor", 0.50, "MEDIUM (50%)", "MEDIUM", False),
        ("Assign Subject Matter Expert / Domain Specialist to", "Subject Matter Expert", 0.40, "LOW (40%)", "LOW", False),
    ]
    return [
        {"label": f"{prefix} {raw_name} — Confidence: {lvl}", "role": r, "confidence": c, "confidence_score": c, "confidence_level": lvl, "tier": t, "is_recommended": rec}
        for prefix, r, c, lvl, t, rec in specs
    ]


def _check_single_role_hitl(raw_name: str, raw_role: str, metadata: Dict[str, Any]) -> None:
    if metadata.get("attendee_roles_confirmed_by_operator"):
        return
    resolved_roles = metadata.get("hitl_role_resolutions") or {}
    auto_roles = metadata.get("auto_role_resolutions") or {}

    is_single_token = len(raw_name.strip().split()) == 1

    if raw_name in auto_roles and not is_single_token:
        return

    if raw_name in resolved_roles:
        res_val = resolved_roles[raw_name]
        is_unconfirmed_proposal = isinstance(res_val, dict) and res_val.get("is_human_confirmed") is False
        if is_unconfirmed_proposal:
            resolved_roles.pop(raw_name, None)
        elif is_single_token or bool(metadata.get("strict_ledger_role_gate") or metadata.get("require_ledger_role_receipt")):
            receipts = metadata.get("hitl_role_receipts")
            receipts_dict = receipts if isinstance(receipts, dict) else {}
            has_receipt = isinstance(receipts_dict.get(raw_name), dict) and receipts_dict[raw_name].get("is_human_confirmed")
            if not has_receipt:
                from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
                    verify_ledger_backed_role_resolutions,
                )
                meta_copy = dict(metadata)
                meta_copy["require_ledger_role_receipt"] = True
                verified, rejected = verify_ledger_backed_role_resolutions(meta_copy, repo_root=metadata.get("repo_root"))
                if raw_name not in verified:
                    resolved_roles.pop(raw_name, None)
                else:
                    return
            else:
                return
        else:
            return

    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    ent = resolve_person_entity(
        raw_name,
        repo_root=metadata.get("repo_root"),
        runtime_root=metadata.get("runtime_root"),
        generation_id=metadata.get("generation_id"),
        as_of_timestamp=metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date"),
        explicit_role=raw_role,
    )
    is_ambiguous = bool(
        ent and (getattr(ent, "requires_operator_confirmation", False) or getattr(ent, "status", "") == "AMBIGUOUS_CONFLICT")
    )
    is_single_token_unverified = (
        is_single_token
        and (
            ent is None
            or len(ent.canonical_name.split()) == 1
            and getattr(ent, "source", "") not in ("authoritative_correction", "directory_prior", "charter_csv")
        )
    )

    role_explicitly_stated = bool(raw_role and not _is_placeholder(raw_role))
    if role_explicitly_stated and not is_ambiguous and not is_single_token_unverified:
        return

    # If role is unstated or placeholder, require explicit human confirmation in ledger to auto-clear
    receipts = metadata.get("hitl_role_receipts")
    is_human_confirmed = bool(
        (isinstance(receipts, dict) and raw_name in receipts and receipts[raw_name].get("is_human_confirmed"))
        or (isinstance(resolved_roles.get(raw_name), dict) and resolved_roles[raw_name].get("is_human_confirmed"))
    )

    if ent and ent.role and not _is_placeholder(ent.role) and not is_ambiguous and not is_single_token_unverified and is_human_confirmed:
        logger.info(
            "C0 HITL: Ledger-confirmed '%s' role '%s' → '%s' (GraphDB/directory prior).",
            raw_name, raw_role, ent.role,
        )
        metadata.setdefault("autonomous_hitl_role_decisions", {})[raw_name] = {
            "raw_operator_role": raw_role,
            "resolved_role": ent.role,
            "resolution_basis": ent.source,
            "confidence": f"HIGH ({int(round(float(ent.confidence) * 100))}%)",
            "gating": "AUTONOMOUS_APPROVED",
        }
        from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import C06GraphDBRoleVerificationGate
        C06GraphDBRoleVerificationGate._update_roster_entry(metadata, raw_name, ent.role, ent.org)
        return

    role_options = _build_c0_role_options(raw_name, ent)
    if is_single_token_unverified:
        context_msg = (
            f"**Context:** Attendee **{raw_name}** is a single-token first name that does not resolve "
            f"to a verified corporate entity in GraphDB or the enterprise directory ('{raw_role or 'not specified'}').\n\n"
        )
    elif is_ambiguous:
        context_msg = (
            f"**Context:** Attendee **{raw_name}** has competing active titles in GraphDB within "
            f"the 30% ambiguity margin or a role mismatch ('{raw_role or 'not specified'}').\n\n"
        )
    else:
        context_msg = (
            f"**Context:** Attendee **{raw_name}** was provided with role '{raw_role or 'not specified'}' — "
            f"this is a generic placeholder and no system directory entry was found.\n\n"
        )
    prompt = (
        f"### HITL Decision Required (Speaker Role Clarification)\n\n"
        f"{context_msg}"
        f"Please select their verified project role to ground forensic speaker attribution."
    )
    raise HITLRequiredError(
        prompt,
        hitl_type="role_clarification",
        missing_fields=[f"role:{raw_name}"],
        options=role_options,
        prompt=prompt,
    )


def _check_hitl_role_clarifications(
    pre_enrichment_roles: Dict[str, str], metadata: Dict[str, Any]
) -> None:
    from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import _sync_hitl_roles_to_roster
    _sync_hitl_roles_to_roster(metadata)
    for raw_name, raw_role in pre_enrichment_roles.items():
        _check_single_role_hitl(raw_name, raw_role, metadata)


def _enrich_single_list_item(item: Any, metadata: Optional[Dict[str, Any]] = None) -> Any:
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    meta = metadata or {}
    ctx_kwargs = {
        "repo_root": meta.get("repo_root"),
        "runtime_root": meta.get("runtime_root"),
        "generation_id": meta.get("generation_id"),
        "as_of_timestamp": meta.get("as_of_timestamp") or meta.get("meeting_date") or meta.get("date"),
    }
    if isinstance(item, dict):
        name = item.get("name", "")
        role = item.get("role", "")
        ent = resolve_person_entity(name, explicit_role=role, **ctx_kwargs)
        if ent and _is_placeholder(role):
            item["role"] = ent.role
            if not item.get("org"):
                item["org"] = ent.org
        return item
    if isinstance(item, str):
        raw_name, raw_role = _extract_role_from_string(item)
        ent = resolve_person_entity(raw_name, explicit_role=raw_role, **ctx_kwargs)
        if ent:
            eff_role = ent.role if _is_placeholder(raw_role) else (raw_role or ent.role)
            return f"{ent.display_name} ({eff_role})"
        return item
    return item


def _format_matched_attendee(att_str: str, enriched_roster: List[Any]) -> str:
    raw_att_name, _ = _extract_role_from_string(att_str)
    clean_att = raw_att_name.strip().lower()
    for itm in enriched_roster:
        if isinstance(itm, dict) and itm.get("name"):
            itm_name = str(itm["name"]).strip()
            if itm_name.lower() == clean_att or re.search(rf"(?:^|\b){re.escape(itm_name)}(?:\b|$)", att_str, re.IGNORECASE):
                return f"{itm_name} ({itm.get('role', '')})"
    return att_str


def _update_attendee_list(att_list: List[Any], enriched_roster: List[Any]) -> None:
    for idx, att_str in enumerate(att_list):
        if isinstance(att_str, str):
            att_list[idx] = _format_matched_attendee(att_str, enriched_roster)


def _update_attendees_dict_from_roster(attendees_dict: Dict[str, Any], enriched_roster: List[Any]) -> None:
    for att_list in attendees_dict.values():
        if isinstance(att_list, list):
            _update_attendee_list(att_list, enriched_roster)


def _enrich_roster_list(roster: List[Any], metadata: Dict[str, Any]) -> None:
    enriched_roster = [_enrich_single_list_item(item, metadata) for item in roster]
    metadata["roster"] = enriched_roster
    if "attendees" in metadata:
        if isinstance(metadata["attendees"], list):
            metadata["attendees"] = enriched_roster
        elif isinstance(metadata["attendees"], dict):
            _update_attendees_dict_from_roster(metadata["attendees"], enriched_roster)


def _enrich_single_dict_entry(att_entry: Any, metadata: Optional[Dict[str, Any]] = None) -> Any:
    if not isinstance(att_entry, str):
        return att_entry
    meta = metadata or {}
    raw_name, raw_role = _extract_role_from_string(att_entry)
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    ent = resolve_person_entity(
        raw_name,
        repo_root=meta.get("repo_root"),
        runtime_root=meta.get("runtime_root"),
        generation_id=meta.get("generation_id"),
        as_of_timestamp=meta.get("as_of_timestamp") or meta.get("meeting_date") or meta.get("date"),
        explicit_role=raw_role,
    )
    if ent and _is_placeholder(raw_role):
        raw_role = ent.role
    return f"{raw_name} ({raw_role})" if raw_role else raw_name


def _enrich_roster_dict(roster: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    enriched_dict = {}
    for org_k, att_list in roster.items():
        if isinstance(att_list, list):
            enriched_dict[org_k] = [_enrich_single_dict_entry(e, metadata) for e in att_list]
        else:
            enriched_dict[org_k] = att_list
    metadata["roster"] = enriched_dict
    if isinstance(metadata.get("attendees"), dict):
        metadata["attendees"] = enriched_dict


def _enrich_roster_string(roster_str: str, metadata: Dict[str, Any]) -> None:
    items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", roster_str) if p.strip()]
    enriched_items = [_enrich_single_list_item(item) for item in items]
    metadata["roster"] = enriched_items
    metadata["attendees"] = ", ".join(
        f"{it['name']} ({it['role']})" if isinstance(it, dict) and it.get("role") else str(it)
        for it in enriched_items
    )


def _enrich_roster(roster: Any, metadata: Dict[str, Any]) -> None:
    if isinstance(roster, list):
        _enrich_roster_list(roster, metadata)
    elif isinstance(roster, dict):
        _enrich_roster_dict(roster, metadata)
    elif isinstance(roster, str):
        _enrich_roster_string(roster, metadata)
    else:
        metadata["roster"] = roster


class C0RosterContextGate:
    """
    UNDIARIZED path: mandatory roster and ingestion context gate (T-GICE).

    When the operator has NOT supplied a roster, raises HITLRequiredError
    with hitl_type='roster_gate' so the pipeline can surface the structured
    HITL prompt to the caller.

    When an ingestion_context or roster IS supplied (either via metadata or via
    a prior HITL response injected back into metadata), normalizes fields into
    metadata (roster, lexicon_map, collateral_context, operator_hypothesis),
    evaluates security/PII patterns, and clears the gate.
    """

    HITL_PROMPT_TEMPLATE = (
        "### HITL Decision Required (Roster Gate — Decision 1 of 1)\n\n"
        "**Context:** This transcript was recorded on {platform} and does not have "
        "native speaker diarization. Before forensic analysis can begin, I need the "
        "complete attendee roster.\n\n"
        "**Required Information:**\n\n"
        "1. **Full attendee list** — every person who was in the meeting\n"
        "2. **Organization / company** for each attendee (e.g., PartnerAdvisory, NEA / APEX, Client Org)\n"
        "3. **Role** for each attendee if known (e.g., Developer, Delivery Lead, Principal)\n"
        "4. **Speaking volume guidance** (optional but recommended) — who spoke the most, "
        "who spoke the least, any topic-to-speaker mappings you can provide\n"
        "5. **Supplementary sources** (optional but recommended) — do you have the calendar "
        "invite, a pre-meeting deck/agenda, post-meeting notes, or email thread? "
        "These improve speaker attribution significantly.\n\n"
        "**Why this is mandatory:** Without a confirmed roster, speaker attribution "
        "defaults to generic group labels ([Participant]), which degrades the forensic "
        "value of the transcript, weakens action item ownership, and produces lower-quality "
        "graph projections."
    )

    # Security scan regexes
    _RESTRICTED_PATTERNS = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        re.compile(r"\b(?:sk|ghp|ghs|ghu|ghr|gho)_[A-Za-z0-9_]{16,}\b"),
    ]
    _CONFIDENTIAL_PATTERNS = [
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    ]

    def _scan_string_security(self, s: str, metadata: Dict[str, Any]) -> None:
        for pat in self._RESTRICTED_PATTERNS:
            if pat.search(s):
                raise ValueError(f"Restricted content pattern detected in ingestion context: {pat.pattern}")
        for pat in self._CONFIDENTIAL_PATTERNS:
            if pat.search(s):
                metadata["classification"] = "confidential"

    def _scan_security_patterns(self, data: Any, metadata: Dict[str, Any]) -> None:
        """Scan values in data recursively for restricted credentials or confidential markers."""
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                self._scan_string_security(item, metadata)
            elif isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)

    @staticmethod
    def _unpack_ingestion_context(ingestion_ctx: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        roster_cand = ingestion_ctx.get("roster") or ingestion_ctx.get("attendees") or ingestion_ctx.get("attendee_roster")
        if roster_cand:
            metadata["roster"] = roster_cand
        lex_cand = ingestion_ctx.get("domain_lexicon") or ingestion_ctx.get("lexicon_map")
        if lex_cand:
            metadata["lexicon_map"] = lex_cand
        col_cand = ingestion_ctx.get("collateral_context") or ingestion_ctx.get("slides")
        if col_cand:
            metadata["collateral_context"] = col_cand
        hyp_cand = (
            ingestion_ctx.get("operator_hypothesis")
            or ingestion_ctx.get("executive_tensions")
            or ingestion_ctx.get("tensions")
        )
        if hyp_cand:
            metadata["operator_hypothesis"] = hyp_cand
        for k in ("date", "time", "meeting_name"):
            if k in ingestion_ctx and k not in metadata:
                metadata[k] = ingestion_ctx[k]

    def execute(self, l0_context: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing C0 (UNDIARIZED): Ingestion Context & Roster Gate")
        metadata = dict(l0_context.get("metadata", {}))
        assert_no_u0_hitl_bypass(metadata)
        verify_mandatory_attendee_confirmation(metadata, l0_context.get("transcript_text", ""))

        ingestion_ctx = metadata.get("ingestion_context")
        if isinstance(ingestion_ctx, dict):
            assert_no_u0_hitl_bypass(ingestion_ctx)
            sealed_hash = hashlib.sha256(
                json.dumps(ingestion_ctx, sort_keys=True).encode("utf-8")
            ).hexdigest()
            metadata["ingestion_context_hash"] = sealed_hash
            self._scan_security_patterns(ingestion_ctx, metadata)
            self._unpack_ingestion_context(ingestion_ctx, metadata)

        # Scan general metadata for security patterns
        self._scan_security_patterns(metadata.get("prior_context", ""), metadata)

        roster = metadata.get("roster") or metadata.get("attendees")

        if not roster:
            platform = metadata.get("platform", "an unidentified recording source")
            prompt = self.HITL_PROMPT_TEMPLATE.format(platform=platform)
            roster_options = [
                {
                    "label": "(Recommended) Provide attendee roster via metadata JSON — Confidence: HIGH (95%)",
                    "text": "Provide attendee roster via metadata JSON",
                    "confidence": 0.95,
                    "confidence_score": 0.95,
                    "confidence_level": "HIGH (95%)",
                    "tier": "HIGH",
                    "is_recommended": True,
                },
                {
                    "label": "Extract roster from calendar invite or meeting collateral — Confidence: MEDIUM (75%)",
                    "text": "Extract roster from calendar invite or meeting collateral",
                    "confidence": 0.75,
                    "confidence_score": 0.75,
                    "confidence_level": "MEDIUM (75%)",
                    "tier": "MEDIUM",
                    "is_recommended": False,
                },
            ]
            raise HITLRequiredError(
                prompt,
                hitl_type="roster_gate",
                missing_fields=["roster"],
                options=roster_options,
                prompt=prompt,
            )

        if roster:
            pre_roles = _extract_pre_enrichment_roles(roster)
            metadata["pre_enrichment_roles"] = pre_roles
            _check_hitl_role_clarifications(pre_roles, metadata)
            _enrich_roster(roster, metadata)

        l0_context["metadata"] = metadata
        l0_context["conformance_checked"] = True
        l0_context["roster_gate_cleared"] = True
        l0_context["context_envelope_cleared"] = True
        logger.info("C0 Ingestion Context Gate: cleared — verified roster present")
        return l0_context


# Canonical alias for pipeline clarity
C0ContextEnvelopeGate = C0RosterContextGate
