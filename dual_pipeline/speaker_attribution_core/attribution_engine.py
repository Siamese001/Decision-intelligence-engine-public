"""Pre-Output 01 Human-in-the-Loop (HITL) Speaker Attribution Engine.
Analyzes speech clusters, checks audio uncertainty and role consistency,
enforces SSOT 20% ambiguity margin rule (`dual_pipeline.parameters`), and records verified decisions in hitl_decision_ledger.json."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
)
from dual_pipeline.core import _GENERIC_SPEAKER_PATTERN
from dual_pipeline.exceptions import HITLRequiredError
from .audio_uncertainty_detector import (
    check_ledger_for_audio_resolution, detect_audio_role_discrepancies, extract_roster_with_roles,
)
from .candidate_scorer import (
    extract_speaker_turns, load_diarization_tracks, load_speaker_hints, normalize_search_text, score_candidates_for_speaker,
)
from .collapse_detector import check_speaker_skew_and_collapse
from .conflict_detector import (
    guard_collapse_or_evict_for_hitl,
    reconcile_existing_map_conflicts,
    run_iterative_conflict_discrepancies,
)
from .models import (
    AttributionResult, CandidateAttribution, SpeakerAttributionCollisionError, TurnAttributionDiscrepancy,
)
from .quote_hint_turn_mapper import apply_quote_hints_to_turns
from .review_context_render import build_hitl_review_payload, build_speaker_review_question

logger = logging.getLogger(__name__)

_TURN_SPEAKER_PATTERN = re.compile(
    r"(?:^|\n|\](?:\*\*)?\s*)(?:\*\*)?\s*\[?(Speaker(?:\s+[A-Za-z0-9]+)?|Voice(?:\s+[A-Za-z0-9]+)?|PartnerAdvisory\s+Speaker|SPEAKER_\d+|Unknown(?:\s+Speaker)?|Unattributed)\]?(?:\*\*)?(?:\s*\[[^\]]+\])?:",
    re.IGNORECASE,
)


def _isolate_body(text: str) -> str:
    body = text
    for div in ("\n## Live Feed", "\n## Transcript", "\n---"):
        if div in text and len(parts := text.split(div, 1)) > 1 and len(parts[1].strip()) > 50:
            body = parts[1]
            break
    for foot in ("\n## Ambiguity Lock Register", "\n## Attribution Summary", "\n# Ambiguity Lock Register"):
        if foot in body:
            body = body.split(foot, 1)[0]
    return body


class AudioSpeakerAttributionEngine:
    """Pre-Output 01 forensic speaker attribution engine for AUDIO_ONLY_UNDIARIZED inputs."""

    def __init__(
        self,
        decision_store: Optional[HITLDecisionStore] = None,
        margin_threshold: Optional[float] = None,
        min_autonomous_confidence: float = 0.85,
        voiceprint_store: Optional[Any] = None,
    ) -> None:
        eff_margin = get_hitl_ambiguity_margin_threshold() if margin_threshold is None else float(margin_threshold)
        self.store, self.margin_threshold, self.min_autonomous_confidence = (
            decision_store or HITLDecisionStore(), eff_margin, min_autonomous_confidence
        )
        self.voiceprint_store = voiceprint_store

    def _resolve_single_discrepancy(
        self, disc: TurnAttributionDiscrepancy, metadata: Dict[str, Any],
        interactive_resolver: Optional[Callable[[str, List[str]], str]],
        autonomous_if_decisive: bool, transcript: str,
    ) -> Optional[tuple[str, str]]:
        prior_resolved = check_ledger_for_audio_resolution(
            disc.cluster_id, self.store, project=metadata.get("project"),
            meeting_name=metadata.get("meeting_name") or metadata.get("title"),
            date=metadata.get("date") or metadata.get("date_filename"),
        )
        if prior_resolved:
            logger.info(
                f"Speaker Attribution: Prior ledger resolution applied for '{disc.cluster_id}' "
                f"({disc.current_speaker} -> {prior_resolved})"
            )
            return disc.current_speaker, prior_resolved

        src_id = str(metadata.get("source_id") or metadata.get("meeting_id") or metadata.get("title") or "pending")
        base_q = (
            f"HITL Decision: Speaker Attribution for {disc.current_speaker} [{disc.start_timestamp} - {disc.end_timestamp}]\n\n"
            f"**Candidate Cluster**: `{disc.cluster_id}`\n**Context**: Role cues conflict with the current speaker label."
        )
        if disc.is_ambiguous and not interactive_resolver:
            payload = build_hitl_review_payload(
                transcript, disc.current_speaker, base_q, disc.candidates,
                source_id=src_id, start_timestamp=disc.start_timestamp, end_timestamp=disc.end_timestamp,
            )
            raise HITLRequiredError(
                f"L1 ATTRIBUTION GATE BLOCKED: Ambiguous speaker attribution for '{disc.current_speaker}' "
                f"during {disc.start_timestamp}-{disc.end_timestamp} (role cue '{disc.detected_role}' "
                f"conflicts with roster; margin={disc.margin:.2f} <= {self.margin_threshold}).",
                unmapped_speakers=[disc.current_speaker],
                hitl_type="speaker_attribution",
                **payload,
            )

        if not disc.candidates:
            return None

        top_cand = disc.candidates[0]
        if disc.is_ambiguous or top_cand.attendee_name != disc.current_speaker:
            if interactive_resolver:
                q = build_speaker_review_question(
                    transcript, disc.current_speaker, base_q,
                    source_id=src_id, start_timestamp=disc.start_timestamp, end_timestamp=disc.end_timestamp,
                )
                ans = interactive_resolver(q, [c.to_option_string() for c in disc.candidates])
                chosen = self._parse_selected_name(ans, disc.candidates)
                self._record_in_ledger(disc.cluster_id, chosen, disc.candidates, metadata, autonomous=False)
                return disc.current_speaker, chosen
            elif autonomous_if_decisive:
                self._record_in_ledger(disc.cluster_id, top_cand.attendee_name, disc.candidates, metadata, autonomous=True)
                return disc.current_speaker, top_cand.attendee_name
        return None

    def _try_apply_prior_cluster_ledger(
        self, spk_id: str, cluster_label: str, metadata: Dict[str, Any],
        existing_map: Dict[str, str], assigned_attendees: Set[str], enforce_injective: bool,
    ) -> bool:
        prior_resolved = check_ledger_for_audio_resolution(
            cluster_label, self.store, project=metadata.get("project"),
            meeting_name=metadata.get("meeting_name") or metadata.get("title"),
            date=metadata.get("date") or metadata.get("date_filename"),
        )
        if not prior_resolved:
            return False
        if enforce_injective and prior_resolved in assigned_attendees:
            logger.warning(
                f"Speaker Attribution: Prior ledger resolution '{prior_resolved}' for '{cluster_label}' "
                f"violates injective mapping (already assigned). Ignoring stale ledger entry."
            )
            return False
        existing_map[spk_id] = prior_resolved
        existing_map[cluster_label] = prior_resolved
        return True

    def _resolve_single_generic_speaker(
        self, spk_id: str, resolved_transcript: str, roster: List[Any],
        metadata: Dict[str, Any], interactive_resolver: Optional[Callable[[str, List[str]], str]],
        autonomous_if_decisive: bool, existing_map: Dict[str, str], total_clusters: int = 1,
    ) -> None:
        spk_clean = re.sub(r"[_\s]+", " ", spk_id.lower().strip())
        is_unk = spk_clean in ("unknown", "speaker unknown", "unattributed")
        cluster_label = (
            spk_id
            if spk_id.lower().startswith("speaker") or is_unk
            else f"Speaker {spk_id}"
        )
        assigned_attendees = set(existing_map.values())
        cluster_clean = re.sub(r"[_\s]+", " ", cluster_label.lower().strip())
        enforce_injective = (total_clusters <= len(roster)) and not is_unk

        if self._try_apply_prior_cluster_ledger(spk_id, cluster_label, metadata, existing_map, assigned_attendees, enforce_injective):
            return

        candidates = self._score_candidates_for_speaker(
            spk_id, resolved_transcript, roster, metadata, assigned_attendees=assigned_attendees, total_clusters=total_clusters,
        )
        if not candidates:
            raise SpeakerAttributionCollisionError(
                f"L1 ATTRIBUTION GATE BLOCKED: No eligible roster candidates remaining for '{cluster_label}' "
                f"under injective mapping constraint ({len(assigned_attendees)} attendees already assigned).",
                unmapped_speakers=[cluster_label], hitl_type="speaker_attribution_collision",
            )

        top_cand, second_cand = candidates[0], (candidates[1] if len(candidates) > 1 else None)
        sec_conf = second_cand.confidence if second_cand else 0.0
        margin = round(top_cand.confidence - sec_conf, 4)
        ch0_failed = bool(metadata.get("ch0_subdiarization_failed") and len(roster) > 2 and str(spk_id).lower() in ("speaker 1", "1", "speaker_00", "0"))
        requires_op = needs_operator_decision(top_cand.confidence, sec_conf if second_cand else None, threshold=self.margin_threshold)
        is_decisive = (not requires_op and top_cand.confidence >= self.min_autonomous_confidence) and not ch0_failed
        src_id = str(metadata.get("source_id") or metadata.get("meeting_id") or metadata.get("title") or "pending")
        base_q = f"HITL Decision: Speaker Attribution for {cluster_label}\n\n**Candidate Cluster**: `{cluster_label}`\n**Context**: Assign this unresolved speaker cluster."

        if is_decisive and autonomous_if_decisive and not interactive_resolver:
            chosen_name, autonomous = top_cand.attendee_name, True
        elif interactive_resolver:
            q = build_speaker_review_question(resolved_transcript, cluster_label, base_q, source_id=src_id)
            chosen_name = self._parse_selected_name(interactive_resolver(q, [c.to_option_string() for c in candidates]), candidates)
            autonomous = False
        else:
            payload = build_hitl_review_payload(resolved_transcript, cluster_label, base_q, candidates, source_id=src_id)
            raise HITLRequiredError(
                f"L1 ATTRIBUTION GATE BLOCKED: Ambiguous speaker attribution for '{cluster_label}' "
                f"(top confidence={top_cand.confidence:.2f}, margin={margin:.2f} <= {self.margin_threshold}).",
                unmapped_speakers=[cluster_label], hitl_type="speaker_attribution", **payload,
            )

        existing_map[spk_id] = chosen_name
        existing_map[cluster_label] = chosen_name
        self._record_in_ledger(cluster_label, chosen_name, candidates, metadata, autonomous=autonomous)

    @staticmethod
    def _find_unmapped_generic_speakers(
        transcript: str,
        existing_map: Dict[str, str],
        include_unknown: bool = True,
    ) -> List[str]:
        """Identifies generic, placeholder, and 'Unknown' speakers that have not yet been attributed."""
        body = _isolate_body(transcript)
        excluded = {"anonymous"} if include_unknown else {
            "unknown", "unknown speaker", "speaker unknown", "unattributed", "anonymous"
        }
        detected_turns = {
            s for s in _TURN_SPEAKER_PATTERN.findall(body)
            if s.strip("[]").strip().lower() not in excluded
        }
        clean = {k.strip("[]").strip().lower(): str(v).strip() for k, v in existing_map.items()
                 if v and str(v).strip().lower() != k.strip("[]").strip().lower()
                 and not _GENERIC_SPEAKER_PATTERN.fullmatch(str(v).strip())
                 and str(v).strip().lower() not in ("unknown", "unknown speaker", "speaker unknown", "unattributed")}
        return [s for s in sorted(detected_turns) if (sk := s.strip("[]").strip().lower()) not in clean
                and f"speaker {sk}" not in clean and (s not in existing_map or not clean.get(sk))
                and (f"Speaker {s}" not in existing_map or not clean.get(f"speaker {sk}"))]

    @staticmethod
    def _normalize_cluster_key(k: str) -> str:
        s = str(k).strip()
        m_raw = re.match(r"^speaker_0*(\d+)$", s, re.IGNORECASE)
        if m_raw:
            return f"Speaker {int(m_raw.group(1)) + 1}"
        m_num = re.match(r"^(\d+)$", s)
        if m_num:
            return f"Speaker {m_num.group(1)}"
        m_spk = re.match(r"^speaker\s+0*(\d+)$", s, re.IGNORECASE)
        if m_spk:
            return f"Speaker {int(m_spk.group(1))}"
        m_alpha = re.match(r"^(?:voice|speaker)\s+([a-z])$", s, re.IGNORECASE)
        if m_alpha:
            return f"Speaker {ord(m_alpha.group(1).upper()) - ord('A') + 1}"
        return s

    @classmethod
    def _resolve_generic_assignment_key(cls, k: str, label_map: Dict[str, str]) -> str:
        if k in label_map and str(label_map[k]).startswith("Speaker "):
            return str(label_map[k]).strip()
        if k.upper().startswith("SPEAKER_") and label_map:
            return str(label_map.get(k.upper(), k)).strip()
        return cls._normalize_cluster_key(k)

    @classmethod
    def assert_injective_speaker_mapping(
        cls,
        speaker_map: Dict[str, str],
        roster_count: int,
        cluster_count: int,
        cluster_label_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """Enforces that no attendee is assigned to multiple speaker clusters when cluster count <= roster count."""
        generic_assignments: Dict[str, str] = {}
        label_map = cluster_label_map or {}
        for k, v in speaker_map.items():
            norm_k = cls._resolve_generic_assignment_key(k, label_map)
            if norm_k.lower().startswith("speaker "):
                if norm_k not in generic_assignments or generic_assignments[norm_k] != v:
                    generic_assignments[norm_k] = v
            elif k.lower().startswith("speaker ") or f"Speaker {k}" not in speaker_map:
                generic_assignments[k] = v

        effective_clusters = max(cluster_count, len(generic_assignments))
        if not (0 < effective_clusters <= roster_count):
            return
        seen: Dict[str, str] = {}
        for cluster, name in generic_assignments.items():
            if name in seen:
                raise SpeakerAttributionCollisionError(
                    f"L1 ATTRIBUTION GATE BLOCKED: Duplicate speaker attribution detected. "
                    f"Both '{seen[name]}' and '{cluster}' are mapped to '{name}'. "
                    f"Injective mapping violated for {effective_clusters} clusters and {roster_count} roster attendees.",
                    hitl_type="speaker_attribution_collision",
                )
            seen[name] = cluster

    def _sort_unmapped_by_margin(
        self, unmapped: List[str], transcript: str, roster: List[Any],
        metadata: Dict[str, Any], assigned: Set[str], total_clusters: int,
    ) -> List[str]:
        if len(unmapped) <= 1:
            return unmapped
        scored_order: List[Tuple[float, float, str]] = []
        for spk_id in unmapped:
            cands = self._score_candidates_for_speaker(
                spk_id, transcript, roster, metadata, assigned_attendees=assigned, total_clusters=total_clusters,
            )
            top = cands[0].confidence if cands else 0.0
            sec = cands[1].confidence if len(cands) > 1 else 0.0
            scored_order.append((round(top - sec, 4), top, spk_id))
        scored_order.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [x[2] for x in scored_order]

    def _prepare_attribution_inputs(
        self, transcript: str, metadata: Dict[str, Any]
    ) -> Tuple[str, Dict[str, str]]:
        from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass
        assert_no_u0_hitl_bypass(metadata)
        metadata.setdefault("pre_hint_raw_transcript", metadata.get("raw_transcript") or transcript)
        if "speaker_hints" not in metadata and (hints_loaded := load_speaker_hints(metadata)):
            metadata["speaker_hints"] = hints_loaded
        if "diarization_tracks" not in metadata and (tracks := load_diarization_tracks(metadata)):
            metadata["diarization_tracks"] = tracks
        hints = metadata.get("speaker_hints") or []
        turn_reassignments: Dict[str, str] = {}
        if hints:
            transcript, turn_reassignments = apply_quote_hints_to_turns(transcript, hints)
            if "raw_transcript" in metadata:
                metadata["raw_transcript"] = transcript
        return transcript, turn_reassignments

    def _run_undiarized_role_discrepancy_pass(
        self, resolved_transcript: str, transcript: str, metadata: Dict[str, Any],
        interactive_resolver: Optional[Callable[[str, List[str]], str]],
        autonomous_if_decisive: bool, turn_reassignments: Dict[str, str],
    ) -> None:
        is_undiarized = (
            metadata.get("source_type") == "AUDIO_ONLY_UNDIARIZED"
            or metadata.get("diarization_status") == "UNDIARIZED"
            or bool(metadata.get("verify_audio_attributions", False))
        )
        if not is_undiarized:
            return
        for disc in detect_audio_role_discrepancies(resolved_transcript, metadata):
            if resolved := self._resolve_single_discrepancy(disc, metadata, interactive_resolver, autonomous_if_decisive, transcript):
                turn_reassignments[resolved[0]] = resolved[1]

    def analyze_and_resolve(
        self, transcript: str, metadata: Dict[str, Any],
        interactive_resolver: Optional[Callable[[str, List[str]], str]] = None,
        autonomous_if_decisive: bool = True,
    ) -> AttributionResult:
        orig_transcript = transcript
        transcript, turn_reassignments = self._prepare_attribution_inputs(transcript, metadata)
        roster = self._extract_roster_candidates(metadata)
        existing_map = {k: v for k, v in (metadata.get("speaker_map") or {}).items() if v and not _GENERIC_SPEAKER_PATTERN.fullmatch(str(v).strip())}
        guard_collapse_or_evict_for_hitl(orig_transcript, roster, metadata, interactive_resolver, existing_map)
        reconcile_existing_map_conflicts(orig_transcript, transcript, existing_map, roster, metadata)

        generic_speakers = sorted(set(_TURN_SPEAKER_PATTERN.findall(_isolate_body(transcript))))
        unmapped = self._find_unmapped_generic_speakers(transcript, existing_map)
        total_clusters = max(len(generic_speakers), len(unmapped))
        resolved_transcript = self.apply_speaker_map(transcript, existing_map)
        self._run_undiarized_role_discrepancy_pass(
            resolved_transcript, transcript, metadata, interactive_resolver, autonomous_if_decisive, turn_reassignments
        )

        if not roster and (unmapped or metadata.get("diarization_status") == "UNDIARIZED"):
            raise HITLRequiredError("Speaker attribution requires an attendee roster.", hitl_type="roster_gate", missing_fields=["roster"])

        for spk_id in self._sort_unmapped_by_margin(unmapped, transcript, roster, metadata, set(existing_map.values()), total_clusters):
            self._resolve_single_generic_speaker(
                spk_id, transcript, roster, metadata, interactive_resolver, autonomous_if_decisive, existing_map, total_clusters=total_clusters
            )

        if roster:
            self.assert_injective_speaker_mapping(
                existing_map, len(roster), total_clusters, cluster_label_map=metadata.get("cluster_label_map")
            )
        final_transcript = self.apply_speaker_map(resolved_transcript, existing_map) if unmapped else resolved_transcript
        final_transcript = run_iterative_conflict_discrepancies(
            self, final_transcript, orig_transcript, roster, metadata,
            interactive_resolver, autonomous_if_decisive, turn_reassignments,
        )
        if interactive_resolver and metadata.get("collapse_evicted_pending_receipt"):
            from .conflict_detector import _sign_interactive_collapse_receipt
            _sign_interactive_collapse_receipt(metadata)
            metadata.pop("collapse_evicted_pending_receipt", None)
        self._maybe_auto_enroll_voiceprints(metadata, existing_map)
        self._maybe_auto_learn_speaker_memory(final_transcript, existing_map, metadata)
        return AttributionResult(speaker_map=existing_map, resolved_transcript=final_transcript, unmapped_speakers=[], turn_reassignments=turn_reassignments)

    def _maybe_auto_learn_speaker_memory(
        self, final_transcript: str, speaker_map: Dict[str, str], metadata: Dict[str, Any]
    ) -> int:
        if not metadata.get("hitl_verified"):
            return 0
        if metadata.get("diarization_collapsed") or metadata.get("diarization_status") in ("UNDIARIZED_QUARANTINED", "HITL_REQUIRED"):
            return 0
        try:
            from dual_pipeline.speaker_memory import SpeakerMemory, get_speaker_memory
            mem = metadata.get("speaker_memory") or (
                SpeakerMemory(memory_path=metadata["speaker_memory_path"])
                if metadata.get("speaker_memory_path")
                else get_speaker_memory()
            )
            return mem.learn_from_resolved_transcript(final_transcript, speaker_map=speaker_map, persist=True)
        except Exception as exc:
            logger.debug("Auto speaker memory learning skipped: %s", exc)
            return 0

    def apply_speaker_map(self, text: str, speaker_map: Dict[str, str]) -> str:
        from dual_pipeline.transcript_stages_core.l1_attribution import apply_speaker_map as _asm
        return _asm(text, speaker_map)

    def _extract_roster_candidates(self, metadata: Dict[str, Any]) -> List[Tuple[str, str]]:
        return [(r["name"], r["org"]) for r in extract_roster_with_roles(metadata)]

    def _score_candidates_for_speaker(
        self, spk_id: str, transcript: str, roster: List[Tuple[str, str]],
        metadata: Dict[str, Any], assigned_attendees: Optional[Set[str]] = None, total_clusters: int = 1,
    ) -> List[CandidateAttribution]:
        return score_candidates_for_speaker(
            spk_id=spk_id, transcript=transcript, roster=roster, metadata=metadata,
            assigned_attendees=assigned_attendees, total_clusters=total_clusters,
        )

    _extract_speaker_turns = staticmethod(extract_speaker_turns)

    def _record_in_ledger(
        self, cluster_label: str, chosen_name: str,
        candidates: List[CandidateAttribution], metadata: Dict[str, Any], autonomous: bool,
    ) -> None:
        try:
            cand = next((c for c in candidates if c.attendee_name == chosen_name), None)
            did = f"speaker_attribution_{cluster_label.lower().replace(' ', '_')}_{metadata.get('date_filename', 'date')}"
            meta = {"cluster": cluster_label, "project": metadata.get("project"), "meeting_name": metadata.get("meeting_name"), "autonomous": autonomous, "confidence": cand.confidence if cand else 0.0}
            self.store.record_decision(
                decision_id=did, category="speaker_attribution", prompt=f"Attribute {cluster_label} to attendee",
                options=[c.attendee_name for c in candidates], selected_option=chosen_name,
                rationale=cand.evidence_rationale if cand else None, metadata=meta,
            )
        except Exception as e:
            logger.warning(f"Could not record speaker attribution decision in ledger: {e}")

    @staticmethod
    def _parse_selected_name(selected_str: str, candidates: List[CandidateAttribution]) -> str:
        for c in candidates:
            if c.attendee_name in selected_str:
                return c.attendee_name
        return selected_str.strip() if selected_str else (candidates[0].attendee_name if candidates else "")

    def _maybe_auto_enroll_voiceprints(self, metadata: Dict[str, Any], speaker_map: Dict[str, str]) -> List[Dict[str, Any]]:
        """Auto-enrolls verified speaker clusters into VoiceprintStore after resolution."""
        from .voiceprint_auto_enrollment import auto_enroll_voiceprints
        return auto_enroll_voiceprints(
            metadata=metadata, speaker_map=speaker_map,
            voiceprint_store=self.voiceprint_store, normalize_key_fn=self._normalize_cluster_key,
        )


AttributionEngine = AudioSpeakerAttributionEngine
