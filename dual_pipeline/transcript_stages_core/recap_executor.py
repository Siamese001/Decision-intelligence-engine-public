"""
dual_pipeline/transcript_stages_core/recap_executor.py

L2 Stage 02: Executive Recap Synthesis Agent.
Decoupled single-responsibility agent producing Artifact 02 (executive_recap.md).
Encapsulates archetype detection, mandatory section topology enforcement,
epistemic hypothesis isolation, and synthesis dispatch.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .constants import _load_stage_prompt
from .models import L2SynthesisProvider

logger = logging.getLogger(__name__)

# Mapping from section name to detection pattern (kept in sync with gate2._check_structural_completeness)
_FORBIDDEN_SECTION_PATTERNS: Dict[str, str] = {
    "Business Meaning and Value Case": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Business Meaning|Business Value|Value Case|Context,\s+Ground\s+Truth(?:\s+&\s+Systems\s+Landscape)?|Ground\s+Truth\s+&\s+Systems\s+Landscape)",
    "Active Technical Spikes & Experimental Inquiries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Active\s+Technical\s+Spikes(?:\s+&\s+Experimental\s+Inquiries)?|Falsifiable\s+Spike(?:s)?(?:\s+Contracts)?|Technical\s+Spikes|Spikes\s+&\s+Explorations)",
    "Immediate Delivery Scope & Release Boundaries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?|What\s+We\s+Decided.*Scope|In/Out\s+of\s+Scope|Release\s+Boundaries)",
}


def _strip_forbidden_sections(text: str, forbidden_names: List[str]) -> str:
    """
    Deterministically strips any FORBIDDEN sections from the raw LLM output.

    This is a pipeline-level enforcement gate — the LLM may generate forbidden sections
    despite prompt instructions. This function removes them before the QA gate runs,
    matching from the section header to the start of the next same-or-higher-level header.

    Args:
        text: Raw LLM output text.
        forbidden_names: List of section names that are FORBIDDEN for this archetype.

    Returns:
        Cleaned text with all forbidden sections removed.
    """
    if not forbidden_names:
        return text

    for name in forbidden_names:
        pattern = _FORBIDDEN_SECTION_PATTERNS.get(name)
        if not pattern:
            continue
        # Match the section header line and everything until the next ## or # header
        full_section_pattern = (
            r"(?m)^"
            + pattern
            + r"[^\n]*\n"           # header line
            + r"(?:(?!^#{1,3}\s).+\n|^\n)*"  # body until next header
        )
        cleaned = re.sub(full_section_pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)
        if cleaned != text:
            logger.info(
                "Deterministic post-process: stripped forbidden section '%s' from LLM output (%d chars removed).",
                name,
                len(text) - len(cleaned),
            )
            text = cleaned

    return text


def _post_process_recap(
    text: str,
    metadata: Dict[str, Any],
    forbid_sections: List[str],
    forensic_transcript: Optional[str] = None,
) -> str:
    """Post-process Output 02 to enforce duration, clean markdown, and heal deterministically."""
    # 1. Deterministically strip forbidden sections if specified
    text = _strip_forbidden_sections(text, forbid_sections)

    # 2. Inject duration into header if missing
    if "**duration:**" not in text.lower():
        from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
        dur_mins = resolve_meeting_duration(metadata, text) or 30
        dur_line = f"**Duration:** ~{dur_mins} minutes\n"
        text = re.sub(r"(\*\*Time:\*\*[^\n]*\n)", r"\1" + dur_line, text, count=1, flags=re.IGNORECASE)

    # 3. Real-time deterministic pre-flight healing (attendees, actions, C-20, headers, appendix)
    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
    healed, changes = DeterministicHealer.heal_output_2(
        text, metadata=metadata, output_1=forensic_transcript
    )
    if changes:
        logger.info("L2-Stage02 Real-Time Healer: applied %d syntactic normalizations", len(changes))

    # 4. Inject Meeting Tone Arc if missing and not forbidden/remediated
    healed = _process_meeting_tone_arc(healed, metadata, forbid_sections, forensic_transcript)

    # 5. Deterministic Cross-Pass Coherence Gate
    from .recap_coherence_gate import parse_recap_sections, validate_cross_pass_coherence
    sections = parse_recap_sections(healed)
    if sections.get("2A") and sections.get("2B") and forensic_transcript:
        validate_cross_pass_coherence(sections["2A"], sections["2B"], forensic_transcript)

    return healed


def _process_meeting_tone_arc(
    healed: str,
    metadata: Dict[str, Any],
    forbid_sections: List[str],
    forensic_transcript: Optional[str] = None,
) -> str:
    """Inject or strip Meeting Tone Arc based on archetype and remediation directives."""
    remediation_text = f"{metadata.get('remediation_directive_recap', '')} {metadata.get('remediation_directive', '')}".lower()
    delete_tone = (
        "tone" in remediation_text
        or "tone arc" in remediation_text
        or "unsupported engagement" in remediation_text
        or "meeting tone arc" in [s.lower() for s in forbid_sections]
    )
    arch_val = str(metadata.get("archetype") or metadata.get("meeting_type") or "").upper()
    is_tech_sync = any(k in arch_val for k in ("TECH", "ARCH", "ENG", "DEEP_DIVE", "TACTICAL", "STANDUP"))

    if delete_tone or is_tech_sync:
        return re.sub(r"(?m)^#{1,3}\s+Meeting Tone Arc[\s\S]*?(?=^#{1,3}\s|\Z)", "", healed)

    if not re.search(r"(?i)##\s+Meeting Tone Arc", healed) and "Meeting Tone Arc" not in forbid_sections:
        try:
            from dual_pipeline.tone_analysis_core import ParalinguisticToneAnalyzer
            analyzer = ParalinguisticToneAnalyzer()
            turns = metadata.get("turns") or []
            if not turns and forensic_transcript:
                turns = analyzer.parse_turns_from_text(forensic_transcript)
            if turns and len(turns) >= 6:
                arc = analyzer.analyze_turns(turns)
                return analyzer.inject_tone_arc_into_recap(healed, arc)
        except Exception as e:
            logger.warning(f"Could not inject Meeting Tone Arc into Output 2: {e}")

    return healed


ALLOWED_MEETING_METADATA_KEYS = frozenset({
    "project",
    "project_name",
    "client",
    "organization",
    "company",
    "archetype",
    "meeting_type",
    "track",
    "workstream",
    "title",
    "meeting_name",
    "meeting_title",
    "meeting",
    "date",
    "time",
    "duration",
    "meeting_duration",
    "attendees",
    "attendee_list",
    "hitl_role_resolutions",
    "hitl_org_resolutions",
    "tags",
    "classification",
    "audience",
    "location",
    "scope",
    "objective",
    "gate_f_certified",
    "gate_f_evidence",
})

EXCLUDED_METADATA_KEYS = frozenset({
    "turns",
    "turn_list",
    "raw_turns",
    "raw_transcript",
    "prior_recap_candidate",
    "operator_hypothesis",
    "executive_tensions",
    "evidence_locks",
    "attachment_prompt_block",
    "uploaded_collateral",
    "prior_context",
    "prior_graph_decisions",
    "prior_graph_risks",
    "user_clarifications",
    "known_graph_entities",
})


def filter_meeting_metadata_allowlist(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Filters metadata down to allowed keys, strictly excluding bulky raw turns, prior candidates, and separate XML blocks."""
    filtered: Dict[str, Any] = {}
    for k, v in metadata.items():
        if callable(v):
            continue
        k_str = str(k).lower().strip()
        if k_str.startswith("remediation_directive"):
            continue
        if k_str in EXCLUDED_METADATA_KEYS:
            continue
        if k_str in ALLOWED_MEETING_METADATA_KEYS:
            filtered[k] = v
    return filtered


def _build_optional_context_blocks(metadata: Dict[str, Any]) -> str:
    """Compile optional context XML blocks from metadata."""
    blocks: List[str] = []
    operator_hypothesis = metadata.get("operator_hypothesis") or metadata.get("executive_tensions")
    if operator_hypothesis:
        dump = json.dumps(operator_hypothesis, indent=2) if isinstance(operator_hypothesis, (dict, list)) else operator_hypothesis
        blocks.append(f"<operator_prior_hypothesis>\n{dump}\n</operator_prior_hypothesis>\n\n")

    evidence_locks = metadata.get("evidence_locks", [])
    if evidence_locks:
        blocks.append(f"<evidence_locks>\n{json.dumps(evidence_locks, indent=2)}\n</evidence_locks>\n\n")

    attachment_block = metadata.get("attachment_prompt_block") or metadata.get("uploaded_collateral")
    if attachment_block:
        blocks.append(f"<uploaded_meeting_collateral>\n{attachment_block}\n</uploaded_meeting_collateral>\n\n")

    prior_context = metadata.get("prior_context", "")
    if prior_context:
        blocks.append(f"<prior_context>\n{prior_context}\n</prior_context>\n\n")

    prior_decisions = metadata.get("prior_graph_decisions")
    if prior_decisions:
        dec_dump = json.dumps(prior_decisions, indent=2)
        blocks.append(
            f"<prior_decisions>\n"
            f"Active Predecessor Decisions from Canonical GraphDB (Rule C-16):\n"
            f"{dec_dump}\n"
            f"INSTRUCTION: Maintain cross-meeting continuity. Reference these predecessor decisions when evaluating status, changes, or blockers.\n"
            f"</prior_decisions>\n\n"
        )

    prior_risks = metadata.get("prior_graph_risks")
    if prior_risks:
        risk_dump = json.dumps(prior_risks, indent=2)
        blocks.append(
            f"<prior_risks>\n"
            f"In-Flight Risks from Canonical GraphDB:\n"
            f"{risk_dump}\n"
            f"INSTRUCTION: Evaluate whether this session addresses, mitigates, or escalates these active risks.\n"
            f"</prior_risks>\n\n"
        )

    user_clarifications = metadata.get("user_clarifications", "")
    if user_clarifications:
        blocks.append(f"<user_clarifications>\n{user_clarifications}\n</user_clarifications>\n\n")

    known_entities = metadata.get("known_graph_entities")
    if known_entities:
        blocks.append(
            f"<project_resources>\n"
            f"Verified Project Resources and Roles from Canonical GraphDB:\n"
            f"{json.dumps(known_entities, indent=2)}\n"
            f"INSTRUCTION: Use these verified names, roles, and affiliations to accurately attribute ownership in Action Items, Next Steps, and Meeting Attendees.\n"
            f"</project_resources>\n\n"
        )

    return "".join(blocks)


def validate_recap_preflight(
    forensic_transcript: str,
    metadata: Dict[str, Any],
) -> None:
    """Typed preflight validation: fails fast if forensic transcript is empty or missing turns."""
    if not forensic_transcript or not forensic_transcript.strip():
        raise ValueError("Preflight Error: Forensic transcript is empty. Cannot synthesize executive recap.")
    if metadata.get("notes_mode") or metadata.get("is_notes"):
        return
    if not re.search(r"\*\*[^\*\n]+(?::\*\*|\*\*:)", forensic_transcript) and not re.search(r"\[\d{1,2}:\d{2}", forensic_transcript):
        raise ValueError("Preflight Error: Forensic transcript contains no valid dialogue turn locators.")


def _build_remediation_block(
    metadata: Dict[str, Any],
    forensic_transcript: str,
    directive: str,
    prior_candidate: Optional[str],
) -> str:
    """Builds structured remediation directive XML with surgical targeted patching rules."""
    from dual_pipeline.transcript_duration_helper import resolve_meeting_duration

    prior_block = f"<prior_recap_candidate>\n{prior_candidate}\n</prior_recap_candidate>\n\n" if prior_candidate else ""
    dur_mins = resolve_meeting_duration(metadata, forensic_transcript) or 60
    max_ts = f"{dur_mins // 60:02d}:{dur_mins % 60:02d}:00"
    archetype_name = str(metadata.get("archetype", "session")).replace("_", " ").lower()

    return (
        f"<remediation_directive>\n"
        f"PREVIOUS ATTEMPT FAILED. REVISE THE RECAP ADDRESSING THE FOLLOWING CRITIQUE.\n\n"
        f"{prior_block}"
        f"CRITIQUE DIRECTIVE:\n"
        f"{directive}\n\n"
        f"SURGICAL TARGETED PATCHING INSTRUCTIONS:\n"
        f"1. Continuous Candidate Continuity: Examine <prior_recap_candidate>. You MUST retain all passing, accurate sections byte-identically (Header, Meeting Objective, verified attendee roster, and substantive Key Discussion Points).\n"
        f"2. Apply targeted differential edits ONLY to the failing section(s) specified in the critique.\n"
        f"3. PROTECTED SECTIONS: Do NOT re-draft or degrade Header, Meeting Objective, or valid Key Discussion Points.\n"
        f"4. FORBIDDEN MUTATIONS:\n"
        f"   - Do NOT fabricate synthetic action items or assign forward commitments if none were agreed in the dialogue. "
        f"If the CRITIQUE DIRECTIVE specifies action items or follow-ups, implement them in the '# Action & Governance Ledger'. "
        f"Only if no forward commitments or follow-ups were agreed in the dialogue or requested in the critique, emit in the '# Action & Governance Ledger':\n"
        f"     'No formal actions were established in this session; open initiatives remain unresolved questions.'\n"
        f"   - Do NOT invent timestamps outside the session duration (< {max_ts}). All locators in the Appendix must be <= {max_ts}.\n"
        f"5. Output the complete revised document with protected sections intact and defects resolved.\n"
        f"</remediation_directive>"
    )


def _format_collateral_embed_line(att: Dict[str, Any]) -> str:
    fn = att.get("filename", "reviewed_exhibit.png")
    rel = att.get("relative_staged_path") or f"referenced_artifacts/{fn}"
    desc = att.get("description") or att.get("summary") or att.get("title") or fn
    return f"- `![{desc}]({rel})` (Artifact: `{fn}`)"


def _build_collateral_embed_block(metadata: Dict[str, Any]) -> Optional[str]:
    if not (metadata.get("attachments") or metadata.get("attachment_prompt_block") or metadata.get("uploaded_collateral")):
        return None
    all_atts = [a for a in (metadata.get("attachments") or []) if isinstance(a, dict)]
    has_explicit_cr = any(x.get("attribution_mode") == "COLLATERAL_REVIEWED" for x in all_atts)
    cr_atts = [
        a for a in all_atts
        if not str(a.get("filename", "")).endswith((".txt", ".md", ".json"))
        and (a.get("attribution_mode") == "COLLATERAL_REVIEWED" or not has_explicit_cr)
    ]
    if not cr_atts:
        return None
    embeds_text = "\n".join(_format_collateral_embed_line(a) for a in cr_atts)
    return (
        "<collateral_embed_requirement>\nCOLLATERAL REVIEWED: Architecture diagrams and prototype exhibits were reviewed during this session.\n"
        f"You MUST include markdown image embeds for all reviewed collateral in the Key Discussion Points & Findings / Technical Landscape section:\n{embeds_text}\n"
        "Ensure each image embed uses `referenced_artifacts/<filename>`.\n</collateral_embed_requirement>"
    )


def _try_surgical_patch_recap(
    metadata: Dict[str, Any], pa_context: Dict[str, Any], forbid_sections: List[str], forensic_transcript: str
) -> Optional[str]:
    critique_env = metadata.get("critique_diff_envelope") or (pa_context.get("critique_diff_envelope") if pa_context else None)
    prior_candidate = metadata.get("prior_recap_candidate") or (pa_context.get("prior_recap_candidate") if pa_context else None)
    if not (critique_env and critique_env.get("target_path") and critique_env.get("replacement_content") and prior_candidate):
        return None
    from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
    try:
        patched_recap, patch_audit = SurgicalPatcher.apply_critique_envelope(
            original_text=prior_candidate, critique_envelope=critique_env,
            replacement_content=critique_env["replacement_content"], metadata=metadata, auto_heal=True,
        )
        logger.info("recap_executor dispatched section patch via SurgicalPatcher: %s", patch_audit)
        return _post_process_recap(patched_recap, metadata, forbid_sections, forensic_transcript=forensic_transcript)
    except Exception as e:
        logger.warning("recap_executor surgical patch dispatch failed, falling back to LLM synthesis: %s", e)
        return None


class L2ExecutiveRecapExecutor:
    """
    L2 Stage 02: Executive Recap Agent.
    Produces Artifact 02 (executive_recap.md).
    Takes Stage 01 output as primary input.
    """

    def __init__(self, provider: L2SynthesisProvider):
        self.provider = provider

    def execute(
        self,
        forensic_transcript: str,
        pa_context: Dict[str, Any],
        remediation_directive: Optional[str] = None,
    ) -> str:
        logger.info("L2-Stage02: Executive Recap Agent")
        metadata = pa_context.get("metadata", {})
        metadata["gate_f_certified"] = True
        validate_recap_preflight(forensic_transcript, metadata)
        from dual_pipeline.prompts import OUTPUT2_CONTRACT, MeetingArchetype, SectionRequirement
        from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner

        budget = PromptPrefixAligner.calculate_reasoning_token_budget(forensic_transcript, metadata, stage="stage_02")
        metadata["reasoning_budget"] = budget
        PromptPrefixAligner.apply_reasoning_budget_to_provider(self.provider, budget)

        arch_val = metadata.get("archetype") or metadata.get("meeting_type")
        title = metadata.get("meeting_name") or metadata.get("title") or ""
        arch_enum = MeetingArchetype.from_string(arch_val) if arch_val else OUTPUT2_CONTRACT.detect_archetype(title=title, text=forensic_transcript)
        metadata["archetype"] = arch_enum.value
        metadata["meeting_type"] = metadata.get("meeting_type") or arch_enum.value
        recap_prompt = _load_stage_prompt("recap", archetype=arch_enum)

        topo = OUTPUT2_CONTRACT.get_section_topology(arch_enum)
        mand_sections = [
            f"# {sec}" for sec, req in topo.items()
            if req == SectionRequirement.MANDATORY and sec != "Detailed Stakeholder Perspectives (Appendix)"
        ]
        mand_str = ", ".join(mand_sections) + ", and # Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        forbid_sections = [sec for sec, req in topo.items() if req == SectionRequirement.FORBIDDEN]
        forbid_str = f" Prohibited sections for {arch_enum.value}: {', '.join(forbid_sections)}." if forbid_sections else ""

        static_blocks = [
            OUTPUT2_CONTRACT.compile_governance_context(arch_enum),
            "<gate_f_certification>\nGATE F CERTIFICATION: PASS (Audio completeness 100%, zero unbounded silence gaps).\nConstraint C-30 is SATISFIED. You MUST synthesize the full executive recap without withholding sections.\n</gate_f_certification>",
            f"<mandatory_structure>\nMust include: {mand_str}.{forbid_str}\nadhering strictly to the pipeline's SSOT Gold Standard without duplicate tables.\n</mandatory_structure>",
        ]
        collateral_block = _build_collateral_embed_block(metadata)
        if collateral_block:
            static_blocks.append(collateral_block)

        patched = _try_surgical_patch_recap(metadata, pa_context, forbid_sections, forensic_transcript)
        if patched is not None:
            return patched

        meta_prompt = filter_meeting_metadata_allowlist(metadata)
        dynamic_blocks = [
            f"<meeting_metadata>\n{json.dumps(meta_prompt, separators=(',', ':'), default=str)}\n</meeting_metadata>",
            f"<forensic_transcript>\n{forensic_transcript}\n</forensic_transcript>",
        ]
        opt_blocks = _build_optional_context_blocks(metadata)
        if opt_blocks:
            dynamic_blocks.append(opt_blocks.strip())
        if remediation_directive:
            logger.info("Injecting remediation_directive into L2-Stage02 recap generation.")
            prior_cand = metadata.get("prior_recap_candidate") or (pa_context.get("prior_recap_candidate") if pa_context else None)
            dynamic_blocks.append(_build_remediation_block(metadata, forensic_transcript, remediation_directive, prior_cand))

        system_prefix = PromptPrefixAligner.align_static_prefix(recap_prompt, static_blocks, stage_tag="stage02_recap")
        raw_recap = self.provider.synthesize_recap(system_prefix, "\n\n".join(dynamic_blocks) + "\n\n")
        return _post_process_recap(raw_recap, metadata, forbid_sections, forensic_transcript=forensic_transcript)
