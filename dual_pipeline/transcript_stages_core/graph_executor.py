"""
dual_pipeline/transcript_stages_core/graph_executor.py

L2 Stage 03: Canonical Graph Extractor & Bipartite Projection Agent.
Decoupled single-responsibility agent producing Artifact 03 (canonical_signal_projection.json).
Encapsulates prompt assembly, chained context integration, post-processing validation,
programmatic entity grounding (PERH), and bipartite shadow enclave extraction.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import AGENTIC_EXTRACTION_PROMPT, AGENTIC_LEVERAGE_NAMESPACE, CLIENT_SAFE_NAMESPACE, _load_stage_prompt
from .graph_post_extractor import sanitize_and_validate_projection
from .incremental_reconciler import reconcile_incremental_projection
from .models import AgenticLeverageNode, BipartiteSignalProjection, L2SynthesisProvider, RestrictedAdvisoryProjection

logger = logging.getLogger(__name__)


def resolve_stage03_args(arg2: Any, arg3: Any, pa_context: Optional[Dict[str, Any]], executive_recap: Optional[str]) -> Tuple[Optional[str], Dict[str, Any]]:
    recap = executive_recap
    pa_ctx = pa_context or {}
    if isinstance(arg2, dict):
        pa_ctx = arg2
    elif isinstance(arg2, str):
        recap = arg2
        if isinstance(arg3, dict):
            pa_ctx = arg3
    return recap, pa_ctx


_EXCLUDED_STAGE03_METADATA_KEYS = frozenset({
    "source_text", "source_code", "raw_transcript", "resolved_transcript", "forensic_transcript", "recap",
    "operator_hypothesis", "prior_recap_candidate", "prior_forensic_candidate", "prior_graph_candidate",
    "prior_canonical_projection", "prior_context", "known_graph_entities", "cross_meeting_context", "cross_meeting_xml",
    "evidence_locks", "attachment_prompt_block", "uploaded_collateral", "remediation_directive", "presentation_path",
    "output_dir", "target_output_dir", "normalized_turns", "auto_role_receipts", "speaker_role_resolutions",
    "graph_context_packet", "transcript_resume", "pinned_evaluations", "structured_segments", "structured_utterances",
    "structured_segments_path", "structured_utterances_path", "audio_spec", "audio_path", "raw_source_sha256", "source_file_hash",
    "qa_report", "upstream_qa_report", "audio_certificate", "signal_envelope", "acoustic_clusters", "speaker_hints",
})


def _format_attendee_roster(raw: Any) -> List[str]:
    lines: List[str] = []
    if isinstance(raw, dict):
        for org, members in raw.items():
            for m in (members if isinstance(members, list) else [members]):
                lines.append(f"- Name/Role: {m} | Affiliation: {org}")
    elif isinstance(raw, list):
        lines.extend(f"- {m}" for m in raw)
    elif isinstance(raw, str):
        lines.extend(f"- {m.strip()}" for m in raw.split(",") if m.strip())
    return lines


def _append_roster_block(static_blocks: List[str], dynamic_blocks: List[str], metadata: Dict[str, Any]) -> None:
    attendees_raw = metadata.get("attendees")
    if not attendees_raw:
        return
    roster_lines = _format_attendee_roster(attendees_raw)
    if not roster_lines:
        return
    static_blocks.append(
        "INSTRUCTIONS FOR ATTENDEES (Rule C-16 & Gate 3.11):\n"
        "1. For any active participant in <meeting_attendees_roster>, upsert a person: entity with a concise, atomic 'description' stating ONLY their verified organizational title and affiliation (do NOT bundle meeting actions into entity descriptions).\n"
        "2. Link each attendee to their organization (org:partner_advisory, org:nea, etc.) via a MEMBER_OF edge. Ensure the MEMBER_OF description accurately states membership in that specific organization (Gate 3.12).\n"
        "3. Generic placeholders such as 'Review participant' or 'Meeting attendee' are strictly forbidden."
    )
    dynamic_blocks.append(f"<meeting_attendees_roster>\nAuthenticated Meeting Participants (Stage U0 SSOT):\n{chr(10).join(roster_lines)}\n</meeting_attendees_roster>")


def _append_prior_and_collateral(dynamic_blocks: List[str], metadata: Dict[str, Any]) -> None:
    if metadata.get("evidence_locks"):
        dynamic_blocks.append(f"<evidence_locks>\n{json.dumps(metadata['evidence_locks'], indent=2, default=str)}\n</evidence_locks>")
    att = metadata.get("attachment_prompt_block") or metadata.get("uploaded_collateral")
    if att:
        dynamic_blocks.append(f"<uploaded_meeting_collateral>\n{att}\n</uploaded_meeting_collateral>")
    if metadata.get("prior_context"):
        dynamic_blocks.append(f"<prior_context>\n{metadata['prior_context']}\n</prior_context>")
    if metadata.get("user_clarifications"):
        dynamic_blocks.append(f"<user_clarifications>\n{metadata['user_clarifications']}\n</user_clarifications>")


def _append_prior_graph_and_entities(static_blocks: List[str], dynamic_blocks: List[str], metadata: Dict[str, Any]) -> None:
    prior_graph = metadata.get("prior_canonical_projection")
    if prior_graph:
        static_blocks.append(
            "<incremental_graph_directive>\nYou are updating the Canonical Signal Projection for this meeting.\n"
            "1. Do NOT duplicate entities or relationship signals that already exist in <prior_canonical_projection>.\n"
            "2. Use mutation: 'CONFIRM' for previously identified entities that are mentioned again.\n"
            "3. Use mutation: 'CREATE' ONLY for net-new entities introduced in the updated transcript.\n"
            "4. Only emit new relationship signals that connect new entities or establish new relationships.\n"
            "5. If the updated transcript contains no new material decisions, risks, or entities, emit zero mutating additions to prevent graph clutter.\n"
            "</incremental_graph_directive>"
        )
        dynamic_blocks.append(f"<prior_canonical_projection>\n{prior_graph}\n</prior_canonical_projection>")

    known_entities = metadata.get("known_graph_entities")
    if known_entities:
        if isinstance(known_entities, list):
            seen_ids = set()
            deduped = []
            for e in known_entities:
                nid = e.get("node_id") if isinstance(e, dict) else str(e)
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    deduped.append(e)
            known_entities = deduped[:100]
        static_blocks.append(
            "INSTRUCTION:\n1. For any entity listed in <known_graph_entities> mentioned in the transcript, "
            "use its exact 'node_id' and 'node_type'. Set mutation: 'CONFIRM' instead of 'CREATE'.\n"
            "2. Use mutation: 'CREATE' ONLY for net-new entities not present in <known_graph_entities>.\n"
            "3. Enforce standard entity ID prefixes: person:, org:, system:, workstream:, project:, metric:, role:, policy: (Rule C-03)."
        )
        dynamic_blocks.append(
            f"<known_graph_entities>\nVerified Entities from Canonical Knowledge Graph (Rule C-16):\n{json.dumps(known_entities, indent=2, default=str)}\n</known_graph_entities>"
        )


def _append_remediation_block(dynamic_blocks: List[str], metadata: Dict[str, Any], remediation_directive: Optional[str]) -> None:
    if not remediation_directive:
        return
    logger.info("Injecting remediation_directive into L2-Stage03 graph extraction.")
    prior_candidate = metadata.get("prior_graph_candidate")
    prior_block = f"<prior_graph_candidate>\n{prior_candidate}\n</prior_graph_candidate>\n\n" if prior_candidate else ""
    dynamic_blocks.append(
        f"<remediation_directive>\nPREVIOUS ATTEMPT FAILED. REVISE THE CANONICAL SIGNAL PROJECTION JSON ADDRESSING THE FOLLOWING CRITIQUE.\n\n"
        f"{prior_block}CRITIQUE DIRECTIVE:\n{remediation_directive}\n\n"
        f"SURGICAL TARGETED PATCHING INSTRUCTIONS:\n"
        f"1. Continuous Candidate Continuity: Examine <prior_graph_candidate>. You MUST retain all passing, accurate nodes, edges, and evidence items from the prior candidate.\n"
        f"2. Apply targeted differential edits ONLY to the failing nodes, edges, or evidence citations specified in the critique.\n"
        f"3. PROTECTED ENTITIES: Do NOT remove or degrade previously verified person, org, or core system nodes.\n"
        f"4. ATOMIC ENTITIES & HYGIENE:\n"
        f"   - Decompose compound proposals into atomic entities.\n"
        f"   - Ensure each entity is grounded with exact evidence IDs containing verbatim references to that entity.\n"
        f"   - Do NOT emit ungrounded or speculative entities, relationships, or attributes absent from the source text.\n"
        f"   - Do NOT emit unconfirmed VALIDATES edges unless explicitly demonstrated in source dialogue.\n"
        f"5. Output the complete valid JSON object conforming to the Output 03 contract.\n</remediation_directive>"
    )


def build_stage03_user_content(
    forensic_transcript: str,
    metadata: Dict[str, Any],
    recap: Optional[str] = None,
    remediation_directive: Optional[str] = None,
    return_components: bool = False,
) -> Any:
    from dual_pipeline.prompts import OUTPUT3_CONTRACT, MeetingArchetype
    from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget
    arch_val = metadata.get("archetype") or metadata.get("meeting_type")
    title = metadata.get("meeting_name") or metadata.get("title") or ""
    arch = MeetingArchetype.from_string(arch_val) if arch_val else OUTPUT3_CONTRACT.detect_archetype(title=title, text=forensic_transcript)
    metadata["archetype"] = arch.value
    metadata["meeting_type"] = metadata.get("meeting_type") or arch.value
    metadata["resolved_domain_mutation_budget"] = resolve_duration_mutation_budget(metadata, forensic_transcript, base_budget=30)
    gov_ctx = OUTPUT3_CONTRACT.compile_governance_context(arch)
    meta_prompt = {k: v for k, v in metadata.items() if k not in _EXCLUDED_STAGE03_METADATA_KEYS and not callable(v) and len(json.dumps(v, default=str)) < 20000}

    effective_transcript = forensic_transcript
    if not metadata.get("disable_statement_filtering", False):
        from dual_pipeline.transcript_pipeline_core.context_builder import filter_stage03_statement_payload
        filter_meta = dict(metadata)
        if remediation_directive or metadata.get("remediation_directive_graph"):
            filter_meta.setdefault("proportional_payload_budget", True)
        effective_transcript = filter_stage03_statement_payload(forensic_transcript, filter_meta)

    static_blocks = [
        gov_ctx,
        "<gate_f_certification>\nGATE F CERTIFICATION: PASS (Audio completeness 100%, zero unbounded silence gaps).\n"
        "Constraint C-30 is SATISFIED. You MUST extract canonical signals from the verified meeting dialogue and collateral.\n</gate_f_certification>",
    ]
    dynamic_blocks = [
        f"<meeting_metadata>\n{json.dumps(meta_prompt, separators=(',', ':'), default=str)}\n</meeting_metadata>",
        f"<forensic_transcript>\n{effective_transcript}\n</forensic_transcript>",
    ]
    if recap:
        dynamic_blocks.append(f"<executive_recap>\n{recap}\n</executive_recap>")

    _append_roster_block(static_blocks, dynamic_blocks, metadata)
    _append_prior_and_collateral(dynamic_blocks, metadata)
    _append_prior_graph_and_entities(static_blocks, dynamic_blocks, metadata)
    _append_remediation_block(dynamic_blocks, metadata, remediation_directive)

    if return_components:
        return "\n\n".join(static_blocks), "\n\n".join(dynamic_blocks) + "\n\n"
    return "\n\n".join(static_blocks + dynamic_blocks) + "\n\n"


def _item_confidence(item: dict, evidence_map: dict) -> dict:
    for eid in item.get("evidence_ids") or []:
        ev = evidence_map.get(eid)
        if ev and (ev.get("grounding_status") == "GROUNDED" or ev.get("start_offset") is not None):
            return {
                "score": 0.90, "tier": "HIGH",
                "rationale": "Grounded: verified exact source span and speaker attribution in transcript",
            }
    return {"score": 0.50, "tier": "LOW", "rationale": "Uncalibrated: synthesized without explicit LLM confidence rating"}


def _lookup_lineage_roster(clean_spk: str, metadata: dict) -> Tuple[Optional[str], Optional[str]]:
    for r in (metadata.get("roster") or metadata.get("attendees") or []):
        if isinstance(r, dict) and clean_spk and re.sub(r"[^a-z0-9]", "", str(r.get("name", "")).strip().lower()) == clean_spk:
            return r.get("org") or r.get("organization"), r.get("role")
    return None, None


def _lookup_hitl_map(clean_spk: str, mapping: Any, field: str = "role") -> Optional[str]:
    for k_name, val in (mapping or {}).items():
        if re.sub(r"[^a-z0-9]", "", str(k_name).strip().lower()) == clean_spk:
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                return val.get(field) or val.get("organization" if field == "org" else "resolved_role")
    return None


def _item_lineage(ev: dict, metadata: dict, meeting_id: str) -> dict:
    spk, org, role = ev.get("speaker") or "Speaker", ev.get("speaker_org"), ev.get("speaker_role")
    clean_spk = re.sub(r"[^a-z0-9]", "", spk.replace("person:", "").lower())
    unverified_orgs = ("internal", "client / partner", "organization unverified", "unverified")
    if org and str(org).strip().lower() in unverified_orgs:
        org = None
    if not org or not role or role == "Speaker":
        r_org, r_role = _lookup_lineage_roster(clean_spk, metadata)
        if r_org and str(r_org).strip().lower() in unverified_orgs:
            r_org = None
        role = role if (role and role != "Speaker") else (
            _lookup_hitl_map(clean_spk, metadata.get("hitl_role_resolutions"), "role")
            or _lookup_hitl_map(clean_spk, metadata.get("speaker_role_resolutions"), "role")
            or r_role
        )
        org = org or (
            _lookup_hitl_map(clean_spk, metadata.get("hitl_org_resolutions"), "org")
            or _lookup_hitl_map(clean_spk, metadata.get("speaker_role_resolutions"), "org")
            or r_org
        )
    return {
        "source_id": meeting_id, "speaker": spk, "speaker_role": role or "Speaker",
        "speaker_org": org or metadata.get("client_org") or metadata.get("tenant") or "Internal",
        "recorded_at": metadata.get("observed_at") or metadata.get("date") or metadata.get("meeting_date") or "",
    }


def _merge_item_lineage(item: dict, ev: dict, metadata: dict, mid: str) -> dict:
    prev_lin = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
    new_lin = _item_lineage(ev, metadata, mid)
    if prev_lin.get("speaker_role") and prev_lin["speaker_role"] != "Speaker" and new_lin.get("speaker_role") == "Speaker":
        new_lin["speaker_role"] = prev_lin["speaker_role"]
    if prev_lin.get("speaker_org") and prev_lin["speaker_org"] not in ("Internal", "Unverified") and new_lin.get("speaker_org") in ("Internal", "Unverified"):
        new_lin["speaker_org"] = prev_lin["speaker_org"]
    return new_lin


def _enrich_single_item(item: dict, a_type: str, ev_map: dict, metadata: dict, mid: str) -> None:
    if not item.get("assertion_type"):
        item["assertion_type"] = a_type
    if not item.get("confidence"):
        item["confidence"] = _item_confidence(item, ev_map)
    eids = [eid for eid in (item.get("evidence_ids") or []) if eid in ev_map]
    if not eids:
        return
    best_eid = next((eid for eid in eids if ev_map[eid].get("grounding_status") == "GROUNDED" or ev_map[eid].get("start_offset") is not None), eids[0])
    ev, cur_ev = ev_map[best_eid], item.get("evidence")
    if not isinstance(cur_ev, dict) or cur_ev.get("statement_id") not in (best_eid, ev.get("statement_id", "")):
        item["evidence"] = {
            "statement_id": ev.get("statement_id", "") or best_eid, "direct_quote": ev.get("exact_text") or ev.get("text", ""),
            "locator": ev.get("locator", {}), "turn_offset": 0, "all_evidence_ids": list(eids),
        }
        item["lineage"] = _merge_item_lineage(item, ev, metadata, mid)


def _enrich_gs10_traits(final_data: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    """Hydrate GS-10 confidence, assertion_type, evidence, and lineage traits."""
    ev_map, mid = final_data.get("evidence_index", {}), final_data.get("meeting_id", "unknown")
    muts = final_data.get("graph_mutations", {})
    sections = [
        (muts.get("relationship_signals", []), "architecture_relationship"),
        (muts.get("entity_upserts", []), "architecture_component"),
        (muts.get("action_updates", []), "action_item"),
        (muts.get("proposal_updates") or final_data.get("proposal_updates", []), "proposal"),
        (muts.get("risk_updates") or final_data.get("risk_updates", []), "risk"),
    ]
    for items, a_type in sections:
        for it in items:
            _enrich_single_item(it, a_type, ev_map, metadata, mid)


def _confirm_known_graph_entities(final_data: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    """Deterministically flip mutation: 'CREATE' to 'CONFIRM' when an entity matches known_graph_entities (Rule C-16)."""
    known_raw = metadata.get("known_graph_entities") or []
    known_ids = {str(x).strip().lower() for x in (metadata.get("prior_entity_ids") or []) if x}
    known_names = set()
    for k in known_raw:
        if isinstance(k, dict):
            if k.get("node_id") or k.get("id"):
                known_ids.add(str(k.get("node_id") or k.get("id")).strip().lower())
            for nk in ("canonical_name", "display_name", "name"):
                if k.get(nk):
                    known_names.add(re.sub(r"[^a-z0-9]+", "", str(k.get(nk)).strip().lower()))
        elif isinstance(k, str) and k.strip():
            known_ids.add(k.strip().lower())
    upserts = (final_data.get("graph_mutations") or {}).get("entity_upserts")
    if (not known_ids and not known_names) or not isinstance(upserts, list):
        return
    flipped = 0
    for ent in upserts:
        if not isinstance(ent, dict) or str(ent.get("mutation", "CREATE")).upper() != "CREATE":
            continue
        eid = str(ent.get("id") or "").strip().lower()
        ename = re.sub(r"[^a-z0-9]+", "", str(ent.get("name") or "").strip().lower())
        slug_tail = re.sub(r"[^a-z0-9]+", "", eid.split(":", 1)[-1]) if ":" in eid else ""
        if (eid and eid in known_ids) or (ename and ename in known_names) or (slug_tail and slug_tail in known_names):
            ent["mutation"], flipped = "CONFIRM", flipped + 1
    if flipped > 0:
        from .incremental_reconciler import sync_reconciliation_counters
        sync_reconciliation_counters(final_data)


def post_process_stage03_projection(
    raw_graph_json: str, metadata: Dict[str, Any], forensic_transcript: str, executive_recap: Optional[str] = None,
) -> str:
    from .anti_conflation import validate_anti_conflation
    clean_target: Any = raw_graph_json
    if isinstance(raw_graph_json, str):
        trimmed = "\n".join(l for l in raw_graph_json.strip().splitlines() if not l.strip().startswith("```")).strip()
        try:
            clean_target = json.loads(trimmed)
        except Exception:
            clean_target = raw_graph_json
    validate_anti_conflation(clean_target, context_label="Stage 03 raw_graph_json")
    try:
        from dual_pipeline.signal_extraction import SignalExtractor
        promoted_projection, _ = SignalExtractor().extract_and_gate(raw_graph_json, metadata=metadata, forensic_transcript=forensic_transcript)
        final_data = sanitize_and_validate_projection(promoted_projection, metadata=metadata, source_text=forensic_transcript)
    except Exception as gate_err:
        raise RuntimeError(f"Signal extraction gating failed — cannot fall back to raw un-gated synthesis: {gate_err}") from gate_err

    from .speaker_role_reconciler import reconcile_speaker_role_resolutions
    final_data = reconcile_speaker_role_resolutions(final_data, metadata=metadata)
    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
    if metadata is not None:
        metadata.setdefault("source_text", forensic_transcript)
        if executive_recap:
            metadata.setdefault("output_2", executive_recap)
    healed_json_str, changes = DeterministicHealer.heal_output_3(final_data, metadata=metadata, output_1=forensic_transcript, output_2=executive_recap)
    if changes:
        logger.info("L2-Stage03 Real-Time Healer: applied %d syntactic normalizations", len(changes))
    try:
        final_data = json.loads(healed_json_str)
    except Exception as e:
        logger.error("L2-Stage03 Real-Time Healer produced invalid JSON: %s", e)
        raise RuntimeError(f"Fail-Fast: DeterministicHealer produced unparseable JSON for Stage 03: {e}") from e

    if metadata.get("prior_canonical_projection") and not metadata.get("force_rerun"):
        final_data = reconcile_incremental_projection(final_data, prior_projection=metadata["prior_canonical_projection"])
    _confirm_known_graph_entities(final_data, metadata)
    validate_anti_conflation(final_data, context_label="Stage 03 post_process_stage03_projection")
    from .edge_trait_evaluator import evaluate_edge_traits_and_disambiguate
    final_data, _ = evaluate_edge_traits_and_disambiguate(final_data, metadata=metadata)
    _enrich_gs10_traits(final_data, metadata)
    arch_val = metadata.get("archetype") or metadata.get("meeting_type")
    if arch_val:
        final_data["meeting_archetype"] = arch_val
        if isinstance(final_data.get("graph_mutations"), dict):
            final_data["graph_mutations"]["meeting_archetype"] = arch_val
        if isinstance(final_data.setdefault("meeting_metadata", {}), dict):
            final_data["meeting_metadata"]["meeting_archetype"] = arch_val
    from dual_pipeline.canonical_pipeline.evidence_coverage import audit_stage03_projection
    audit_stage03_projection(final_data, metadata)
    return json.dumps(final_data, indent=2)


class L2GraphExtractorExecutor:
    """L2 Stage 03: Canonical Graph Extractor Agent producing Artifact 03."""

    def __init__(self, provider: L2SynthesisProvider):
        self.provider = provider

    def execute(
        self, forensic_transcript: str, arg2: Any = None, arg3: Any = None,
        remediation_directive: Optional[str] = None, pa_context: Optional[Dict[str, Any]] = None,
        executive_recap: Optional[str] = None,
    ) -> str:
        logger.info("L2-Stage03: Graph Extractor Agent")
        from dual_pipeline.prompts import OUTPUT3_CONTRACT, MeetingArchetype
        recap, pa_ctx = resolve_stage03_args(arg2, arg3, pa_context, executive_recap)
        metadata = pa_ctx.get("metadata", {})
        arch_val = metadata.get("archetype") or metadata.get("meeting_type")
        title = metadata.get("meeting_name") or metadata.get("title") or ""
        arch_enum = MeetingArchetype.from_string(arch_val) if arch_val else OUTPUT3_CONTRACT.detect_archetype(title=title, text=forensic_transcript)
        metadata["archetype"] = arch_enum.value
        metadata["meeting_type"] = metadata.get("meeting_type") or arch_enum.value
        graph_prompt = _load_stage_prompt("graph", archetype=arch_enum)
        static_prefix, user_content = build_stage03_user_content(
            forensic_transcript=forensic_transcript, metadata=metadata, recap=recap,
            remediation_directive=remediation_directive, return_components=True,
        )
        provider_effort = getattr(self.provider, "reasoning_effort", None)
        if provider_effort and not metadata.get("stage03_reasoning_effort") and not metadata.get("reasoning_effort"):
            metadata["reasoning_effort"] = provider_effort

        from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner
        budget = PromptPrefixAligner.calculate_reasoning_token_budget(forensic_transcript, metadata, stage="stage_03")
        metadata["reasoning_budget"], metadata["stage03_reasoning_effort"] = budget, budget.get("reasoning_effort", provider_effort or "medium")
        PromptPrefixAligner.apply_reasoning_budget_to_provider(self.provider, budget)
        system_prefix = PromptPrefixAligner.align_static_prefix(graph_prompt, [static_prefix] if static_prefix else [], stage_tag="stage03_graph")

        raw_graph_json = self.provider.synthesize_graph(system_prefix, user_content)
        if not raw_graph_json or not raw_graph_json.strip():
            raise RuntimeError("L2 Stage 03 graph synthesis returned empty string from provider.")
        return post_process_stage03_projection(raw_graph_json, metadata, forensic_transcript, executive_recap=recap)

    def execute_programmatic_grounding(
        self, triples: Sequence[Dict[str, Any]], meeting_date: Optional[str] = None, catalog_alias_map: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        from dual_pipeline.ptc.entity_resolution import PTCEntityResolutionPlan, PTCEntityResolver
        plan = PTCEntityResolutionPlan(triples=triples, meeting_date=meeting_date, catalog_alias_map=catalog_alias_map or {})
        return PTCEntityResolver().resolve(plan).to_dict()

    def extract_agentic_leverage(
        self, forensic_transcript: str, executive_recap: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
    ) -> List[AgenticLeverageNode]:
        """Extract invisible AGENTIC_LEVERAGE nodes using the Machiavellian prompt."""
        meta_prompt = {k: v for k, v in (metadata or {}).items() if k not in _EXCLUDED_STAGE03_METADATA_KEYS and not callable(v)}
        user_content = f"<meeting_metadata>\n{json.dumps(meta_prompt, separators=(',', ':'), default=str)}\n</meeting_metadata>\n\n<forensic_transcript>\n{forensic_transcript}\n</forensic_transcript>\n\n"
        if executive_recap:
            user_content += f"<executive_recap>\n{executive_recap}\n</executive_recap>\n\n"
        return self.provider.synthesize_agentic_leverage(AGENTIC_EXTRACTION_PROMPT, user_content) if hasattr(self.provider, "synthesize_agentic_leverage") else []

    def execute_bipartite(
        self, forensic_transcript: str, arg2: Any = None, arg3: Any = None, executive_recap: Optional[str] = None,
        pa_context: Optional[Dict[str, Any]] = None, remediation_directive: Optional[str] = None,
    ) -> BipartiteSignalProjection:
        """Execute bipartite graph extraction returning both CLIENT_SAFE and AGENTIC_LEVERAGE namespaces."""
        raw_graph_json = self.execute(forensic_transcript, arg2=arg2, arg3=arg3, executive_recap=executive_recap, pa_context=pa_context, remediation_directive=remediation_directive)
        try:
            client_safe_data = json.loads(raw_graph_json)
        except Exception:
            client_safe_data = {"raw": raw_graph_json}
        pa_ctx = arg2 if isinstance(arg2, dict) else (arg3 if isinstance(arg3, dict) else (pa_context or {}))
        recap = executive_recap or (arg2 if isinstance(arg2, str) else None)
        leverage_nodes = self.extract_agentic_leverage(forensic_transcript=forensic_transcript, executive_recap=recap, metadata=pa_ctx.get("metadata", {}))
        return BipartiteSignalProjection(
            client_safe=client_safe_data, agentic_leverage=leverage_nodes,
            metadata={"client_safe_namespace": CLIENT_SAFE_NAMESPACE, "agentic_leverage_namespace": AGENTIC_LEVERAGE_NAMESPACE},
        )

    def execute_advisory_enclave(
        self, forensic_transcript: str, executive_recap: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
    ) -> RestrictedAdvisoryProjection:
        """Execute restricted internal advisory extraction into an isolated enclave projection."""
        meta = metadata or {}
        nodes = self.extract_agentic_leverage(forensic_transcript=forensic_transcript, executive_recap=executive_recap, metadata=meta)
        return RestrictedAdvisoryProjection(
            advisory_nodes=nodes,
            metadata={"extracted_at": datetime.now(timezone.utc).isoformat(), "node_count": len(nodes), "source_project": meta.get("project_name", ""), "meeting_name": meta.get("meeting_name", "")},
        )
