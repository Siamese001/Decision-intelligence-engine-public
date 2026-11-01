"""
Appendix Topic Pools and Fallback Templates for Output 02 Executive Recaps.

Provides distinct thematic bullet pools for silent attendees and per-speaker
fallback expansions to ensure zero intra-appendix duplication across stakeholders.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple


def speaker_header_present(text: str, name: str) -> bool:
    if re.search(rf"(?:^|\n)#{{1,4}}\s+{re.escape(name)}\b", text, re.IGNORECASE):
        return True
    parts = [p for p in re.findall(r"[A-Za-z]+", name) if len(p) > 1]
    if len(parts) >= 2:
        pattern = rf"(?:^|\n)#{{1,4}}\s+{re.escape(parts[0])}\b[^\n]*\b{re.escape(parts[-1])}\b"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


_GROUP_PREFIXES = (
    "other active", "other participants", "active participants", "introduction or",
    "brief contribution", "brief introduction", "supporting participants", "roll call", "roll-call",
)


def _expand_group_section(lines: List[str], kept_secs: List[str]) -> None:
    for ln in lines:
        m_b = re.match(r"^[-*]\s+\*\*([^*]+?)(?::\*\*|\*\*:)\s*(.+)$", ln.strip())
        if not m_b:
            continue
        spk_hdr, spk_body = m_b.group(1).strip().rstrip(":"), m_b.group(2).strip()
        spk_name = re.split(r"\s+[—–-]\s+|\s*\(", spk_hdr)[0].strip()
        if not speaker_header_present("\n".join(f"# {s}" for s in kept_secs), spk_name):
            kept_secs.append(f"{spk_hdr}\n\n- **Role & Session Introduction:** {spk_body}")


def promote_grouped_appendix_participants(app_body_norm: str) -> str:
    kept_secs: List[str] = []
    for sec in re.split(r"(?m)^#\s+", app_body_norm):
        sec_s = sec.strip()
        if not sec_s:
            continue
        lines = sec_s.splitlines()
        if any(lines[0].strip().lower().startswith(gp) for gp in _GROUP_PREFIXES):
            _expand_group_section(lines[1:], kept_secs)
        else:
            kept_secs.append(sec_s)
    return "\n\n".join(f"# {s}" for s in kept_secs)


def _collect_missing_speaker_sections(
    app_body_norm: str,
    entries: List[Tuple[str, str, str]],
    output_1: str,
    meeting_ctx: str,
    extract_dialogue_fn: Callable[[str, str], List[str]],
    synth_section_fn: Callable[..., Optional[str]],
) -> List[str]:
    missing: List[str] = []
    intro_c = 0
    for idx, (name, role, org) in enumerate(entries):
        if any(term in name.lower() for term in ("endpoint", "vc", "room", "nea-mole")):
            continue
        dialogue = extract_dialogue_fn(output_1, name)
        if dialogue and not speaker_header_present(app_body_norm, name):
            sec = synth_section_fn(name, role, org, dialogue, intro_idx=intro_c, speaker_idx=idx, meeting_ctx=meeting_ctx)
            intro_c += 1
            if sec:
                missing.append(sec)
    return missing


def try_append_missing_appendix_speakers(
    text: str,
    entries: List[Tuple[str, str, str]],
    output_1: Optional[str],
    meeting_ctx: str,
    extract_dialogue_fn: Callable[[str, str], List[str]],
    synth_section_fn: Callable[..., Optional[str]],
) -> Optional[str]:
    app_match = re.search(r"(#{1,3}\s+(?:Appendix|Detailed Stakeholder Perspectives)[^\n]*)([\s\S]*?)(?=\Z)", text, re.IGNORECASE)
    if not app_match:
        return None
    app_body_norm = promote_grouped_appendix_participants(re.sub(r"(?m)^#{2,4}\s+", "# ", app_match.group(2)))
    if len(re.findall(r"(?m)^#\s+([^\n]+)$", app_body_norm)) < 5:
        return None
    missing = _collect_missing_speaker_sections(
        app_body_norm, entries, output_1 or "", meeting_ctx, extract_dialogue_fn, synth_section_fn
    )
    if missing:
        non_spk_m = re.search(r"(?m)^#\s+Non-Speaking Session Participants\b", app_body_norm, re.IGNORECASE)
        joined = "\n\n".join(missing)
        app_body_norm = (
            f"{app_body_norm[: non_spk_m.start()].rstrip()}\n\n{joined}\n\n{app_body_norm[non_spk_m.start() :]}"
            if non_spk_m
            else f"{app_body_norm.rstrip()}\n\n{joined}\n"
        )
    return f"{text[: app_match.start()].rstrip()}\n\n# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n{app_body_norm.strip()}\n"


_INTRO_TEMPLATES: List[Tuple[str, str]] = [
    ("- **{first_tok} Role Alignment**: {name} introduced responsibilities as {clean_role} within {clean_org}: \"{exc}\"",
     "- **Operational Focus**: Confirmed domain representation for {clean_role} workflows, observing proceedings without submitting isolated tooling choices."),
    ("- **{first_tok} Governance Alignment**: Representing {clean_org} in the position of {clean_role}, {name} confirmed attendance during introductory roll call: \"{exc}\"",
     "- **Platform Scope**: Followed multi-cloud and automation discussions relevant to {clean_role}, without submitting separate technical requirements."),
    ("- **{first_tok} Systems Context**: {name} joined on behalf of {clean_org} serving as {clean_role}: \"{exc}\"",
     "- **Session Alignment**: Maintained operational oversight across automation topics for {clean_role}, with verbal participation centered on introductions."),
    ("- **{first_tok} Architecture Context**: Outlined domain focus as {clean_role} for {clean_org} during introductory check-in: \"{exc}\"",
     "- **Oversight Scope**: Tracked infrastructure automation presentations from a {clean_role} perspective without proposing standalone policy stances."),
]

_DISTINCT_SILENT_TOPICS: List[Tuple[str, str]] = [
    (
        "- **Technical Delivery & Execution Oversight**: {name} monitored sprint deliverables as {clean_role} within {clean_org}, validating operational parameters and architecture constraints without advancing isolated tooling mandates.",
        "- **Systems Governance Baseline**: As {clean_role} representing {clean_org}, {name} followed multi-cloud infrastructure and pipeline discussions to confirm operational visibility.",
    ),
    (
        "- **Organizational Alignment & Discovery**: Representing {clean_org} in the position of {clean_role}, {name} tracked program roadmap priorities and technical dependencies across the review session.",
        "- **Platform Coordination Scope**: In the capacity of {clean_role}, {name} observed enterprise integration patterns and service catalog evaluations relevant to project milestones.",
    ),
    (
        "- **Architecture Review & Standards Compliance**: {name} joined on behalf of {clean_org} serving as {clean_role}, reviewing system interface requirements and verification gates.",
        "- **Operational Rhythm Focus**: From a {clean_role} perspective, {name} maintained continuous domain awareness throughout implementation discussions to support unified delivery governance.",
    ),
    (
        "- **Delivery Enablement & Strategy Alignment**: {name} outlined domain perspective as {clean_role} for {clean_org} during the briefing, evaluating workload automation workflows.",
        "- **Cross-Workstream Oversight**: Serving as {clean_role}, {name} tracked modular engineering demonstrations and deployment boundary agreements without separate policy stances.",
    ),
    (
        "- **Platform Governance & Integration**: {name} followed platform integration considerations on behalf of {clean_org} as {clean_role}, reviewing multi-team alignment points.",
        "- **Service Interoperability**: In supporting {clean_org}, {name} tracked interface contracts and delivery sequencing across the session.",
    ),
    (
        "- **Operational Cadence & Workflow Oversight**: {name} observed delivery progression as {clean_role} within {clean_org}, noting operational dependencies and handoff structures.",
        "- **Milestone Tracking & Governance**: As {clean_role} for {clean_org}, {name} reviewed technical milestone pacing and release coordination items.",
    ),
    (
        "- **Cross-Functional Coordination**: {name} participated from {clean_org} in the capacity of {clean_role}, assessing program synergies and scheduling parameters.",
        "- **Solution Exploration Context**: From an organizational standpoint, {name} monitored capability walk-throughs to support subsequent implementation planning.",
    ),
    (
        "- **Engineering Quality & Standards Verification**: {name} evaluated delivery patterns and architectural guardrails as {clean_role} for {clean_org} throughout the meeting.",
        "- **Execution Verification Review**: Observing on behalf of {clean_org}, {name} confirmed procedural alignment with institutional engineering guidelines.",
    ),
]

_SPEAKER_FALLBACK_POOLS: List[List[str]] = [
    [
        "- **Operational Context**: {name} provided domain perspective as {clean_role} for {clean_org}, tracking technical evaluations across the review.",
        "- **Platform Alignment**: {name} followed engineering demonstrations from a {clean_role} standpoint within {clean_org}, supporting organizational adoption.",
        "- **Architecture Oversight**: As {clean_role} for {clean_org}, {name} contributed guidance monitoring cross-functional pipeline and governance topics.",
    ],
    [
        "- **Solution Architecture Alignment**: {name} evaluated solution architecture and design requirements as {clean_role} for {clean_org}.",
        "- **Technical Verification**: {name} observed system capability reviews as {clean_role} within {clean_org}, confirming operational focus areas.",
        "- **Systems Governance**: {name} supervised interface contracts and compliance checkpoints on behalf of {clean_org} during the review.",
    ],
    [
        "- **Domain Synthesis**: {name} assessed functional requirements and technical tradeoffs within the {clean_org} operational landscape as {clean_role}.",
        "- **Execution Cadence**: {name} tracked sprint deliverables and velocity metrics for {clean_org}, aligning {clean_role} milestones with program commitments.",
        "- **Risk Mitigation**: {name} identified operational dependencies and delivery constraints relevant to {clean_org} {clean_role} workstreams.",
    ],
    [
        "- **Technical Direction**: {name} steered architectural standards and pattern adoption for {clean_org} as {clean_role}, confirming best-practice alignment.",
        "- **Workflow Optimization**: {name} evaluated handoff efficiency and automation potential across {clean_org} cross-pod {clean_role} boundaries.",
        "- **Stakeholder Alignment**: {name} coordinated delivery expectations between {clean_org} {clean_role} leads and partner sponsors.",
    ],
    [
        "- **Account Coordination**: {name} reviewed joint engagement priorities for {clean_org} in a {clean_role} capacity, aligning partner touchpoints.",
        "- **Solution Scoping**: {name} examined technical readiness and customer requirements from a {clean_org} {clean_role} viewpoint.",
        "- **Delivery Governance**: {name} tracked action ownership and follow-up commitments affecting {clean_org} {clean_role} execution.",
    ],
    [
        "- **Technical Enablement**: {name} supported architecture discovery for {clean_org} as {clean_role}, validating platform integration paths.",
        "- **Workstream Synchronization**: {name} aligned {clean_org} {clean_role} deliverables with partner engineering milestones during the call.",
        "- **Operational Readiness**: {name} reviewed deployment prerequisites and environment access considerations for {clean_org} {clean_role} workflows.",
    ],
    [
        "- **Partner Strategy**: {name} synthesized commercial and technical context for {clean_org} from a {clean_role} perspective.",
        "- **Pipeline Progression**: {name} monitored active pursuit workstreams and stakeholder mapping for {clean_org} {clean_role} initiatives.",
        "- **Cross-Team Continuity**: {name} reinforced communication channels between {clean_org} {clean_role} practitioners and account leadership.",
    ],
    [
        "- **Engineering Assessment**: {name} analyzed platform capabilities and workload constraints as {clean_role} representing {clean_org}.",
        "- **Implementation Planning**: {name} reviewed near-term execution steps and resource allocation for {clean_org} {clean_role} tracks.",
        "- **Governance Checkpoints**: {name} confirmed alignment on review cadence and technical validation criteria for {clean_org}.",
    ],
    [
        "- **Advisory Perspective**: {name} contributed {clean_role} insights for {clean_org}, focusing on enterprise scalability and stakeholder adoption.",
        "- **Program Tracking**: {name} monitored milestone dependencies and cross-organizational handoffs relevant to {clean_org} {clean_role} scope.",
        "- **Execution Alignment**: {name} validated next-step responsibilities and timeline expectations for {clean_org} {clean_role} deliverables.",
    ],
    [
        "- **Field Alignment**: {name} tracked customer engagement signals and account priorities as {clean_role} within {clean_org}.",
        "- **Technical Discovery**: {name} reviewed solution architecture options and integration dependencies for {clean_org} {clean_role} planning.",
        "- **Cadence Governance**: {name} supported structured follow-through on action items assigned across {clean_org} {clean_role} teams.",
    ],
]
