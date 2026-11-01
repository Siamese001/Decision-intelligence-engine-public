"""Offline deterministic fixture synthesizer for transcript stages testing and CI."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import AgenticLeverageNode

logger = logging.getLogger(__name__)


def _extract_meta_and_date(user_content: str) -> Tuple[Dict[str, Any], str]:
    meta: Dict[str, Any] = {}
    m_meta = re.search(r"<meeting_metadata>\s*([\s\S]*?)\s*</meeting_metadata>", user_content)
    if m_meta:
        with contextlib.suppress(Exception):
            meta = json.loads(m_meta.group(1).strip())
    date = meta.get("date")
    if not date:
        date_match = re.search(r'"date":\s*"([^"]+)"', user_content)
        date = date_match.group(1) if date_match else "01/01/2026"
    return meta, date

def _extract_roster_item_name(item: Any) -> Optional[str]:
    val = item.get("name") if isinstance(item, dict) else item
    return str(val).strip() if val else None

def _extract_group_names(grp: Any) -> List[str]:
    if isinstance(grp, list):
        return [n for r in grp if (n := _extract_roster_item_name(r))]
    return [c] if isinstance(grp, str) and (c := re.sub(r"\s*\(.*?\)", "", grp).strip()) else []

def _names_from_roster(roster: Any) -> List[str]:
    if isinstance(roster, list):
        return [n for r in roster if (n := _extract_roster_item_name(r))]
    return [n for grp in roster.values() for n in _extract_group_names(grp)] if isinstance(roster, dict) else []

def _names_from_text(user_content: str) -> List[str]:
    from_regex = re.findall(r'"name":\s*"([^"]+)"', user_content)
    if from_regex:
        return from_regex
    if m_att := re.search(r'"attendees":\s*(\[[^\]]+\])', user_content):
        with contextlib.suppress(Exception):
            return [str(x) for x in json.loads(m_att.group(1))]
    return []


def _extract_roster_names(meta: Dict[str, Any], user_content: str) -> List[str]:
    roster = meta.get("roster") or meta.get("attendees") or meta.get("speaker_map")
    if roster:
        return _names_from_roster(roster)
    return _names_from_text(user_content)


def _build_attribution_block(
    roster_names: List[str],
    metadata: Optional[Dict[str, Any]] = None,
    dialogue_text: str = "",
) -> str:
    from .attribution_builder import AttributionSummaryBuilder
    meta = dict(metadata) if metadata else {}
    return AttributionSummaryBuilder.build_summary(meta, dialogue_text=dialogue_text, roster_names=roster_names)


class OfflineTranscriptSynthesizer:
    """Deterministic offline provider — returns valid fixtures for CI and tests."""

    def synthesize_signal_triage(self, transcript: str) -> str:
        return "PROCEED"

    @staticmethod
    def _resolve_recap_metadata(user_content_or_metadata=None, legacy_metadata: dict = None) -> dict:
        if isinstance(user_content_or_metadata, dict):
            return user_content_or_metadata
        metadata = legacy_metadata or {}
        if not metadata and isinstance(user_content_or_metadata, str):
            m = re.search(r"<meeting_metadata>\s*([\s\S]*?)\s*</meeting_metadata>", user_content_or_metadata)
            if m:
                with contextlib.suppress(Exception):
                    metadata = json.loads(m.group(1).strip())
        if not metadata.get("archetype") and isinstance(user_content_or_metadata, str):
            m_arch = re.search(r'(?:Archetype:\s*|["\']archetype["\']:\s*["\'])([\w_]+)', user_content_or_metadata, re.IGNORECASE)
            if m_arch:
                metadata = dict(metadata)
                metadata["archetype"] = m_arch.group(1).upper()
        return metadata

    @classmethod
    def _build_attendees_lines(cls, metadata: dict, user_content_or_metadata=None) -> List[str]:
        with contextlib.suppress(Exception):
            from .c06_graphdb_role_gate import _sync_hitl_roles_to_roster
            _sync_hitl_roles_to_roster(metadata)
        from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
            format_company_grouped_attendees,
        )
        roster = metadata.get("roster")
        attendees = metadata.get("attendees")
        if roster or attendees:
            lines = format_company_grouped_attendees(attendees=attendees, roster=roster)
            if lines:
                return lines
        if isinstance(user_content_or_metadata, str):
            names = re.findall(r'"name":\s*"([^"]+)"', user_content_or_metadata)
            if names:
                return format_company_grouped_attendees(attendees={"Key Stakeholders": names})
        return ["- PartnerAdvisory: Amit Ayer (Principal Architect & Technology Strategist)", "- Apex Global Enterprises (NEA): Key Leadership"]

    @staticmethod
    def _get_archetype_flags(arch: Any) -> tuple[bool, bool]:
        try:
            from dual_pipeline.prompts.contracts import (
                OUTPUT2_CONTRACT,
                MeetingArchetype,
                SectionRequirement,
            )
            arch_enum = MeetingArchetype.from_string(str(arch)) if arch else MeetingArchetype.GENERAL
            topo = OUTPUT2_CONTRACT.get_section_topology(arch_enum)
            return (
                topo.get("Immediate Delivery Scope & Release Boundaries") != SectionRequirement.FORBIDDEN,
                topo.get("Business Meaning and Value Case") != SectionRequirement.FORBIDDEN,
            )
        except Exception:
            return True, True

    @staticmethod
    def _extract_prior_actions(user_content_or_metadata) -> list:
        if isinstance(user_content_or_metadata, dict):
            text = user_content_or_metadata.get("prior_executive_recap") or user_content_or_metadata.get("prior_context") or ""
        elif isinstance(user_content_or_metadata, str):
            text = user_content_or_metadata
        else:
            return []
        lines, in_actions, prior = text.splitlines(), False, []
        for line in lines:
            s = line.strip()
            if any(s.startswith(h) for h in ("## Action Items", "## Next Steps", "## Action & Governance")):
                in_actions = True
            elif in_actions and s.startswith("## "):
                break
            elif in_actions and s.startswith("|") and not s.startswith(("| #", "|---")):
                parts = [p.strip() for p in s.split("|")]
                if len(parts) >= 8 and parts[2].lower() in ("action", "gate", "milestone", "blocker"):
                    prior.append({"item": parts[3], "owner": parts[4], "timing": parts[5], "locator": parts[6] or "Prior Synthesis"})
                elif len(parts) >= 6:
                    prior.append({"item": parts[2], "owner": parts[3], "timing": parts[4], "locator": parts[5] or "Prior Synthesis"})
        return prior

    @classmethod
    def _resolve_owners(cls, metadata: Optional[dict]) -> tuple[str, str]:
        if not metadata:
            return "Amit Ayer (Lead Enterprise Architect)", "Cameron Mercer (Managing Director)"
        names = []
        for r in (metadata.get("roster") or []):
            if isinstance(r, dict) and r.get("name"):
                role_suffix = f" ({r['role']})" if r.get("role") else ""
                names.append(f"{r['name']}{role_suffix}")
        if not names and isinstance(metadata.get("attendees"), dict):
            names = [str(p).strip() for g in metadata["attendees"].values() if isinstance(g, list) for p in g if p]
        o1 = names[0] if names else "Amit Ayer (Lead Enterprise Architect)"
        o2 = names[1] if len(names) >= 2 else (names[0] if names else "Cameron Mercer (Managing Director)")
        return o1, o2

    @classmethod
    def _build_action_rows(cls, prior_actions: list, metadata: Optional[dict] = None) -> str:
        o1, o2 = cls._resolve_owners(metadata)
        if not prior_actions:
            return (
                f"| 1 | Action | Finalize delivery charter and verify data contracts | {o1} | 10/01/2026 | [00:01:00] | Proposed |\n"
                f"| 2 | Gate | Architectural boundary ratification and schema validation signoff | {o2} | 10/15/2026 | [00:02:00] | Proposed |"
            )
        rows = []
        for idx, a in enumerate(prior_actions, 1):
            loc = a.get("locator") or "00:01:00"
            loc_str = str(loc) if str(loc).startswith("[") else f"[{loc}]"
            rows.append(f"| {idx} | Action | {a.get('item', '')} | {a.get('owner', o1)} | {a.get('timing', '10/01/2026')} | {loc_str} | Proposed |")
        if not any(a.get("item") == "Implement incremental operational additions" for a in prior_actions):
            rows.append(f"| {len(rows) + 1} | Action | Implement incremental operational additions | {o1} | 10/15/2026 | [00:02:00] | Proposed |")
        return "\n".join(rows)

    def _parse_duration_from_text(self, text: Any) -> Optional[int]:
        if not isinstance(text, str):
            return None
        m_dur = re.search(r'"duration(?:_minutes)?":\s*["\']?(\d+)', text) or re.search(r'(?:Duration|Time):\s*[~]?(\d+)\s*(?:m|min|minutes)\b', text, re.IGNORECASE)
        try:
            return int(m_dur.group(1)) if m_dur else None
        except (TypeError, ValueError):
            return None

    def _extract_duration_minutes(self, metadata: dict, user_content_or_metadata: Any) -> Optional[int]:
        dur = metadata.get("duration_minutes") or metadata.get("duration")
        if dur is not None and (m := re.search(r"\d+", str(dur))):
            with contextlib.suppress(TypeError, ValueError):
                return int(m.group())
        return self._parse_duration_from_text(user_content_or_metadata)

    def _render_deep_recap(
        self,
        meeting_name: str,
        date: str,
        time: str,
        attendees_str: str,
        actions_table_str: str,
        metadata: Optional[dict] = None,
        output_1: Optional[str] = None,
    ) -> str:
        deep_biz = (
            "Enterprise value case established around operational efficiency and developer capacity expansion. "
            "The current multi-system landscape spans disparate tooling across legacy platforms and siloed databases, "
            "resulting in significant manual reconciliation overhead, high defect leakage, and developer context switching. "
            "By establishing a unified execution spine and automated ingestion pipeline, the organization unlocks "
            "substantial time-to-market acceleration while eliminating costly manual data migration errors and reducing "
            "overall infrastructure operating expenses across delivery workstreams. "
            "This foundation ensures rigorous data governance, verifiable provenance across artifacts, and sustained architectural integrity."
        )
        deep_wedge = (
            "Immediate delivery scope confirms core pipeline enablement and automated ingestion for this milestone. "
            "Target users include operations engineers and platform reviewers evaluating ingestion reliability. "
            "In-scope boundaries include automated ingestion, deterministic routing, and schema validation components, "
            "while deferred legacy integrations remain explicitly quarantined for phase two execution. "
            "Clear acceptance criteria mandate sub-second parsing latency, zero unannotated timestamp gaps, and strict "
            "schema compliance before downstream graph persistence is authorized across test and production environments."
        )
        discussion_sections = [
            (
                "### Core Operating Model & Delivery Architecture\n"
                "The leadership team reviewed the target operating model for enterprise modernization. "
                "Key stakeholders aligned on the necessity of decoupling monolithic legacy workflows into modular, "
                "contract-governed stages. The engineering leads demonstrated that direct database connections in business "
                "logic have historically caused concurrency lockouts and unrecoverable deadlocks during peak batch runs. "
                "To mitigate this risk, the target architecture mandates write-ahead logging (WAL mode), 30-second busy timeouts, "
                "and serialized write queuing across all persistent storage operations."
            ),
            (
                "### Ingestion Pipeline Performance & Telemetry Validation\n"
                "The technical discussion evaluated the performance boundaries of the stream ingestion subsystem. "
                "Benchmarks confirm that native diarization pathways execute sub-second regex processing, while unstructured "
                "inputs require defensive token-budget management. Participants reviewed telemetry traces and agreed that "
                "reasoning models must be calibrated to appropriate effort tiers to prevent hidden reasoning token exhaustion "
                "and 360-second transport timeouts. Exit gates X1 through X3 will run deterministically prior to release packaging."
            ),
            (
                "### Technical Systems Landscape & Data Flow Integration\n"
                "The engineering leads mapped the technical systems landscape governing project delivery. "
                "Workflows intersect enterprise systems of record, core repositories, financial tracking systems, "
                "and asset management telemetry databases. The review revealed that manual reconciliation between disparate tools "
                "creates operational friction and reporting latency during regular cycles. Implementing event-driven data "
                "envelopes eliminates synchronization drift and secures end-to-end data lineage across workstream boundaries."
            ),

            (
                "### Cross-Functional Risk Management & Developer Velocity\n"
                "The delivery partner presented findings on developer throughput and friction points across active delivery pods. "
                "Uncertain schema requirements and lack of isolated test database fixtures have historically slowed deployment "
                "cadence. By adopting strict Pydantic contract validation, automated preflight scaffolding, and hermetic mock test "
                "environments, the team anticipates a forty percent reduction in defect remediation turnaround times. "
                "Leadership affirmed that all upcoming sprint commitments must prioritize automated verification gates."
            ),
            (
                "### Organizational Scaling & Multi-Workstream Governance\n"
                "Enterprise program leadership established guidelines for cross-workstream coordination. "
                "The project steering committee confirmed that workstream interfaces must maintain strict causal boundaries "
                "and explicit ownership contracts. When changes span multiple functional tracks, designated lead architects "
                "must conduct peer architectural reviews to ensure that shared domain entities, schema registries, and "
                "telemetry channels remain aligned with unified platform governance standards."
            ),
            (
                "### Quality Assurance & Release Governance Thresholds\n"
                "The team ratified the deterministic QA gate protocol. All downstream deliverables must satisfy non-compensating "
                "criteria: Gate 1 verifies forensic transcript source binding, attribution hygiene, and turn density; "
                "Gate 2 enforces structural completeness across all seven sections, word count ceilings (1,800 words), and typed "
                "action item registers; Gate 3 guarantees RFC-8259 JSON schema conformance with zero orphan nodes. "
                "Any delivery failing these requirements fails closed immediately without promotion."
            ),
        ]
        has_diagram = bool(
            metadata and (
                metadata.get("has_architecture_diagram")
                or metadata.get("reference_architecture_ascii")
                or any(isinstance(a, dict) and a.get("attachment_type") == "diagram" for a in metadata.get("attachments", []))
            )
        )
        if has_diagram and len(discussion_sections) > 1:
            ascii_block = (
                "```ascii\n"
                "┌─────────────────────────────────────────────────────────────┐\n"
                "│                SYSTEM ARCHITECTURE TOPOLOGY                 │\n"
                "├──────────────────────────────┬──────────────────────────────┤\n"
                "│ Ingestion Gateway            │ Hybrid Retrieval Sidecar     │\n"
                "│ • Deterministic Routing      │ • Dense & Sparse Vectors     │\n"
                "│ • Schema Validation (L1)     │ • Multi-Token Verification   │\n"
                "└──────────────────────────────┴──────────────────────────────┘\n"
                "```"
            )
            discussion_sections[1] += f"\n\n{ascii_block}"

        discussion_points = "\n\n".join(discussion_sections)

        arch = metadata.get("archetype") or metadata.get("meeting_type")
        include_wedge, include_biz = self._get_archetype_flags(arch)

        wedge_block = f"\n## Immediate Delivery Scope & Release Boundaries\n{deep_wedge}\n" if include_wedge else ""
        biz_block = f"\n## Business Meaning and Value Case\n{deep_biz}\n" if include_biz else ""
        tradeoff_block = ""
        if not include_biz and not include_wedge:
            tradeoff_block = (
                "\n## Architectural Trade-Off Matrix\n"
                "The engineering team evaluated three primary architectural approaches for the ingestion substrate:\n\n"
                "- **Option A (In-Memory Ephemeral State)**: Maximum throughput with microsecond latency, but unacceptable "
                "data loss vulnerability during worker process recycling or unhandled container exits.\n"
                "- **Option B (Direct SQLite Uncoordinated Access)**: Minimal operational complexity, but exhibits severe "
                "database lock contention under concurrent write transactions, causing intermittent timeout failures.\n"
                "- **Option C (Governed WAL Mode with Serialized Write Queue - Selected)**: Guarantees transactional ACID "
                "durability, non-blocking concurrent readers, and strictly serialized writes via connection pools. "
                "Team consensus unanimously affirmed Option C as the enterprise architectural standard across environments.\n\n"
                "Blast radius analysis confirms that isolated read replicas protect active query workloads from write locks, "
                "while automated rollback receipts ensure zero data degradation in the event of pipeline interruption.\n"
            )

        base_recap = f"""# Subject: {meeting_name} -- Executive Strategic & Technical Brief | {date}

**Date:** {date}
**Time:** {time} ET
**Attendees:**
{attendees_str}

---

## Meeting Objective
Executive strategic synthesis, target architecture alignment, and phased delivery roadmap confirmation across all declared engineering workstreams. The primary objective of this session is ratifying the unified pipeline topology, establishing robust cross-pod boundaries, and validating deterministic exit gates to eliminate regression risks during high-throughput operational execution.

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Platform stabilization is critical before scaling new workstreams | Immediate delivery focus shifts to core ingestion hardening and telemetry |
| Monolithic components create concurrency lock contention | Storage operations must strictly enforce WAL mode and serialized queues |
| Manual reconciliation causes high operational defect leakage | Universal automated ingestion with deterministic QA gates becomes mandatory |

## Key Discussion Points & Findings
{discussion_points}

## Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
{actions_table_str}
{biz_block}{wedge_block}{tradeoff_block}"""
        from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
        return heal_output_2_appendix(base_recap, metadata=metadata, output_1=output_1)

    def _render_standard_recap(
        self,
        meeting_name: str,
        date: str,
        time: str,
        attendees_str: str,
        actions_table_str: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        has_diagram = bool(
            metadata and (
                metadata.get("has_architecture_diagram")
                or metadata.get("reference_architecture_ascii")
                or any(isinstance(a, dict) and a.get("attachment_type") == "diagram" for a in metadata.get("attachments", []))
            )
        )
        diag_block = ""
        if has_diagram:
            diag_block = (
                "\n\n```ascii\n"
                "┌─────────────────────────────────────────────────────────────┐\n"
                "│                SYSTEM ARCHITECTURE TOPOLOGY                 │\n"
                "├──────────────────────────────┬──────────────────────────────┤\n"
                "│ Ingestion Gateway            │ Hybrid Retrieval Sidecar     │\n"
                "│ • Deterministic Routing      │ • Dense & Sparse Vectors     │\n"
                "│ • Schema Validation (L1)     │ • Multi-Token Verification   │\n"
                "└──────────────────────────────┴──────────────────────────────┘\n"
                "```"
            )

        base_recap = f"""# Subject: {meeting_name} -- Executive Recap & Next Steps | {date}

**Date:** {date}
**Time:** {time} ET
**Attendees:**
{attendees_str}

---

## Meeting Objective
Executive strategic synthesis, operating rhythm alignment, and phased implementation governance across all declared architectural workstreams. The primary objective is establishing verified cross-workstream coordination, eliminating unmanaged technical divergence, and ensuring rigorous compliance with canonical release lifecycle requirements.

## Key Discussion Points & Findings
- **Strategic Context & Governance Architecture** -- Core operating catalyst and delivery scope confirmed. All engineering modules adhere strictly to unified execution invariants, receipt verification gates, and atomic decision standards.{diag_block}
- **Implementation & Operational Readiness** -- Architecture aligns with deterministic routing protocols, zero-bypass verification requirements, and cross-modality parity across all engineering deliverables.
- **Delivery Milestones & Quality Assurance** -- Multi-tier evaluation verification enforces zero regression across deterministic schema validators, cross-artifact reconciliation matrices, and verified runtime boundaries.

## Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
{actions_table_str}"""
        from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
        return heal_output_2_appendix(base_recap, metadata=metadata)

    def synthesize_recap(
        self,
        system_prompt_or_prompt: str,
        user_content_or_metadata=None,
        _legacy_metadata: dict = None,
    ) -> str:
        metadata = self._resolve_recap_metadata(user_content_or_metadata, _legacy_metadata)
        meeting_name = metadata.get("meeting_name", "Meeting")
        date = metadata.get("date", "01/01/2026")
        time = metadata.get("time", "10:00AM - 11:00AM")

        attendees_lines = self._build_attendees_lines(metadata, user_content_or_metadata)
        attendees_str = "\n".join(attendees_lines)
        prior_actions = self._extract_prior_actions(user_content_or_metadata)
        if not prior_actions and isinstance(metadata, dict) and metadata.get("prior_executive_recap"):
            prior_actions = self._extract_prior_actions(metadata["prior_executive_recap"])
        actions_table_str = self._build_action_rows(prior_actions, metadata)

        output_1 = None
        if isinstance(user_content_or_metadata, str):
            m_tr = re.search(r"<forensic_transcript>\s*([\s\S]*?)\s*</forensic_transcript>", user_content_or_metadata)
            if m_tr:
                output_1 = m_tr.group(1).strip()
            else:
                m_raw = re.search(r"<raw_transcript>\s*([\s\S]*?)\s*</raw_transcript>", user_content_or_metadata)
                if m_raw:
                    output_1 = m_raw.group(1).strip()

        return self._render_deep_recap(
            meeting_name, date, time, attendees_str, actions_table_str, metadata=metadata, output_1=output_1
        )

    def synthesize_forensic(self, system_prompt: str, user_content: str) -> str:
        """Return a minimal valid forensic transcript fixture preserving input dialogue."""
        meta, date = _extract_meta_and_date(user_content)
        meeting_name = meta.get("meeting_name") or meta.get("title") or meta.get("meeting") or "Meeting"
        roster_names = _extract_roster_names(meta, user_content)
        participants_str = ", ".join(roster_names) if roster_names else "Offline participant"

        raw_match = re.search(r"<raw_transcript>\s*([\s\S]*?)\s*</raw_transcript>", user_content)
        raw_body = raw_match.group(1).strip() if raw_match else (user_content[:500] if user_content else "[Transcript content]")
        from .lexical_normalizer import format_ambiguity_lock_register
        spk_map = meta.get("speaker_map") or {}
        alr_table = format_ambiguity_lock_register(
            meta or {"roster": roster_names, "attendees": roster_names, "speaker_map": spk_map}, spk_map
        )
        attribution_summary_block = _build_attribution_block(roster_names, metadata=meta, dialogue_text=raw_body)

        return f"""# {meeting_name} -- HITL-Resolved Transcript | {date}

**Date:** {date}
**Platform Reference:** Not provided
**Participants:** {participants_str}

---

## Transcript

{raw_body}

---

## Ambiguity Lock Register
{alr_table}
{attribution_summary_block}
## Evidence Locks / Semantic Constraints
None.
"""

    def synthesize_graph(self, system_prompt_or_prompt: str, user_content_or_metadata=None, _legacy_metadata: dict = None) -> str:
        from .offline_graph_generator import generate_offline_graph
        return generate_offline_graph(
            system_prompt_or_prompt,
            user_content_or_metadata=user_content_or_metadata,
            legacy_metadata=_legacy_metadata,
        )

    def synthesize_metadata(self, system_prompt: str, user_content: str) -> str:
        return json.dumps({})

    def synthesize_agentic_leverage(self, system_prompt: str, user_content: str) -> List[AgenticLeverageNode]:
        """Return fixture AgenticLeverageNode instances for testing / offline execution."""
        return [
            AgenticLeverageNode(node_type="INFO_ASYMMETRY", key_players=["Tanner (VP)", "Drew Oakley (Managing Director)"], political_rationale="Cloud migration schedule lag.", networking_wedge="Ping Tanner privately.", verbatim_quote="Dates not finalized.", speaker="Drew Oakley"),
            AgenticLeverageNode(node_type="partner_advisory_MARGIN_WEDGE", key_players=["Sarah Jenkins (NEE Director)", "PartnerAdvisory Partner"], political_rationale="Bandwidth pinch before FERC deadline.", networking_wedge="Ping Sarah with pre-cleared pod.", verbatim_quote="Not enough bodies.", speaker="Sarah Jenkins"),
        ]
