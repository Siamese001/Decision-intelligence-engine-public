"""
dual_pipeline/live_meeting_recorder_core/speaker_hints.py

Operator Speaker Hint Manager and Cryptographic Ambiguity Resolution.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.speaker_hints")


@dataclass
class SpeakerHintRecord:
    hint_id: str
    quote: str
    speaker_name: str
    voice_id: str
    matched_chunk_indices: List[int]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    operator_id: str = "human_operator"
    signature: Optional[str] = None
    ledger_entry_digest: Optional[str] = None
    status: str = "APPLIED"  # "APPLIED", "CONFLICT", "REJECTED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpeakerHintRecord":
        return cls(**data)


@dataclass
class HintResult:
    status: str  # "PASS" | "FAIL"
    error: Optional[str] = None  # None | "QUOTE_NOT_FOUND" | "AMBIGUOUS_QUOTE_MATCH" | "NON_ROSTER_SPEAKER"
    voice_id: Optional[str] = None
    speaker_name: Optional[str] = None
    quote: Optional[str] = None
    updated_segments_count: int = 0
    ledger_digest: Optional[str] = None
    details: Optional[str] = None
    conflicts_logged: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


from .speaker_hint_matcher import normalize_search_text


class SpeakerHintManager:
    """Manages operator speaker hints, quote matching, retroactive updates, and cryptographic verification."""

    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        cache_dir: Optional[Union[str, Path]] = None,
        attendees: Optional[List[Any]] = None,
        ledger_path: Optional[Union[str, Path]] = None,
        voice_registry: Optional[Any] = None,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else None
        self.cache_dir = Path(cache_dir) if cache_dir else (self.output_dir if self.output_dir else None)
        self.attendees = attendees or []
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.voice_registry = voice_registry
        self.hints: List[SpeakerHintRecord] = []

    def _get_roster_names(self) -> Set[str]:
        names: Set[str] = set()
        for a in self.attendees:
            if isinstance(a, dict):
                n = a.get("name") or a.get("speaker") or ""
                if n:
                    names.add(n.strip().lower())
            elif isinstance(a, str):
                names.add(a.strip().lower())
        return names

    def resolve_roster_name(self, candidate_name: str) -> str:
        """Expands partial or first name against roster attendees, or returns candidate_name."""
        from dual_pipeline.live_meeting_recorder_core.attendees import resolve_roster_name
        return resolve_roster_name(candidate_name, self.attendees)

    def validate_roster_speaker(self, candidate_name: str) -> bool:
        """Returns True if candidate name matches an attendee by full name or first name."""
        clean = candidate_name.strip().lower()
        if not clean or not self.attendees:
            return True
        if self.resolve_roster_name(candidate_name).lower() != clean:
            return True
        roster = self._get_roster_names()
        return clean in roster or any((p := n.split()) and p[0] == clean or clean in n for n in roster)

    @staticmethod
    def _segment_contains_quote(s: Dict[str, Any], clean_quote: str) -> bool:
        from .speaker_hint_matcher import segment_contains_quote
        return segment_contains_quote(s, clean_quote)

    @staticmethod
    def _token_overlap_match(
        segments: List[Dict[str, Any]],
        clean_quote: str,
        min_ratio: float = 0.65,
        elapsed_seconds: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        from .speaker_hint_matcher import token_overlap_match
        return token_overlap_match(segments, clean_quote, min_ratio, elapsed_seconds=elapsed_seconds)

    def match_quote_to_segments(
        self, quote: str, segments: List[Dict[str, Any]], elapsed_seconds: Optional[float] = None
    ) -> Tuple[str, List[Dict[str, Any]], Set[str]]:
        """Matches a quote against transcribed segments; returns (status, matched_segments, distinct_voices)."""
        clean_quote = normalize_search_text(quote)
        if not clean_quote:
            return "QUOTE_NOT_FOUND", [], set()
        matched = [s for s in segments if self._segment_contains_quote(s, clean_quote)] or self._token_overlap_match(
            segments, clean_quote, elapsed_seconds=elapsed_seconds
        )
        if not matched:
            return "QUOTE_NOT_FOUND", [], set()
        if len(matched) > 1 and elapsed_seconds is not None:
            near = [s for s in matched if abs(float(s.get("start_sec", s.get("seconds", 0.0))) - elapsed_seconds) <= 90.0]
            if near:
                matched = [min(near, key=lambda s: abs(float(s.get("start_sec", s.get("seconds", 0.0))) - elapsed_seconds))]
        voices = {m.get("voice_id") or m.get("speaker") or "Voice A" for m in matched}
        if len(voices) > 1 and elapsed_seconds is not None:
            closest = min(matched, key=lambda s: abs(float(s.get("start_sec", s.get("seconds", 0.0))) - elapsed_seconds))
            vid = closest.get("voice_id") or closest.get("speaker") or "Voice A"
            return "MATCHED", [s for s in matched if (s.get("voice_id") or s.get("speaker") or "Voice A") == vid], {vid}
        return ("AMBIGUOUS_QUOTE_MATCH" if len(voices) > 1 else "MATCHED"), matched, voices

    def _read_segments_safely(self, target_segments_file: Path) -> List[Dict[str, Any]]:
        """Reads and parses non-empty valid JSON lines from target segments file."""
        segments: List[Dict[str, Any]] = []
        for line in target_segments_file.read_text(encoding="utf-8").strip().splitlines():
            if line_str := line.strip():
                try:
                    if isinstance(seg_obj := json.loads(line_str), dict):
                        segments.append(seg_obj)
                except Exception:
                    continue
        return segments

    def _log_conflicts_if_any(
        self, target_voice_id: str, speaker_name: str, quote: str, segments: List[Dict[str, Any]], target_segments_file: Path,
    ) -> int:
        """Detects and logs attribution disagreement to speaker_hint_conflicts.jsonl."""
        conflicts_path = (self.output_dir or target_segments_file.parent) / "speaker_hint_conflicts.jsonl"
        for s in segments:
            if s.get("voice_id") == target_voice_id and (prior_spk := s.get("speaker")) and prior_spk not in ("Speaker", target_voice_id, speaker_name):
                rec = {
                    "timestamp": datetime.now(timezone.utc).isoformat(), "voice_id": target_voice_id, "quote": quote,
                    "prior_speaker": prior_spk, "hinted_speaker": speaker_name, "chunk_idx": s.get("chunk_idx"), "resolution": "OPERATOR_HINT_WINS",
                }
                with open(conflicts_path, "a", encoding="utf-8") as f_conf:
                    f_conf.write(json.dumps(rec) + "\n")
                return 1
        return 0

    @staticmethod
    def _is_shared_or_collapsed_cluster(segments: List[Dict[str, Any]], target_voice_id: str, speaker_name: str) -> bool:
        if target_voice_id in ("Voice Host", "Host", "Speaker", "Unknown"):
            return True
        cluster_segs = [s for s in segments if (s.get("voice_id") or s.get("speaker")) == target_voice_id or s.get("speaker") == target_voice_id]
        if any(s.get("hint_applied") and s.get("speaker") not in (None, "", "Speaker", "Unknown", target_voice_id, speaker_name) for s in cluster_segs):
            return True
        return len(segments) >= 6 and (len(cluster_segs) / len(segments)) >= 0.80

    @staticmethod
    def _apply_hint_to_segment(
        s: Dict[str, Any], idx: int, target_voice_id: str, speaker_name: str,
        quote: str, is_targeted: bool, is_generic_host: bool, matched_set: set,
    ) -> bool:
        v_id = s.get("voice_id") or s.get("speaker")
        if v_id != target_voice_id and s.get("speaker") != target_voice_id:
            return False
        if is_targeted and matched_set and int(s.get("chunk_idx", idx)) not in matched_set:
            return False
        if is_targeted and not is_generic_host and speaker_name:
            s["voice_id"] = f"Voice {speaker_name.split()[0]}"
        elif not is_generic_host:
            s["voice_id"] = target_voice_id
        s.update({"speaker": speaker_name, "hint_applied": True, "hint_quote": quote, "speaker_override": True})
        return True

    def _update_segments_and_md(
        self, target_voice_id: str, speaker_name: str, quote: str, segments: List[Dict[str, Any]],
        target_segments_file: Path, target_md_file: Optional[Path], matched_indices: Optional[List[int]] = None,
    ) -> int:
        """Updates segments.jsonl and transcript.md for that voice cluster (or only matched chunks when shared/collapsed)."""
        updated_count, new_lines = 0, []
        is_generic_host = target_voice_id in ("Voice Host", "Host", "Speaker", "Unknown")
        is_targeted = self._is_shared_or_collapsed_cluster(segments, target_voice_id, speaker_name)
        matched_set = set(matched_indices or [])
        for idx, s in enumerate(segments):
            if self._apply_hint_to_segment(s, idx, target_voice_id, speaker_name, quote, is_targeted, is_generic_host, matched_set):
                updated_count += 1
            new_lines.append(json.dumps(s))

        tmp_seg = target_segments_file.with_suffix(".jsonl.tmp")
        tmp_seg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        tmp_seg.replace(target_segments_file)
        if target_md_file and target_md_file.is_file():
            from .speaker_hint_matcher import relabel_markdown_for_hint
            md_text = target_md_file.read_text(encoding="utf-8")
            tmp_md = target_md_file.with_suffix(".md.tmp")
            tmp_md.write_text(relabel_markdown_for_hint(md_text, target_voice_id, speaker_name, quote, is_targeted), encoding="utf-8")
            tmp_md.replace(target_md_file)
        return updated_count

    def _record_signed_hint_approval(
        self,
        target_voice_id: str,
        speaker_name: str,
        quote: str,
        matched_indices: List[int],
        operator_id: str,
        meeting_slug: str,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Constructs and signs Ed25519 approval record and appends to hitl_decision_ledger.json."""
        from dual_pipeline.core import digest
        from dual_pipeline.hitl_governance_core.crypto import (
            append_signed_approval_to_ledger,
            create_signed_hitl_approval_entry,
        )

        subject_data = {
            "hint_type": "speaker_hint",
            "quote": quote,
            "speaker": speaker_name,
            "voice_id": target_voice_id,
            "meeting_slug": meeting_slug,
            "matched_chunks": matched_indices,
        }
        subj_sha = digest(subject_data)
        signed_entry = create_signed_hitl_approval_entry(
            subject_sha256=subj_sha,
            case_ids=[f"speaker_hint:{meeting_slug}:{target_voice_id}"],
            decision="APPROVE",
            operator_id=operator_id,
            meeting_slug=meeting_slug,
            metadata={
                "quote": quote,
                "speaker": speaker_name,
                "voice_id": target_voice_id,
                "operator_verified": True,
            },
        )
        try:
            append_signed_approval_to_ledger(signed_entry, ledger_path=self.ledger_path)
            ledger_digest = signed_entry.get("entry_digest")
        except Exception as exc:
            logger.warning("Failed to record signed entry in hitl_decision_ledger.json: %s", exc)
            ledger_digest = None
        return signed_entry, ledger_digest

    def apply_hint(
        self,
        quote: str,
        speaker_name: str,
        segments_jsonl_path: Optional[Union[str, Path]] = None,
        transcript_md_path: Optional[Union[str, Path]] = None,
        operator_id: str = "operator",
        force: bool = False,
        meeting_slug: str = "meeting",
        elapsed_seconds: Optional[float] = None,
    ) -> HintResult:
        """Executes complete operator hint pipeline with fail-closed validation."""
        speaker_name = self.resolve_roster_name(speaker_name)
        target_segments_file = Path(segments_jsonl_path) if segments_jsonl_path else (self.cache_dir / "segments.jsonl" if self.cache_dir else None)
        target_md_file = Path(transcript_md_path) if transcript_md_path else (self.output_dir / "transcript.md" if self.output_dir else None)

        if not target_segments_file or not target_segments_file.is_file():
            return HintResult(status="FAIL", error="SEGMENTS_FILE_NOT_FOUND", details=f"Target segments file not found: {target_segments_file}")

        if not force and not self.validate_roster_speaker(speaker_name):
            return HintResult(status="FAIL", error="NON_ROSTER_SPEAKER", quote=quote, speaker_name=speaker_name, details=f"Speaker '{speaker_name}' is not in the meeting roster. Pass force=True to override.")

        segments = self._read_segments_safely(target_segments_file)
        if not segments:
            return HintResult(status="FAIL", error="SEGMENTS_EMPTY_OR_CORRUPT", details=f"No valid JSON segment records could be parsed from {target_segments_file}")

        match_status, matched_segs, distinct_voices = self.match_quote_to_segments(quote, segments, elapsed_seconds=elapsed_seconds)
        if match_status == "QUOTE_NOT_FOUND":
            return HintResult(status="FAIL", error="QUOTE_NOT_FOUND", quote=quote, speaker_name=speaker_name, details=f"Quote '{quote}' was not found in any transcribed segment.")
        if match_status == "AMBIGUOUS_QUOTE_MATCH":
            return HintResult(status="FAIL", error="AMBIGUOUS_QUOTE_MATCH", quote=quote, speaker_name=speaker_name, details=f"Quote matches segments attributed to multiple distinct voices: {sorted(distinct_voices)}.")

        target_voice_id = list(distinct_voices)[0]
        if self.voice_registry and hasattr(self.voice_registry, "bind_speaker_hint"):
            self.voice_registry.bind_speaker_hint(voice_id=target_voice_id, speaker_name=speaker_name, quote=quote)
        matched_indices = [int(s.get("chunk_idx", 0)) for s in matched_segs]

        conflicts_logged = self._log_conflicts_if_any(target_voice_id, speaker_name, quote, segments, target_segments_file)
        updated_count = self._update_segments_and_md(
            target_voice_id, speaker_name, quote, segments, target_segments_file, target_md_file, matched_indices=matched_indices,
        )
        signed_entry, ledger_digest = self._record_signed_hint_approval(
            target_voice_id, speaker_name, quote, matched_indices, operator_id, meeting_slug
        )

        hint_record = SpeakerHintRecord(
            hint_id=f"hint_{target_voice_id}_{len(self.hints)+1}",
            quote=quote,
            speaker_name=speaker_name,
            voice_id=target_voice_id,
            matched_chunk_indices=matched_indices,
            operator_id=operator_id,
            signature=signed_entry.get("signature") or signed_entry.get("payload", {}).get("signature"),
            ledger_entry_digest=ledger_digest,
            status="APPLIED",
        )
        self.hints.append(hint_record)

        base_dir = self.output_dir or target_segments_file.parent
        clean_slug = meeting_slug.replace(" ", "_").lower()
        self.export_colocated_speaker_map(output_dir=base_dir, meeting_slug=clean_slug)

        return HintResult(
            status="PASS",
            voice_id=target_voice_id,
            speaker_name=speaker_name,
            quote=quote,
            updated_segments_count=updated_count,
            ledger_digest=ledger_digest,
            conflicts_logged=conflicts_logged,
            details=f"Hint applied: bound {target_voice_id} to '{speaker_name}', updated {updated_count} segment(s).",
        )

    def export_colocated_speaker_map(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        meeting_slug: str = "meeting",
    ) -> Optional[Path]:
        """
        Exports {clean_slug}_speaker_map.json and speaker_hints.json to the target directory.
        Used for Stage U0 handoff so downstream batch transcription automatically maps clusters.
        """
        base_dir = Path(output_dir) if output_dir else self.output_dir
        if not base_dir:
            return None
        base_dir.mkdir(parents=True, exist_ok=True)

        target_dirs = [base_dir]
        if self.cache_dir and self.cache_dir.resolve() != base_dir.resolve():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            target_dirs.append(self.cache_dir)

        clean_slug = meeting_slug.replace(" ", "_").lower()
        map_file = base_dir / f"{clean_slug}_speaker_map.json"
        cur_map: Dict[str, str] = {}
        if map_file.is_file():
            try:
                cur_map = json.loads(map_file.read_text(encoding="utf-8"))
            except Exception:
                cur_map = {}
        for h in self.hints:
            if h.voice_id and h.speaker_name:
                cur_map[h.voice_id] = h.speaker_name

        hints_payload = json.dumps([h.to_dict() for h in self.hints], indent=2)
        map_payload = json.dumps(cur_map, indent=2)
        for target in target_dirs:
            (target / "speaker_hints.json").write_text(hints_payload, encoding="utf-8")
            (target / f"{clean_slug}_speaker_map.json").write_text(map_payload, encoding="utf-8")
        return map_file
