"""
dual_pipeline/transcript_pipeline_core/cross_meeting_context.py

Cross-Meeting Context Engine.
Retrieves and synthesizes recent meeting recaps (Output 02) and canonical entity
projections (Output 03) across the same project within a sliding window (default: 7 days).
Enables cross-meeting entity continuity, eliminates isolated meeting amnesia,
and grounds referenced third-party stakeholders under Constraint C-27.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.parameters import (
    get_cross_meeting_context_window_days,
    get_cross_meeting_max_context_tokens,
)

logger = logging.getLogger(__name__)


@dataclass
class CrossMeetingEntity:
    token: str
    canonical_name: str
    org: str
    role: str
    source_meeting: str
    meeting_date: str


@dataclass
class CrossMeetingSummary:
    meeting_name: str
    meeting_date: str
    attendees: List[str]
    key_decisions: List[str]
    open_actions: List[str]
    entities: List[CrossMeetingEntity]


class CrossMeetingContextLoader:
    """Loads and synthesizes cross-meeting context across project output directories."""

    def __init__(
        self,
        window_days: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        self.window_days = (
            window_days if window_days is not None else get_cross_meeting_context_window_days()
        )
        self.max_tokens = (
            max_context_tokens if max_context_tokens is not None else get_cross_meeting_max_context_tokens()
        )
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent.parent

    def _parse_meeting_date(self, date_str: str) -> Optional[datetime.date]:
        """Parses MM/DD/YYYY or MM-DD-YYYY into a date object."""
        if not date_str:
            return None
        clean = date_str.strip().replace("/", "-")
        parts = clean.split("-")
        if len(parts) == 3:
            try:
                # MM-DD-YYYY
                if len(parts[0]) <= 2 and len(parts[2]) == 4:
                    return datetime.date(int(parts[2]), int(parts[0]), int(parts[1]))
                # YYYY-MM-DD
                if len(parts[0]) == 4:
                    return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                return None
        return None

    def scan_project_meetings(
        self,
        project_name: str,
        current_date_str: str,
        current_meeting_name: Optional[str] = None,
    ) -> List[CrossMeetingSummary]:
        """Scans project output directory for recent meetings within window."""
        curr_date = self._parse_meeting_date(current_date_str)
        if not curr_date:
            curr_date = datetime.date.today()

        project_dir = self.repo_root / "output" / project_name
        if not project_dir.is_dir():
            return []

        summaries: List[CrossMeetingSummary] = []
        date_dirs = [d for d in project_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

        for d_dir in sorted(date_dirs, reverse=True):
            dir_date = self._parse_meeting_date(d_dir.name)
            if not dir_date:
                continue

            delta_days = (curr_date - dir_date).days
            if delta_days < 0 or delta_days > self.window_days:
                continue

            # Check meeting subdirectories or flat meetings
            meeting_dirs = [m for m in d_dir.iterdir() if m.is_dir() and not m.name.startswith(".")]
            targets = meeting_dirs if meeting_dirs else [d_dir]

            for m_dir in targets:
                # Avoid circular self-reference on identical meeting re-runs
                if current_meeting_name and current_meeting_name.lower() in m_dir.name.lower():
                    continue

                summary = self._extract_meeting_summary(m_dir, d_dir.name)
                if summary:
                    summaries.append(summary)

        return summaries

    def _extract_meeting_summary(self, m_dir: Path, date_label: str) -> Optional[CrossMeetingSummary]:
        """Extracts key context from Output 02 recap and Output 03 graph JSON."""
        f2_files = list(m_dir.glob("02_*_executive_recap.md"))
        f3_files = list(m_dir.glob("03_*_canonical_signal_projection.json"))

        if not f2_files and not f3_files:
            return None

        attendees: List[str] = []
        decisions: List[str] = []
        actions: List[str] = []
        entities: List[CrossMeetingEntity] = []

        if f2_files:
            try:
                f2_text = f2_files[0].read_text(encoding="utf-8")
                # Extract attendees from bold or standard header
                att_match = re.search(r"(?:\*\*Attendees:\*\*|Attendees:)\s*(.*?)(?:\n\n\n|\n---|\n#|\Z)", f2_text, re.DOTALL)
                if att_match:
                    for line in att_match.group(1).splitlines():
                        line_clean = line.strip().lstrip("-* ").strip()
                        if line_clean and not line_clean.lower().startswith(("partner_advisory:", "apex_global", "attendees")):
                            for part in line_clean.split(","):
                                p_clean = part.strip()
                                if p_clean:
                                    attendees.append(p_clean)

                # Extract key decisions / discussion points
                for m in re.finditer(r"\*\*([A-Z][a-zA-Z0-9\s\-]+?\s+(?:decided|ratified|confirmed|agreed|approved|rejected|established)[^\n\*]+)\*\*", f2_text):
                    decisions.append(m.group(1).strip())

                # Extract action item rows
                for line in f2_text.splitlines():
                    if "|" in line and any(k in line for k in ("PartnerAdvisory", "NEA", "Apex Global", "Client")) and not line.startswith("| Item"):
                        parts = [p.strip() for p in line.split("|") if p.strip()]
                        if len(parts) >= 3:
                            actions.append(f"{parts[1]} (Owner: {parts[0]})")
            except Exception as e:
                logger.debug(f"Error parsing Output 02 in {m_dir}: {e}")

        if f3_files:
            try:
                f3_data = json.loads(f3_files[0].read_text(encoding="utf-8"))
                # Schema 5.7 entity_upserts
                all_ents = (
                    f3_data.get("graph_mutations", {}).get("entity_upserts", [])
                    or f3_data.get("entity_upserts", [])
                    or f3_data.get("entities", [])
                )
                for ent in all_ents:
                    name = ent.get("canonical_name") or ent.get("name", "")
                    org = ent.get("organization") or ent.get("org", "Unknown")
                    role = ent.get("role", "Stakeholder")
                    if name:
                        first = name.split()[0].lower()
                        entities.append(
                            CrossMeetingEntity(
                                token=first,
                                canonical_name=name,
                                org=org,
                                role=role,
                                source_meeting=m_dir.name,
                                meeting_date=date_label,
                            )
                        )
                # Also extract verified speakers from evidence_index
                for ev in f3_data.get("evidence_index", {}).values():
                    if isinstance(ev, dict) and ev.get("speaker"):
                        spk = ev.get("speaker", "").strip()
                        if spk and "speaker" not in spk.lower():
                            first = spk.split()[0].lower()
                            res_org = "Verified Speaker"
                            res_role = "Participant"
                            try:
                                from dual_pipeline.transcript_metadata_core.entity_resolver import (
                                    resolve_person_entity,
                                )
                                from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
                                    strip_org_suffix_from_role,
                                )
                                ent = resolve_person_entity(spk)
                                if ent and ent.canonical_name:
                                    if ent.org and ent.org not in ("Client / Partner", "Unknown"):
                                        res_org = ent.org
                                    if ent.role and ent.role not in ("Participant", "Unknown"):
                                        res_role = strip_org_suffix_from_role(ent.role)
                                        if " / " in res_role:
                                            res_role = res_role.split(" / ")[0].strip()
                            except Exception as e:
                                logger.debug("Failed resolving person in cross-meeting context: %s", e)

                            entities.append(
                                CrossMeetingEntity(
                                    token=first,
                                    canonical_name=spk,
                                    org=res_org,
                                    role=res_role,
                                    source_meeting=m_dir.name,
                                    meeting_date=date_label,
                                )
                            )
            except Exception as e:
                logger.debug(f"Error parsing Output 03 in {m_dir}: {e}")

        return CrossMeetingSummary(
            meeting_name=m_dir.name,
            meeting_date=date_label,
            attendees=attendees[:10],
            key_decisions=decisions[:8],
            open_actions=actions[:8],
            entities=entities,
        )

    def build_cross_meeting_packet(
        self,
        project_name: str,
        current_date_str: str,
        current_meeting_name: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Dict[str, Any]]]:
        """Builds XML context block and entity lookup registry from recent project meetings."""
        summaries = self.scan_project_meetings(project_name, current_date_str, current_meeting_name)
        entity_registry: Dict[str, Dict[str, Any]] = {}

        if not summaries:
            return "", {}

        blocks: List[str] = [
            f'<cross_meeting_context project="{project_name}" window_days="{self.window_days}">'
        ]

        blocks.append("  <prior_project_meetings>")
        for s in summaries[:4]:  # Cap at 4 most recent meetings to stay well within token budget
            blocks.append(f'    <meeting date="{s.meeting_date}" name="{s.meeting_name}">')
            if s.attendees:
                blocks.append(f"      <attendees>{', '.join(s.attendees[:6])}</attendees>")
            if s.key_decisions:
                blocks.append("      <key_decisions>")
                for dec in s.key_decisions[:4]:
                    blocks.append(f"        <decision>{dec}</decision>")
                blocks.append("      </key_decisions>")
            if s.open_actions:
                blocks.append("      <open_actions>")
                for act in s.open_actions[:4]:
                    blocks.append(f"        <action>{act}</action>")
                blocks.append("      </open_actions>")
            blocks.append("    </meeting>")

            for ent in s.entities:
                if ent.token not in entity_registry:
                    entity_registry[ent.token] = {
                        "canonical_name": ent.canonical_name,
                        "org": ent.org,
                        "role": ent.role,
                        "source_meeting": ent.source_meeting,
                        "date": ent.meeting_date,
                    }
        blocks.append("  </prior_project_meetings>")

        if entity_registry:
            blocks.append("  <verified_project_entities>")
            for token, info in list(entity_registry.items())[:15]:
                blocks.append(
                    f'    <entity token="{token}" name="{info["canonical_name"]}" '
                    f'org="{info["org"]}" role="{info["role"]}" />'
                )
            blocks.append("  </verified_project_entities>")

        blocks.append("</cross_meeting_context>")
        context_xml = "\n".join(blocks)

        logger.info(
            f"CrossMeetingContextLoader compiled context from {len(summaries)} prior meetings "
            f"with {len(entity_registry)} verified entities for {project_name}."
        )
        return context_xml, entity_registry
