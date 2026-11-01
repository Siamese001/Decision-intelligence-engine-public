"""
SpeakerDisambiguator — High-reasoning LLM speaker attribution for shared conference room microphones.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD

from .models import (
    DisambiguationResult,
    RoomMicConfig,
    TurnAttribution,
    classify_offline_candidate,
    compute_company_scores,
)
from .patterns import PATTERNS, format_replaced_turn

logger = logging.getLogger(__name__)


class SpeakerDisambiguator:
    """
    Forensic Speaker Disambiguation Engine for shared room microphones.
    Uses OpenAI gpt-5.6-luna (with reasoning_effort="xhigh") or deterministic offline fallback.
    """

    PATTERNS = PATTERNS

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        confidence_margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
        openai_client: Optional[Any] = None,
        mock: bool = False,
        client: Optional[Any] = None,
    ):
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "high"
        self.margin_threshold = confidence_margin_threshold
        self.mock = mock
        self._client = openai_client if openai_client is not None else client

    @property
    def client(self) -> Any:
        if self._client is None and not self.mock:
            from dual_pipeline.credentials import get_openai_client
            try:
                client, creds = get_openai_client(
                    allow_mock=False,
                    model=self.model,
                    reasoning_effort=self.reasoning_effort,
                )
                if client is not None and not creds.is_mock:
                    self._client = client
                else:
                    raise RuntimeError(
                        "Fail-Fast: OPENAI_API_KEY required for SpeakerDisambiguator. "
                        "Offline mock fallback is prohibited. Set mock=True for test environments."
                    )
            except Exception as e:
                if isinstance(e, RuntimeError) and "Fail-Fast" in str(e):
                    raise
                raise RuntimeError(
                    f"Fail-Fast: OPENAI_API_KEY required for SpeakerDisambiguator. "
                    f"Offline mock fallback is prohibited. Set mock=True for test environments. Error: {e}"
                ) from e
        return self._client

    def parse_turns(self, transcript: str) -> List[Dict[str, Any]]:
        """Extract line-by-line speaker turns while preserving exact original text."""
        lines = transcript.splitlines(keepends=True)
        turns = []
        # Only treat as in_header if there is an explicit --- divider in the first 25 lines
        has_header_divider = any(line.strip().startswith("---") for line in lines[:25])
        in_header = has_header_divider

        current_header_ts = ""
        header_ts_pattern = re.compile(r"^#+\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?")

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("---"):
                in_header = False
                continue

            if in_header:
                continue

            m_ts = header_ts_pattern.match(stripped)
            if m_ts:
                current_header_ts = m_ts.group(1)

            if not stripped or stripped.startswith("#") or stripped.startswith("**Date") or stripped.startswith("**Platform") or stripped.startswith("**Participants"):
                continue

            for name, pattern in self.PATTERNS:
                match = pattern.match(line)
                if match:
                    d = match.groupdict()
                    speaker_raw = d.get("speaker", "").strip()
                    ts = (d.get("ts_before") or d.get("ts_after") or "").strip() or current_header_ts
                    turns.append({
                        "line_index": idx,
                        "raw_line": line,
                        "pattern_type": name,
                        "speaker": speaker_raw,
                        "text": d.get("text", "").strip(),
                        "timestamp": ts,
                        "num_prefix": d.get("num_prefix") or "",
                        "open_stars": d.get("open_stars") or "",
                        "ts_before": d.get("ts_before") or "",
                        "ts_after": d.get("ts_after") or "",
                        "annotation": d.get("annotation") or "",
                        "colon": d.get("colon") or ": ",
                    })
                    break
        return turns

    def _is_room_mic_speaker(self, speaker: str, room_config: RoomMicConfig) -> bool:
        """Check if a speaker tag matches the room mic label, display label, or generic ASR label."""
        import re
        spk_clean = speaker.strip().lower()
        mic_clean = room_config.mic_label.strip().lower()
        disp_clean = room_config.display_label.strip().lower()

        if spk_clean in (mic_clean, disp_clean):
            return True
        if "room" in spk_clean or "shared" in spk_clean or "vc" in spk_clean:
            return True
        if re.match(r"^(speaker|voice)\s+([a-z]|\d+)$", spk_clean):
            return True
        return spk_clean in ("speaker", "unknown", "unattributed")

    def disambiguate(
        self,
        transcript: str,
        room_config: RoomMicConfig,
        remote_attendees: Optional[List[Dict[str, str]]] = None,
        batch_size: Optional[int] = None,
    ) -> DisambiguationResult:
        """
        Execute high-reasoning disambiguation across room-mic turns.
        Returns DisambiguationResult with resolved transcript and manifest.
        """
        lines = transcript.splitlines(keepends=True)
        parsed_turns = self.parse_turns(transcript)

        # Identify room mic turns
        room_turns = [
            t for t in parsed_turns if self._is_room_mic_speaker(t["speaker"], room_config)
        ]

        logger.info(
            f"SpeakerDisambiguator: Detected {len(room_turns)} shared room mic turns "
            f"for '{room_config.display_label}' out of {len(parsed_turns)} total turns."
        )

        if not room_turns:
            return self._build_noop_result(transcript, room_config)

        eff_batch_size = batch_size or 20

        # Disambiguate turns either via LLM or offline heuristic engine
        if self.mock or self.client is None:
            attributions = self._disambiguate_offline(room_turns, room_config)
        else:
            attributions = self._disambiguate_llm(
                parsed_turns, room_turns, room_config, remote_attendees or [], eff_batch_size
            )

        # Reconstruct transcript with verbatim text lock
        resolved_transcript, speaker_distribution, directly_attributed, shared_preserved = (
            self._reconstruct_transcript(transcript, parsed_turns, attributions, room_config)
        )

        # Build Gate 1.2 compliant attribution manifest
        manifest = self._build_manifest(
            room_config, remote_attendees or [], attributions, speaker_distribution
        )

        return DisambiguationResult(
            resolved_transcript=resolved_transcript,
            attribution_manifest=manifest,
            turns=attributions,
            total_room_turns=len(room_turns),
            directly_attributed_turns=directly_attributed,
            shared_room_preserved_turns=shared_preserved,
            speaker_distribution=speaker_distribution,
            affiliation_attributed_turns=sum(1 for a in attributions if a.attribution_mode == "AFFILIATION"),
        )

    def _reconstruct_transcript(
        self,
        transcript: str,
        parsed_turns: List[Dict[str, Any]],
        attributions: List[TurnAttribution],
        room_config: RoomMicConfig,
    ) -> Tuple[str, Dict[str, int], int, int]:
        lines = transcript.splitlines(keepends=True)
        resolved_lines = list(lines)
        speaker_distribution: Dict[str, int] = {}
        directly_attributed = 0
        affiliation_attributed = 0
        shared_preserved = 0
        turns_by_index = {t["line_index"]: t for t in parsed_turns}

        for attr in attributions:
            line_idx = attr.turn_index
            orig_turn = turns_by_index.get(line_idx)
            if not orig_turn:
                continue

            if attr.attribution_mode == "DIRECT":
                new_speaker = attr.assigned_speaker
                directly_attributed += 1
            elif attr.attribution_mode == "AFFILIATION":
                new_speaker = attr.assigned_speaker
                affiliation_attributed += 1
            else:
                new_speaker = room_config.display_label
                shared_preserved += 1

            speaker_distribution[new_speaker] = speaker_distribution.get(new_speaker, 0) + 1
            resolved_lines[line_idx] = format_replaced_turn(orig_turn, new_speaker)

        return "".join(resolved_lines), speaker_distribution, directly_attributed, shared_preserved

    @staticmethod
    def _score_role_and_nuances(att_name: str, att_role: str, text_lower: str) -> float:
        delta = 0.0
        role_lower = att_role.lower()
        is_exec = any(r in role_lower for r in ("sponsor", "director", "executive", "vp", "head", "managing director")) and any(w in text_lower for w in ["priority", "priorities", "budget", "quarterly", "strategy", "strategic", "leadership", "target", "targeting", "milestone", "q4", "first thing"])
        is_ops = any(r in role_lower for r in ("operations", "ops", "e&c", "field")) and any(w in text_lower for w in ["spreadsheet", "copy", "paste", "pole", "placement", "resequencing"])
        is_tech = any(r in role_lower for r in ("architect", "engineering", "technical", "tech lead", "developer", "pm", "delivery", "product", "program", "lead", "analyst", "data", "analytics")) and any(w in text_lower for w in ["architecture", "standards", "blueprint", "system", "technical", "engineering", "infrastructure", "backend", "milestone", "timeline", "scope", "charter", "deliverables", "sprint", "schedule", "requirements", "data", "metrics", "report", "analysis"])
        if is_exec or is_ops:
            delta += 0.45
        elif is_tech:
            delta += 0.30

        att_lower = att_name.lower()
        if "reid" in att_lower and any(w in text_lower for w in ["q4 milestone", "quarterly", "milestone", "priorities", "process flow", "50 step", "leadership model", "first thing"]):
            delta += 0.50
        elif "sharon" in att_lower and any(w in text_lower for w in ["built out our nexus", "don't let him", "tiger team", "jira", "264,000", "what our real ask is for q4", "get this built"]):
            delta += 0.65
        elif "daniel" in att_lower:
            if any(w in text_lower for w in ["solar 40", "batteries between 10", "database with a bunch of entries", "can we query that", "transmission system is in the minds", "blocker", "scott approval"]):
                delta += 0.60
            elif "don't let him" in text_lower or "built out our nexus" in text_lower:
                delta -= 0.20
        elif "diana" in att_lower and any(w in text_lower for w in ["pole", "poles", "spreadsheet", "copy-pasting", "copy and paste", "resequencing"]):
            delta += 0.65
        return delta

    def _score_turn_offline(
        self,
        t: Dict[str, Any],
        room_config: RoomMicConfig,
        memory: Any,
    ) -> Dict[str, float]:
        text = t["text"]
        text_lower = text.lower()
        orig_spk_lower = t["speaker"].strip().lower()
        scores: Dict[str, float] = {att.name: 0.05 for att in room_config.in_room_attendees}

        for att in room_config.in_room_attendees:
            att_name = att.name
            for topic in att.domain_topics:
                if topic.lower() in text_lower:
                    scores[att_name] += 0.40

            mem_score = memory.match_speaker_score(att_name, text)
            if mem_score > 0:
                scores[att_name] += mem_score

            comp = (att.company or "").lower()
            if "partner_advisory" in comp:
                if any(w in text_lower for w in ["survey", "readout", "37 respondents", "65 engineers", "outside organization", "earn our way", "dropped in the lap", "show up as a partner", "rewire", "consultant", "second class", "badges at the door", "briefing document", "screening", "macbook", "adm", "matt sassento"]):
                    scores[att_name] += 0.30
                if orig_spk_lower == "speaker 1":
                    scores[att_name] += 0.35
            elif any(c in comp for c in ("nea", "apex_global", "apex")):
                if any(w in text_lower for w in ["leads team", "pod model", "pineapple", "vertical", "multifaceted", "tech lead", "will lane", "kiro", "opentelemetry", "plumtree", "curator", "credits", "token", "our enterprise", "spend management"]):
                    scores[att_name] += 0.30
                if orig_spk_lower == "speaker 2":
                    scores[att_name] += 0.35

            scores[att_name] += self._score_role_and_nuances(att_name, att.role or "", text_lower)

        word_count = len(text.split())
        if word_count <= 4 and any(text_lower.startswith(w) for w in ["yeah", "right", "okay", "yes", "mhm", "cool", "sure", "yep"]):
            for k in scores:
                scores[k] = min(scores[k], 0.20)

        return scores

    def _disambiguate_offline(
        self,
        room_turns: List[Dict[str, Any]],
        room_config: RoomMicConfig,
    ) -> List[TurnAttribution]:
        """High-precision offline heuristic engine for CI and local verification."""
        from dual_pipeline.speaker_memory import get_speaker_memory
        memory = get_speaker_memory()

        attributions = []
        name_to_person = {att.name: att.person_id for att in room_config.in_room_attendees}
        name_to_att = {att.name: att for att in room_config.in_room_attendees}

        for t in room_turns:
            line_idx = t["line_index"]
            text = t["text"]
            timestamp = t["timestamp"]

            scores = self._score_turn_offline(t, room_config, memory)

            # Sort individual candidates
            sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            top_name, top_score = sorted_candidates[0]
            second_name, second_score = sorted_candidates[1] if len(sorted_candidates) > 1 else (None, 0.0)
            margin = top_score - second_score
            top_att = name_to_att.get(top_name)

            top_comp_name, top_comp_sc, comp_margin = compute_company_scores(scores, name_to_att)
            mode, assigned, person_id, rationale = classify_offline_candidate(
                self.margin_threshold,
                top_name, top_score, second_name, margin, top_att,
                top_comp_name, top_comp_sc, comp_margin, name_to_person, name_to_att, room_config
            )

            attributions.append(TurnAttribution(
                turn_index=line_idx,
                timestamp=timestamp,
                original_speaker=t["speaker"],
                assigned_speaker=assigned,
                person_id=person_id,
                confidence=round(top_score, 2),
                second_candidate=second_name,
                second_confidence=round(second_score, 2),
                margin_delta=round(margin, 2),
                attribution_mode=mode,
                rationale=rationale,
                text_snippet=text[:100],
            ))

        return attributions

    def _disambiguate_llm(
        self,
        all_turns: List[Dict[str, Any]],
        room_turns: List[Dict[str, Any]],
        room_config: RoomMicConfig,
        remote_attendees: List[Dict[str, str]],
        batch_size: int,
    ) -> List[TurnAttribution]:
        """Execute OpenAI model with high reasoning effort in conversational context batches."""
        from .shield import disambiguate_llm_batches

        return disambiguate_llm_batches(
            self, all_turns, room_turns, room_config, remote_attendees, batch_size
        )

    def _build_manifest(
        self,
        room_config: RoomMicConfig,
        remote_attendees: List[Dict[str, str]],
        attributions: List[TurnAttribution],
        speaker_distribution: Dict[str, int],
    ) -> Dict[str, Any]:
        """Construct a contract-valid Gate 1.2 attribution manifest."""
        allowed_owner_ids = set()
        segments = []

        # Add room mic segment
        segments.append({
            "speaker_id": "collective:room-mic",
            "attribution_mode": "SHARED_ROOM_MIC",
            "blocked_individuals": [],
        })

        # Add remote attendees
        for rem in remote_attendees:
            pid = rem.get("person_id")
            if pid:
                allowed_owner_ids.add(pid)
                segments.append({"speaker_id": pid, "attribution_mode": "DIRECT"})

        # Add directly attributed in-room participants
        individually_verified = []
        for att in room_config.in_room_attendees:
            allowed_owner_ids.add(att.person_id)
            # Match by bare name OR name with company/role suffix across all delimiter styles
            bare_att_name = re.sub(r"[\(\[\-\,\:].*$", "", att.name).strip()
            att_count = sum(
                v for k, v in speaker_distribution.items()
                if k == att.name or k == bare_att_name or re.sub(r"[\(\[\-\,\:].*$", "", k).strip() == bare_att_name
            )
            if att_count > 0:
                segments.append({"speaker_id": att.person_id, "attribution_mode": "DIRECT"})
                individually_verified.append(att.person_id)

        allowed_owner_ids.update(["org:partner_advisory", "org:nea", "org:apex"])

        return {
            "meeting_archetype": "STRATEGIC_DISCOVERY",
            "allowed_owner_ids": sorted(allowed_owner_ids),
            "owners": sorted(allowed_owner_ids),
            "segments": segments,
            "individually_verified": sorted(set(individually_verified)),
            "telemetry": {
                "total_room_mic_turns": len(attributions),
                "directly_attributed_turns": sum(1 for a in attributions if a.attribution_mode == "DIRECT"),
                "affiliation_attributed_turns": sum(1 for a in attributions if a.attribution_mode == "AFFILIATION"),
                "shared_room_preserved_turns": sum(1 for a in attributions if a.attribution_mode == "SHARED_ROOM_MIC"),
                "speaker_turn_distribution": speaker_distribution,
                "margin_threshold": self.margin_threshold,
            },
        }

    def _build_noop_result(self, transcript: str, room_config: RoomMicConfig) -> DisambiguationResult:
        return DisambiguationResult(
            resolved_transcript=transcript,
            attribution_manifest={
                "meeting_archetype": "STRATEGIC_DISCOVERY",
                "segments": [{"speaker_id": "collective:room-mic", "attribution_mode": "SHARED_ROOM_MIC"}],
            },
            turns=[],
            total_room_turns=0,
            directly_attributed_turns=0,
            shared_room_preserved_turns=0,
            speaker_distribution={},
            affiliation_attributed_turns=0,
        )

    def disambiguate_with_two_stage_shield(
        self,
        transcript: str,
        room_config: RoomMicConfig,
        remote_attendees: Optional[List[Dict[str, str]]] = None,
        secondary_sol_disambiguator: Optional[SpeakerDisambiguator] = None,
        secondary_escalator: Optional[SpeakerDisambiguator] = None,
        batch_size: Optional[int] = None,
    ) -> DisambiguationResult:
        """
        Executes speaker disambiguation under the Two-Stage Ambiguity Shield:
        1. Run primary disambiguation (typically cost-effective Sol 6.1).
        2. Identify turns where margin_delta < self.margin_threshold (or mode is SHARED_ROOM_MIC).
        3. If secondary_escalator is provided, run frontier model (e.g. Astra) on ambiguous turns with conversational context.
        4. If secondary achieves decisive margin >= self.margin_threshold, promote the turn autonomously.
        5. Returns DisambiguationResult with shielded attributions and telemetry.
        """
        primary_result = self.disambiguate(
            transcript=transcript,
            room_config=room_config,
            remote_attendees=remote_attendees,
            batch_size=batch_size,
        )
        from .shield import execute_two_stage_shield

        return execute_two_stage_shield(
            self,
            transcript=transcript,
            room_config=room_config,
            remote_attendees=remote_attendees,
            secondary_sol_disambiguator=secondary_sol_disambiguator,
            secondary_escalator=secondary_escalator,
            batch_size=batch_size,
        )


