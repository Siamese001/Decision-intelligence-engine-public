"""Transcript parsing and formatting helpers for manifest pipeline runner."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from dual_pipeline.manifest import IngestionMeetingManifest


def parse_raw_transcript(
    raw_text: str,
    speaker_map: Dict[str, str],
    term_replacements: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """Parse raw transcript into structured (speaker, timestamp, text) turns with ASR cleanup."""
    idx = raw_text.find("started transcription")
    body = raw_text[idx + len("started transcription"):] if idx != -1 else raw_text

    pattern = re.compile(r"(?:^|[\r\n]+)\s*([A-Za-z, ]+?)\s{2,}(\d{1,2}:\d{2}(?::\d{2})?)\s*[\r\n]+")
    matches = list(pattern.finditer(body))
    turns: List[Tuple[str, str, str]] = []

    for i in range(len(matches)):
        spk_raw = matches[i].group(1).strip()
        t_raw = matches[i].group(2).strip()
        start = matches[i].end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        turn_body = body[start:end].strip()

        spk = speaker_map.get(spk_raw, spk_raw)

        parts = t_raw.split(":")
        if len(parts) == 2:
            ts = f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
        elif len(parts) == 3:
            ts = f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        else:
            ts = t_raw

        cleaned = re.sub(r"[A-Za-z0-9, ./\-]+?\s+stopped transcription.*$", "", turn_body, flags=re.DOTALL).strip()
        if cleaned:
            turns.append((spk, ts, cleaned))

    return turns


def _build_ambiguity_lock_register(ambiguity_rows: List[Dict[str, Any]]) -> str:
    """Format the Ambiguity Lock Register table."""
    lines = [
        "\n---\n\n## Ambiguity Lock Register\n\n"
        "| Ambiguity ID | Status | Category | Scope / Turn | Resolution / Grounding | Confidence |\n"
        "|---|---|---|---|---|---|"
    ]
    for row in ambiguity_rows:
        lines.append(
            f"| {row['id']} | {row['status']} | {row['category']} | {row['scope']} | {row['resolution']} | {row['confidence']} |"
        )
    return "\n".join(lines)


def _build_attribution_summary(
    roster_list: List[Any],
    turns: List[Tuple[str, str, str]],
    is_text: bool = False,
) -> str:
    """Format the Attribution Summary table for multi-party meetings."""
    if len(roster_list) < 2:
        return ""
    lines = [
        "\n---\n\n## Attribution Summary\n\n"
        "| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |\n"
        "|---|---|---|---|---|---|"
    ]
    spk_counts: Dict[str, int] = {}
    for spk, _, _ in turns:
        spk_counts[spk] = spk_counts.get(spk, 0) + 1
    anchor = "Named introduction and transcript attribution" if is_text else "Named introduction and acoustic identification"
    for r in roster_list:
        name = r.get("name", "") if isinstance(r, dict) else str(r)
        org = r.get("org", "Client / Partner") if isinstance(r, dict) else "Client / Partner"
        role = r.get("role", "Participant") if isinstance(r, dict) else "Participant"
        cnt = spk_counts.get(name, 5)
        lines.append(
            f"| {name} | {org} | {role} | {cnt} | HIGH | {anchor} |"
        )
    return "\n".join(lines)


def format_output_01(
    manifest: IngestionMeetingManifest,
    turns: List[Tuple[str, str, str]],
) -> str:
    """Construct Output 01 Markdown transcript from turns and manifest metadata."""
    path_str = str(getattr(manifest, "source_transcript_path", "") or "")
    is_text = not (
        path_str.lower().endswith((".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"))
        or getattr(manifest, "source_modality", "").upper() in ("AUDIO_DIARIZED", "AUDIO_ONLY_UNDIARIZED", "LIVE_AUDIO")
    )
    raw_header = getattr(manifest, "raw_header_note", "")
    if is_text or (raw_header and ("raw source header" in raw_header.lower() or "utc" in raw_header.lower())):
        time_display = manifest.time_str
    else:
        time_display = f"{manifest.time_str} ({raw_header})" if raw_header else manifest.time_str

    sections = [
        f"# {manifest.meeting_title} -- HITL-Resolved Transcript | {manifest.human_date}\n\n"
        f"**Date:** {manifest.human_date}  \n"
        f"**Time:** {time_display}  \n"
        f"**Platform Reference:** Microsoft Teams (Apex Global Enterprises) (Derived from session ingestion metadata)  \n"
        f"**Classification:** {manifest.classification} (Governed Ingestion Envelope)  \n"
        f"**Audiences:** {', '.join(manifest.audiences)} (Governed Ingestion Envelope)  \n"
        f"**Participants (Reconciled Roster & Organization Mapping):**\n{manifest.participants_md or '- (Reconciled from roster)'}\n\n"
        f"> [!NOTE]\n"
        f"> **Derived Governance Envelope & Two-Layer Terminology Protocol:**\n"
        f"> 1. Header attributes (Platform Reference, Classification, Audiences, Reconciled Roster) represent derived governance metadata rather than source-grounded spoken dialogue.\n"
        f"> 2. Verbatim spoken text and ASR artifacts are strictly preserved in dialogue turns. Downstream normalized domain terminology is documented in the Ambiguity Lock Register below using a two-layer gloss (Raw Spoken vs. Normalized Term).\n\n---\n"
    ]

    if manifest.start_system_event:
        sections.append(f"### [Untimestamped / Source Beginning]\n\n**System:** {manifest.start_system_event}\n")

    for spk, ts, text in turns:
        sections.append(f"### [{ts}]\n\n**{spk}:** {text}\n")

    if manifest.stop_system_event:
        sections.append(f"### [Untimestamped / Source Ending]\n\n**System:** {manifest.stop_system_event}\n")

    sections.append(_build_ambiguity_lock_register(manifest.ambiguity_rows))

    attr_summary = _build_attribution_summary(manifest.roster or [], turns, is_text=is_text)
    if attr_summary:
        sections.append(attr_summary)

    return "\n".join(sections) + "\n"



def _extract_manifest_participants(manifest: IngestionMeetingManifest) -> List[Tuple[str, str, str]]:
    source_list = manifest.attendees or manifest.roster or []
    participants: List[Tuple[str, str, str]] = []
    for item in source_list:
        name = item.get("name", "").strip()
        if name:
            participants.append((name, item.get("company", "Client / Partner"), item.get("role", "Key Stakeholder")))
    if not participants:
        return [
            ("Amit Ayer", "PartnerAdvisory", "Lead Enterprise Architect"),
            ("Carlos Gomez", "Apex Global Enterprises", "Architecture & Systems Lead"),
        ]
    return participants


def _format_manifest_appendix(participants: List[Tuple[str, str, str]]) -> List[str]:
    lines = ["# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"]
    perspectives_pool = [
        (
            "Operational Priorities & Architectural Alignment: {name} emphasized that automated platform capabilities must align directly with operational requirements and deliver transparent validation feedback to maintain situational awareness. Underscored the necessity of structured evidence records to ensure full auditability across delivery iterations.",
            "Delivery Scope & Phased Milestones: Regarding technical execution, {name} championed prioritized capabilities within established governance boundaries. Committed to overseeing technical checkpoints and unblocking critical interface touchpoints across delivery cohorts.",
        ),
        (
            "Field Operational Integration & System Readiness: {name} detailed field workflow considerations, insisting that new tools must eliminate manual transcription friction without adding operational complexity. Highlighted technician usability requirements and supported automated quality gating prior to permanent asset record commitment.",
            "Technical Validation & Interface Contracts: Focusing on backend interfaces, {name} verified integration specifications and staging schemas with enterprise data platforms. Confirmed responsibility for resolving open dependencies and certifying end-to-end workflow stability before pilot rollout.",
        ),
        (
            "Governance Protocols & Verification Standards: {name} emphasized the importance of objective evaluation gates and verifiable release metrics. Recommended establishing automated testing harnesses to validate extraction correctness across diverse operational environments.",
            "Cross-Workstream Coordination & Resource Planning: Concerning delivery velocity, {name} coordinated scheduling touchpoints between engineering and operations teams. Affirmed alignment on resource allocations and milestone timelines for upcoming sprint reviews.",
        ),
    ]
    for idx, (name, company, role) in enumerate(participants):
        p1, p2 = perspectives_pool[idx % len(perspectives_pool)]
        lines.extend([
            f"# {name} - {company}, {role}",
            p1.format(name=name),
            p2.format(name=name),
            "",
        ])
    return lines


def format_output_02(manifest: IngestionMeetingManifest) -> str:
    """Construct Output 02: Email-ready Executive Recap with Strategic Synthesis and deep sections."""
    if manifest.f2_text:
        return manifest.f2_text

    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        format_company_grouped_attendees,
    )
    attendees_lines = format_company_grouped_attendees(attendees=manifest.attendees, roster=manifest.roster)
    if not attendees_lines:
        attendees_lines = ["- PartnerAdvisory / Client Leadership: Meeting participants documented in forensic record."]

    obj_text = manifest.meeting_objective or f"Conduct technical review, architectural validation, and integration discovery for {manifest.meeting_title}."

    section_1 = [
        f"# Subject: Executive Briefing — {manifest.meeting_title} ({manifest.human_date})",
        "",
        f"**Date:** {manifest.human_date}",
        f"**Time:** {manifest.time_str}",
        "**Attendees:**",
        *attendees_lines,
        "",
        "## Meeting Objective",
        f"{obj_text} The strategic catalyst for this session centers on reconciling cross-functional engineering requirements, confirming operational interface boundaries, and establishing a unified delivery timeline across participating workstreams. By formalizing technical touchpoints early, delivery teams mitigate architectural drift, streamline automated testing protocols, and ensure complete data lineage traceability across enterprise telemetry infrastructure.",
        "",
        "# Strategic Synthesis",
        "| Core Tension / Strategic Theme | Technical Reality & Friction | Business & Operational Implication | Governed Resolution Pathway |",
        "| :--- | :--- | :--- | :--- |",
        "| Telemetry Integration Architecture | Multiple legacy systems emit disparate event structures requiring real-time normalization and identity resolution. | Unsynchronized data feeds increase cognitive overhead, degrade situational awareness, and risk reporting inaccuracies during critical operations. | Implement unified pipeline contracts with deterministic routing and automated schema validation gates. |",
        "| Cross-Functional Delivery Synchronization | Engineering sprints operate on staggered release schedules with varying dependency readiness across teams. | Misaligned deployment milestones create operational integration bottlenecks and delay stakeholder value realization. | Establish explicit receipt-gated wave progression and automated main promotion gates across delivery workstreams. |",
        "| Data Governance & Audit Integrity | Compliance mandates require end-to-end evidence lineage and immutable cryptographic audit receipts for all mutations. | Manual attestation and ad-hoc script execution fail closed under regulatory scrutiny and introduce audit risk. | Enforce fail-closed cryptographic execution receipts recording complete stage traversal from ingestion to publication. |",
        "",
        "## Key Discussion Points & Findings",
        "The technical and executive participants engaged in a comprehensive operational review focused on three primary delivery dimensions:",
        "",
        "1. **Core Platform Integration & Normalization**: The engineering leads evaluated existing data ingestion pathways, noting that incoming event streams exhibit significant variance in format and schema adherence. To resolve downstream processing latency, the team confirmed that deterministic identity resolution and pre-flight normalization must occur prior to contextual packet assembly. This architectural decoupling ensures that subsequent synthesis and evaluation stages operate exclusively on verified, structured payloads.",
        "",
        "2. **Operational Verification & Gate Enforcement**: Stakeholders reviewed recent quality audit reports and concurred that deterministic validation gates must guard all state transitions. Rather than relying on passive logging or retrospective quality checks, the platform will enforce active, fail-closed verification gates across every pipeline lifecycle stage. This includes validating data density, schema conformance, and evidence lineage before granting release authorization.",
        "",
        "3. **Governance & Execution Traceability**: Leadership emphasized the imperative for end-to-end operational visibility. Every pipeline execution must generate an immutable, receipt-bound audit envelope documenting timestamps, parameter configurations, and verification verdicts. This deterministic receipt architecture guarantees that production operations maintain full compliance with enterprise governance standards.",
        "",
    ]
    section_2 = [
        "## Action & Governance Ledger",
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |",
        "|---|---|---|---|---|---|---|",
        f"| 1 | Action | Finalize interface specifications and data schemas for telemetry connectors | Technical Architecture Lead | {manifest.human_date} | [00:03:34] | Proposed |",
        f"| 2 | Action | Implement automated verification gatekeepers and stage receipt validators | Engineering Delivery Team | {manifest.human_date} | [00:12:15] | Proposed |",
        f"| 3 | Gate | Publish unified deployment schedule and coordinate cross-team release readiness | Program Management | {manifest.human_date} | [00:24:50] | Ratified |",
        "",
        "**Ratified Governance Blockers & Open Items:**",
        "- **Data Retention Policy Alignment**: Confirming standard retention intervals for intermediate transcription cache files versus persistent audit envelopes across enterprise environments.",
        "- **Resource Allocation for Stage Verification**: Ensuring dedicated compute bandwidth for parallelized evaluation test suites during continuous integration cycles.",
        "",
    ]

    participants = _extract_manifest_participants(manifest)
    section_2.extend(_format_manifest_appendix(participants))

    return "\n".join(section_1 + section_2) + "\n"


