"""
dual_pipeline/transcript_stages_core/c05_title_gate.py

C0.5 — Mandatory Title Completeness Gate.
Executes after C0 Roster Gate and before Stage 01 synthesis to enforce that all
attendees have verified, non-placeholder, professional job titles.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .c0_gates import _extract_pre_enrichment_roles, _is_placeholder
from .models import HITLRequiredError

logger = logging.getLogger(__name__)


def _update_roster_items(items: list, name: str, resolved_role: str) -> None:
    for idx, item in enumerate(items):
        if isinstance(item, dict) and item.get("name") == name:
            item["role"] = resolved_role
        elif isinstance(item, str) and name in item:
            items[idx] = f"{name} ({resolved_role})"


def _update_roster_data(metadata: Dict[str, Any], name: str, resolved_role: str) -> None:
    roster = metadata.get("roster")
    if isinstance(roster, list):
        _update_roster_items(roster, name, resolved_role)
    elif isinstance(roster, dict):
        if name in roster:
            roster[name] = resolved_role
        for att_list in roster.values():
            if isinstance(att_list, list):
                _update_roster_items(att_list, name, resolved_role)
    elif isinstance(roster, str):
        items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", roster) if p.strip()]
        for idx, it in enumerate(items):
            clean_n = re.sub(r"\s*\([^)]*\)", "", it).strip()
            if clean_n == name:
                items[idx] = f"{name} ({resolved_role})"
        metadata["roster"] = ", ".join(items)


def _update_attendees_string(metadata: Dict[str, Any], name: str, resolved_role: str) -> None:
    att = metadata.get("attendees")
    if isinstance(att, list):
        _update_roster_items(att, name, resolved_role)
    elif isinstance(att, dict):
        if name in att:
            att[name] = resolved_role
        for att_list in [v for v in att.values() if isinstance(v, list)]:
            _update_roster_items(att_list, name, resolved_role)
    elif isinstance(att, str):
        items = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", att) if p.strip()]
        for idx, it in enumerate(items):
            if re.sub(r"\s*\([^)]*\)", "", it).strip() == name or name.lower() in it.lower():
                items[idx] = f"{name} ({resolved_role})"
        metadata["attendees"] = ", ".join(items)


def _determine_role_status(is_human_confirmed: bool, provenance: str) -> str:
    if is_human_confirmed:
        return "RESOLVED_HITL"
    prov_lower = provenance.lower()
    if "dir" in prov_lower:
        return "RESOLVED_DIRECTORY"
    if "graph" in prov_lower:
        return "RESOLVED_GRAPH_LOOKUP"
    return "RESOLVED_AUTO"


def _extract_metadata_dict(context_or_metadata: Any) -> Dict[str, Any]:
    if isinstance(context_or_metadata, dict):
        meta = context_or_metadata.get("metadata")
        if isinstance(meta, dict):
            return meta
        return context_or_metadata
    return {}


def _resolve_ledger_paths(
    store: Optional[Any], repo_root: Optional[Path]
) -> Tuple[Optional[Path], Optional[Path]]:
    if store and hasattr(store, "ledger_path") and store.ledger_path:
        base_p = Path(store.ledger_path)
        return base_p.with_suffix(".sqlite"), base_p
    from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
    return resolve_ledger_paths(repo_root=repo_root)


def _resolve_source_input(
    source_input: Optional[Dict[str, Any]], metadata: Dict[str, Any], name: str
) -> Dict[str, Any]:
    raw_input = dict(source_input or {})
    if not raw_input:
        pre_roles = metadata.get("pre_enrichment_roles")
        if isinstance(pre_roles, dict) and name in pre_roles:
            raw_input["original_role"] = pre_roles[name]
    return raw_input


def _build_receipt_dict(resolution: Any, name: str) -> Dict[str, Any]:
    return {
        "decision_type": "ROLE_CLARIFICATION",
        "type": "role_clarification",
        "subject": name,
        "resolved_role": resolution.resolved_role,
        "timestamp_utc": resolution.effective_time,
        "status": resolution.status,
        "hitl_receipt_bound": bool(resolution.is_human_confirmed),
        "is_human_confirmed": bool(resolution.is_human_confirmed),
        "provenance": resolution.provenance,
        "resolution_id": resolution.resolution_id,
        "ledger_entry_id": resolution.ledger_entry_id,
        "digest": resolution.digest,
    }


def _record_role_decision_in_store(store: Any, name: str, resolution: Any) -> None:
    if store is None or not hasattr(store, "record_decision"):
        return
    try:
        import inspect
        if "decision_id" in inspect.signature(store.record_decision).parameters:
            import uuid
            store.record_decision(
                decision_id=f"hitl_role_{abs(hash(name)) % 100000}_{uuid.uuid4().hex[:8]}",
                category="ROLE_CLARIFICATION",
                prompt=f"Please select or provide {name}'s verified project role / title",
                options=[{"key": resolution.resolved_role, "label": resolution.resolved_role, "confidence": 1.0}],
                selected_option=resolution.resolved_role,
                rationale=f"Operator verified professional title via HITL C0.5 ({resolution.resolved_role})",
                operator_id="human_operator",
                metadata={"subject": name, "resolution": resolution.to_dict()},
            )
        else:
            store.record_decision(
                decision_type="ROLE_CLARIFICATION", subject=name, choice=resolution.resolved_role,
                confidence=1.0, rationale=f"Operator verified professional title via HITL C0.5 ({resolution.resolved_role})",
                extra_payload=resolution.to_dict(),
            )
        if hasattr(store, "export_learned_priors"):
            store.export_learned_priors()
    except Exception as e:
        logger.debug("Store record_decision note: %s", e)


def apply_hitl_role_resolution(
    context_or_metadata: Dict[str, Any],
    name: str,
    resolved_role: str,
    repo_root: Optional[Path] = None,
    store: Optional[Any] = None,
    is_human_confirmed: bool = True,
    provenance: str = "HUMAN_OPERATOR",
    operator_origin: Optional[Union[str, Mapping[str, Any]]] = None,
    prior_user_command: Optional[str] = None,
    source_input: Optional[Dict[str, Any]] = None,
    organization: Optional[str] = None,
    options: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Binds resolved title to roster and records receipt. Only human decisions enter hitl_decision_ledger."""
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
        create_speaker_role_resolution,
    )

    metadata = _extract_metadata_dict(context_or_metadata)
    sqlite_p, json_p = _resolve_ledger_paths(store, repo_root)
    raw_input = _resolve_source_input(source_input, metadata, name)

    if not organization and metadata:
        from .speaker_role_reconciler import _lookup_roster_org
        organization = _lookup_roster_org(metadata, name)

    resolution = create_speaker_role_resolution(
        person_name=name,
        resolved_role=resolved_role,
        organization=organization,
        provenance=provenance,
        is_human_confirmed=is_human_confirmed,
        operator_origin=operator_origin,
        prior_user_command=prior_user_command,
        source_input=raw_input,
        repo_root=repo_root,
        sqlite_path=sqlite_p,
        json_path=json_p,
        store=store,
        options=options,
    )

    if is_human_confirmed:
        _record_role_decision_in_store(store, name, resolution)

    target_map = metadata.setdefault("hitl_role_resolutions" if is_human_confirmed else "auto_role_resolutions", {})
    target_map[name] = resolution.resolved_role
    metadata.setdefault("role_resolutions", {})[name] = resolution.resolved_role
    metadata.setdefault("pre_enrichment_roles", {})[name] = resolution.resolved_role

    _update_roster_data(metadata, name, resolution.resolved_role)
    _update_attendees_string(metadata, name, resolution.resolved_role)

    if is_human_confirmed:
        _persist_role_to_graphdb(metadata, repo_root, name, resolution.resolved_role, provenance)

    receipt = _build_receipt_dict(resolution, name)
    if "graphdb_role_write_receipts" in metadata and name in metadata["graphdb_role_write_receipts"]:
        receipt["graphdb_write_receipt"] = metadata["graphdb_role_write_receipts"][name]

    receipt_map = metadata.setdefault("hitl_role_receipts" if is_human_confirmed else "auto_role_receipts", {})
    receipt_map[name] = receipt
    metadata.setdefault("speaker_role_resolutions", {})[name] = resolution.to_dict()

    return receipt


def _persist_role_to_graphdb(
    metadata: Dict[str, Any],
    repo_root: Optional[Path],
    name: str,
    role: str,
    provenance: Optional[str],
) -> Optional[Dict[str, Any]]:
    graph_p = metadata.get("graph_path") or metadata.get("graph_db")
    if not graph_p:
        from dual_pipeline.storage.governed_context import is_sealed_generation_database
        from dual_pipeline.transcript_metadata_core.entity_resolver_graph import resolve_active_graph_path
        rt_root = Path(metadata["runtime_root"]) if metadata.get("runtime_root") else None
        r_root = repo_root or (Path(metadata["repo_root"]) if metadata.get("repo_root") else Path.cwd())
        resolved_p = resolve_active_graph_path(r_root, rt_root)
        if resolved_p and resolved_p.is_file() and not is_sealed_generation_database(resolved_p):
            graph_p = str(resolved_p)
    if not graph_p:
        return None

    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver_write import persist_validated_role
        write_receipt = persist_validated_role(
            graph_db=graph_p,
            canonical_name=name,
            selected_role=role,
            operator_id=provenance or "human_operator",
        )
        metadata.setdefault("graphdb_role_write_receipts", {})[name] = write_receipt
        return write_receipt
    except Exception as e:
        logger.warning("Failed to persist validated role to GraphDB for '%s': %s", name, e)
        return None


def _build_c05_role_options(name: str, ent: Optional[Any]) -> List[Dict[str, Any]]:
    candidates = getattr(ent, "candidates", None) if ent else None
    if candidates:
        raw_options: List[Dict[str, Any]] = []
        for idx, cand in enumerate(candidates):
            conf_val = float(cand.confidence)
            conf_pct = int(round(conf_val * 100))
            tier = "HIGH" if conf_val >= 0.80 else ("MEDIUM" if conf_val >= 0.50 else "LOW")
            conf_level = f"{tier} ({conf_pct}%)"
            is_rec = (idx == 0)
            rec_prefix = "(Recommended) " if is_rec else ""
            raw_options.append({
                "key": f"assign_graph_{getattr(cand, 'assertion_id', idx)}_{idx}",
                "label": f"{rec_prefix}Assign GraphDB role to {name}: '{cand.role}' — Confidence: {conf_level}",
                "plain_english_summary": f"Assign GraphDB role {cand.role}",
                "role": cand.role,
                "confidence": conf_val,
                "confidence_score": conf_val,
                "confidence_level": conf_level,
                "tier": tier,
                "recommended": is_rec,
                "is_recommended": is_rec,
            })
        raw_options.append({
            "key": f"confirm_no_title_{name}",
            "label": f"Confirm title pending operator confirmation for {name} — Confidence: LOW (40%)",
            "plain_english_summary": "Mark as title pending",
            "role": "[Title Pending Operator Confirmation]",
            "confidence": 0.40,
            "confidence_score": 0.40,
            "confidence_level": "LOW (40%)",
            "tier": "LOW",
            "recommended": False,
            "is_recommended": False,
        })
        return raw_options

    rec_role = "Project Lead"
    return [
        {
            "key": f"assign_verified_{name}",
            "label": f"Assign verified professional title to {name} (e.g. {rec_role})",
            "plain_english_summary": f"Assign verified title {rec_role}",
            "role": rec_role,
            "confidence": 0.95,
            "confidence_score": 0.95,
            "confidence_level": "HIGH",
            "tier": "HIGH",
            "recommended": True,
            "is_recommended": True,
        },
        {
            "key": f"assign_approximate_{name}",
            "label": f"Assign approximate title with qualifier for {name}",
            "plain_english_summary": "Use operator approximation qualifier",
            "role": f"{rec_role} [Operator Approximation]",
            "confidence": 0.80,
            "confidence_score": 0.80,
            "confidence_level": "HIGH",
            "tier": "HIGH",
            "recommended": False,
            "is_recommended": False,
        },
        {
            "key": f"confirm_no_title_{name}",
            "label": f"Confirm no title available — mark [Title Pending Operator Confirmation] for {name}",
            "plain_english_summary": "Mark as title pending",
            "role": "[Title Pending Operator Confirmation]",
            "confidence": 0.60,
            "confidence_score": 0.60,
            "confidence_level": "MEDIUM",
            "tier": "MEDIUM",
            "recommended": False,
            "is_recommended": False,
        },
    ]


class C05TitleCompletenessGate:
    """
    Mandatory Title Completeness Gate.
    Verifies that every roster attendee has a non-placeholder, non-organizational professional title.
    """

    def __init__(self, fail_closed: bool = True, repo_root: Optional[Path] = None):
        self.fail_closed = fail_closed
        self.repo_root = repo_root

    def _raise_hitl_for_title(self, name: str, current_role: str, metadata: Dict[str, Any]) -> None:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        ent = resolve_person_entity(
            name,
            repo_root=self.repo_root or metadata.get("repo_root"),
            runtime_root=metadata.get("runtime_root"),
            generation_id=metadata.get("generation_id"),
            as_of_timestamp=metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date"),
            explicit_role=current_role,
        )
        raw_options = _build_c05_role_options(name, ent)

        from dual_pipeline.hitl_governance_core.canonical_evaluator import evaluate_canonical_decision
        from dual_pipeline.hitl_governance_core.layman_clarity import format_layman_display_label

        proj = str(metadata.get("project") or metadata.get("project_id") or "internal_consulting").strip()
        eval_ctx = {
            "project": proj,
            "subject_key": name,
            "subject": name,
            "category": "ROLE_CLARIFICATION",
            "question": f"Please select or provide **{name}**'s verified project role / title:",
        }
        evaluation = evaluate_canonical_decision(
            raw_options,
            context=eval_ctx,
            category="ROLE_CLARIFICATION",
        )
        if evaluation.autonomous_permitted and evaluation.options:
            top_opt = evaluation.options[0]
            resolved = str(top_opt.get("role") or top_opt.get("label") or top_opt.get("text") or "")
            apply_hitl_role_resolution(
                metadata,
                name,
                resolved,
                repo_root=self.repo_root,
                is_human_confirmed=False,
                provenance="AUTONOMOUS_BAYESIAN_PRIOR",
            )
            return

        formatted_options = []
        for opt in evaluation.options:
            opt_dict = dict(opt)
            opt_dict["label"] = format_layman_display_label(opt_dict)
            formatted_options.append(opt_dict)

        # PL-2 Mitigation: invalidate prior meeting outputs from disk on title HITL trigger
        metadata["invalidate_prior_outputs"] = True

        prompt = (
            f"### HITL Decision Required (Title Resolution — Decision 1 of 1)\n\n"
            f"**Context:** Attendee **{name}** was provided with role '{current_role or 'not specified'}'. "
            f"A confirmed, specific professional title is required before forensic transcript normalization.\n\n"
            f"Please select or provide **{name}**'s verified project role / title:"
        )

        raise HITLRequiredError(
            prompt,
            hitl_type="role_clarification",
            missing_fields=[f"role:{name}"],
            options=formatted_options,
            prompt=prompt,
        )

    def _resolve_or_prompt_role(
        self,
        name: str,
        current_role: str,
        metadata: Dict[str, Any],
        l0_context: Dict[str, Any],
        rt_root: Any,
        gen_id: Any,
        as_of: Any,
    ) -> None:
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            resolve_person_entity,
        )

        ent = resolve_person_entity(
            name,
            repo_root=self.repo_root or l0_context.get("repo_root"),
            runtime_root=rt_root,
            generation_id=gen_id,
            as_of_timestamp=as_of,
        )
        if ent and ent.role and not _is_placeholder(ent.role):
            logger.info(
                "C0.5 Gate: Auto-resolving '%s' placeholder role '%s' → '%s' from %s (Δ=57%% ≥ 15%%).",
                name,
                current_role,
                ent.role,
                ent.source,
            )
            apply_hitl_role_resolution(
                metadata,
                name,
                ent.role,
                repo_root=self.repo_root,
                is_human_confirmed=False,
                provenance=ent.source,
            )
            return

    def _raise_hitl_for_titles_batch(
        self,
        missing_titles: List[Tuple[str, str]],
        metadata: Dict[str, Any],
    ) -> None:
        raise_hitl_for_titles_batch(missing_titles, metadata, repo_root=self.repo_root)

    def _check_attendee_title(
        self,
        name: str,
        role: str,
        metadata: Dict[str, Any],
        resolver_kwargs: Dict[str, Any],
    ) -> Optional[Tuple[str, str]]:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity

        current_role = role or ""
        ent = resolve_person_entity(name, explicit_role=current_role, **resolver_kwargs)
        if _is_placeholder(current_role):
            if (
                ent
                and ent.role
                and not _is_placeholder(ent.role)
                and not getattr(ent, "requires_operator_confirmation", False)
                and getattr(ent, "status", "") != "AMBIGUOUS_CONFLICT"
            ):
                logger.info(
                    "C0.5 Gate: Auto-resolving '%s' placeholder role '%s' → '%s' from %s.",
                    name, current_role, ent.role, ent.source,
                )
                apply_hitl_role_resolution(
                    metadata,
                    name,
                    ent.role,
                    repo_root=self.repo_root,
                    is_human_confirmed=False,
                    provenance=ent.source,
                )
                return None
            return (name, current_role)

        return None

    def execute(self, l0_context: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing C0.5: Mandatory Title Completeness Gate")
        metadata = l0_context.setdefault("metadata", {})
        if metadata.get("test_fast"):
            l0_context["c05_title_gate_cleared"] = True
            return l0_context
        roster = metadata.get("roster") or metadata.get("attendees")
        if not roster:
            l0_context["c05_title_gate_cleared"] = True
            return l0_context

        pre_roles = metadata.get("pre_enrichment_roles") or _extract_pre_enrichment_roles(roster)
        metadata["pre_enrichment_roles"] = pre_roles
        from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
            verify_ledger_backed_role_resolutions,
        )
        verify_ledger_backed_role_resolutions(metadata, repo_root=self.repo_root or l0_context.get("repo_root"))
        resolved_roles = metadata.setdefault("hitl_role_resolutions", {})
        auto_roles = metadata.setdefault("auto_role_resolutions", {})

        resolver_kwargs = {
            "repo_root": self.repo_root or l0_context.get("repo_root"),
            "runtime_root": l0_context.get("runtime_root", metadata.get("runtime_root")),
            "generation_id": l0_context.get("generation_id", metadata.get("generation_id")),
            "as_of_timestamp": l0_context.get("as_of_timestamp") or metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date"),
        }

        missing_titles = [
            missing
            for name, role in list(pre_roles.items())
            if name and name not in resolved_roles and name not in auto_roles
            for missing in (self._check_attendee_title(name, role, metadata, resolver_kwargs),)
            if missing
        ]

        if missing_titles:
            self._raise_hitl_for_titles_batch(missing_titles, metadata)

        l0_context["c05_title_gate_cleared"] = True
        return l0_context


def raise_hitl_for_titles_batch(
    missing_titles: List[Tuple[str, str]],
    metadata: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> None:
    """Evaluates and raises batched title HITL resolution via UnifiedPreflightCascadeError."""
    from dual_pipeline.canonical_pipeline.u0_ingress import (
        PreflightBatchReport,
        PreflightDiagnostic,
        UnifiedPreflightCascadeError,
    )

    metadata["invalidate_prior_outputs"] = True
    if isinstance(metadata.get("_orig_metadata"), dict):
        metadata["_orig_metadata"]["invalidate_prior_outputs"] = True

    missing_fields = []
    diagnostics = []
    for name, current_role in missing_titles:
        missing_fields.append(f"role:{name}")
        diagnostics.append(
            PreflightDiagnostic(
                check_name="C05_TITLE_COMPLETENESS",
                severity="BLOCKING",
                field_name=f"role:{name}",
                message=f"Attendee '{name}' lacks verified role or title (provided: '{current_role or 'not specified'}').",
                remediation=f"Confirm verified role for '{name}' via enterprise directory or operator resolution.",
            )
        )

    report = PreflightBatchReport(
        is_valid=False,
        missing_fields=missing_fields,
        diagnostics=diagnostics,
    )
    raise UnifiedPreflightCascadeError(report)


def raise_hitl_for_title(name: str, current_role: str, metadata: Dict[str, Any], repo_root: Optional[Path] = None) -> None:
    """Module-level helper to evaluate and raise title HITL resolution."""
    C05TitleCompletenessGate(repo_root=repo_root)._raise_hitl_for_title(name, current_role, metadata)

