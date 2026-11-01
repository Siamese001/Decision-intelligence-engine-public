"""BaseAudioTranscriber class and shared diarization formatting utilities."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from ..core import load_ssot_env
from .ffmpeg import clean_speaker_label

load_ssot_env()

logger = logging.getLogger(__name__)


def _load_segments_from_paths(meta: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    for p_key in ("structured_utterances_path", "structured_segments_path"):
        p_val = meta.get(p_key)
        if p_val and os.path.isfile(str(p_val)):
            try:
                loaded = json.loads(Path(str(p_val)).read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    return loaded
            except Exception as exc:
                logger.debug("Failed to load segments from %s: %s", p_val, exc)
    return None


def _clamp_segment_overlaps(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for idx in range(len(segments) - 1):
        next_start = float(segments[idx + 1]["start"])
        if next_start < float(segments[idx]["end"]):
            segments[idx]["end"] = max(float(segments[idx]["start"]) + 0.1, next_start)
    return segments


def _parse_raw_segments(raw_text: str, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    if meta.get("structured_utterances"):
        return meta["structured_utterances"]
    loaded_from_path = _load_segments_from_paths(meta)
    if loaded_from_path:
        return loaded_from_path
    if meta.get("structured_segments"):
        return meta["structured_segments"]
    segments = []
    for line in raw_text.split("\n\n"):
        line_str = line.strip()
        if not line_str:
            continue
        m = re.match(r"\*\*([^*]+)\*\*\s+\[(\d+:\d+(?::\d+)?)\]:\s*(.*)", line_str)
        if m:
            spk, ts_str, txt = m.groups()
            parts = [float(p) for p in ts_str.split(":")]
            sec = parts[0]*3600 + parts[1]*60 + parts[2] if len(parts)==3 else parts[0]*60 + parts[1]
            segments.append({"speaker": spk, "start": sec, "end": sec + 5.0, "text": txt})
    return _clamp_segment_overlaps(segments)


def _cosine_similarity_pairs(
    curr_centroids: Dict[str, List[float]], prev_centroids: Dict[str, List[float]]
) -> List[tuple[float, str, str]]:
    import math
    pairs: List[tuple[float, str, str]] = []
    for c_id, c_vec in curr_centroids.items():
        cn = math.sqrt(sum(float(x) * float(x) for x in c_vec))
        if cn < 1e-9:
            continue
        for p_id, p_vec in prev_centroids.items():
            pn = math.sqrt(sum(float(y) * float(y) for y in p_vec)) if len(p_vec) == len(c_vec) else 0.0
            if pn > 1e-9:
                pairs.append((sum(float(a) * float(b) for a, b in zip(c_vec, p_vec)) / (cn * pn), c_id, p_id))
    return sorted(pairs, key=lambda item: item[0], reverse=True)


def stitch_cross_chunk_clusters(
    prev_centroids: Dict[str, List[float]],
    curr_centroids: Dict[str, List[float]],
    curr_segments: List[Dict[str, Any]],
    similarity_threshold: float = 0.78,
) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Bipartite cosine-similarity stitching across audio chunks so SPEAKER_XX labels do not permute."""
    if not prev_centroids or not curr_centroids:
        return [dict(s) for s in curr_segments], {}
    assigned_curr: set[str] = set()
    assigned_prev: set[str] = set()
    relabel_map: Dict[str, str] = {}
    for sim, c_id, p_id in _cosine_similarity_pairs(curr_centroids, prev_centroids):
        if sim >= similarity_threshold and c_id not in assigned_curr and p_id not in assigned_prev:
            relabel_map[c_id] = p_id
            assigned_curr.add(c_id)
            assigned_prev.add(p_id)
    stitched: List[Dict[str, Any]] = []
    for seg in curr_segments:
        s_copy = dict(seg)
        spk = str(s_copy.get("speaker") or "")
        if spk in relabel_map:
            s_copy["raw_chunk_speaker"] = spk
            s_copy["speaker"] = relabel_map[spk]
        stitched.append(s_copy)
    return stitched, relabel_map


def _persist_signal_envelope_atomic(json_cache: Path, envelope: ForensicSignalEnvelope) -> None:
    try:
        tmp_cache = json_cache.with_name(f".{json_cache.name}.tmp")
        tmp_cache.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp_cache, json_cache)
        logger.info("Saved ForensicSignalEnvelope to %s", json_cache)
    except Exception as exc:
        logger.warning("Failed to save signal envelope cache: %s", exc)


class BaseAudioTranscriber:
    """Base interface for audio transcribers with shared formatting and validation."""

    def __init__(self, mock: bool = False):
        self.mock = mock

    def transcribe(self, audio_file_path: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

        if self.mock:
            raise RuntimeError("MOCK_ASR_PROHIBITED: Mock audio transcription is prohibited; authentic ASR execution is required.")

        return self._live_transcribe(audio_file_path, metadata=metadata)

    def transcribe_signal(self, audio_file_path: str, metadata: Optional[Dict[str, Any]] = None) -> ForensicSignalEnvelope:
        """
        Extracts high-fidelity audio evidence packaged as a ForensicSignalEnvelope.
        Caches structured envelope in output/transcript_cache/<safe_stem>.signal_envelope.json.
        """
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

        if self.mock:
            raise RuntimeError("MOCK_ASR_PROHIBITED: Mock audio transcription is prohibited; authentic ASR execution is required.")

        meta = metadata or {}
        safe_stem = re.sub(r'[<>:"/\\|?*]', '_', Path(audio_file_path).stem)
        audio_p = Path(audio_file_path).expanduser().resolve()
        cache_dir = Path(__file__).resolve().parents[2] / "output" / "transcript_cache"
        if meta.get("transcript_cache_dir"):
            cache_dir = Path(meta["transcript_cache_dir"])
        elif "transcript_cache" in audio_p.parts:
            cache_dir = audio_p.parent
            if metadata is not None:
                metadata["transcript_cache_dir"] = str(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        json_cache = cache_dir / f"{safe_stem}.signal_envelope.json"

        # Calculate source audio hash
        hasher = hashlib.sha256()
        with open(audio_file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        source_hash = f"sha256:{hasher.hexdigest()}"

        if not meta.get("no_cache", False) and json_cache.exists():
            try:
                data = json.loads(json_cache.read_text(encoding="utf-8"))
                envelope = ForensicSignalEnvelope.model_validate(data)
                logger.info("Loaded ForensicSignalEnvelope from cache: %s", json_cache)
                return envelope
            except Exception as e:
                logger.warning("Failed to parse cached signal envelope: %s", e)

        # Obtain transcript raw text or segments
        raw_text = self._live_transcribe(audio_file_path, metadata=meta)
        segments = _parse_raw_segments(raw_text, meta)

        sample_rate = 16000
        turns = []
        for i, s in enumerate(segments):
            start_sec = float(s.get("start", s.get("start_time_sec", 0.0)))
            end_sec = float(s.get("end", s.get("end_time_sec", start_sec + 3.0)))
            start_samp = int(start_sec * sample_rate)
            end_samp = max(start_samp + 160, int(end_sec * sample_rate))
            span = SampleSpan(start_sample=start_samp, end_sample=end_samp)

            spk_label = clean_speaker_label(s.get("speaker", "Speaker 1"))
            turn = ForensicTurnSignal(
                turn_id=f"turn-{i+1:03d}",
                span=span,
                transcript_verbatim=s.get("text", "").strip(),
                speaker_cluster_ids=(spk_label,),
                identity_state="cluster_only",
                commitment_state="committed",
            )
            turns.append(turn)

        total_samples = turns[-1].span.end_sample if turns else 16000
        digest = ForensicSignalEnvelope.compute_signal_digest(
            recording_id=safe_stem,
            source_hashes=(source_hash,),
            turns=tuple(turns),
        )

        envelope = ForensicSignalEnvelope(
            schema_version="2.0.0",
            recording_id=safe_stem,
            source_hashes=(source_hash,),
            canonical_sample_rate_hz=sample_rate,
            timeline_length_samples=total_samples,
            turns=tuple(turns),
            signal_digest=digest,
        )
        _persist_signal_envelope_atomic(json_cache, envelope)
        return envelope

    def _live_transcribe(self, audio_file_path: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        raise NotImplementedError

    @staticmethod
    def _resolve_segment_speaker_label(
        seg: Dict[str, Any], speaker_map: Dict[str, str], speaker_counter: int
    ) -> tuple[str, int]:
        raw_speaker = str(seg.get("speaker", "Unknown"))
        m = re.match(r"^speaker[_\-\s]*(\d+)$", raw_speaker, re.IGNORECASE)
        if m:
            raw_key = m.group(0).upper()
            if raw_key not in speaker_map:
                speaker_map[raw_key] = f"Speaker {speaker_counter}"
                speaker_counter += 1
            return speaker_map[raw_key], speaker_counter
        if raw_speaker.strip().lower() in ("unknown", "speaker unknown"):
            return "Unknown", speaker_counter
        if (
            not raw_speaker.lower().startswith("speaker")
            and seg.get("timing_provenance") != "platform_per_channel"
            and re.match(r"^(?:\d+|[A-Za-z0-9]{1,2}|cluster_\w+)$", raw_speaker.strip())
        ):
            return f"Speaker {raw_speaker}", speaker_counter
        return raw_speaker, speaker_counter

    def _format_diarized_json(
        self,
        response_data: Union[Dict[str, Any], List[Dict[str, Any]]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        segments = response_data.get("segments", []) if isinstance(response_data, dict) else response_data
        transcript_lines = []
        speaker_map: Dict[str, str] = {}
        speaker_counter = 1

        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue
            speaker_label, speaker_counter = self._resolve_segment_speaker_label(seg, speaker_map, speaker_counter)
            start = float(seg.get("start", seg.get("start_time_sec", 0.0)))
            m_val, s = divmod(start, 60)
            h, m_val = divmod(m_val, 60)
            timestamp = f"{int(h):02d}:{int(m_val):02d}:{int(s):02d}"
            transcript_lines.append(f"**{speaker_label}** [{timestamp}]: {text}")

        if metadata is not None:
            metadata["acoustic_speaker_map"] = dict(speaker_map)
            metadata["cluster_label_map"] = dict(speaker_map)
            existing_sm = metadata.get("speaker_map")
            if isinstance(existing_sm, dict) and existing_sm:
                for raw_k, spk_n in speaker_map.items():
                    mapped_person = existing_sm.get(raw_k)
                    if mapped_person and not re.match(r"^speaker[_\-\s]*\d+$", str(mapped_person), re.IGNORECASE):
                        existing_sm.setdefault(spk_n, mapped_person)

        return "\n\n".join(transcript_lines)

    def _build_structured_turns(self, response_data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Preserves immutable structured speaker turns with exact bounds, overlap, and words."""
        segments = response_data.get("segments", []) if isinstance(response_data, dict) else response_data
        structured: List[Dict[str, Any]] = []
        for s in segments:
            txt = s.get("text", "").strip()
            if not txt:
                continue
            st = float(s.get("start", s.get("start_time_sec", 0.0)))
            en = float(s.get("end", s.get("end_time_sec", st)))
            dur = round(max(0.0, en - st), 2)
            structured.append({
                "start": round(st, 2),
                "end": round(en, 2),
                "duration": dur,
                "speaker": s.get("speaker", "Unknown"),
                "raw_speaker": s.get("raw_speaker") or s.get("speaker", "Unknown"),
                "text": txt,
                "words": s.get("words", []),
                "overlap": bool(s.get("overlap", False)),
                "active_speakers": s.get("active_speakers", [s.get("speaker", "Unknown")]),
            })
        return structured

    def _build_structured_utterances(self, response_data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Assembles canonical complete-thought speaker utterances conforming to schema."""
        from .utterance_assembler import assemble_utterances
        return assemble_utterances(response_data)

    @staticmethod
    def build_cluster_appearance_map(segments: List[Dict[str, Any]]) -> Dict[str, str]:
        """Builds bidirectional appearance-order mapping between raw SPEAKER_XX labels and Speaker N."""
        speaker_map: Dict[str, str] = {}
        speaker_counter = 1
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            raw_speaker = str(seg.get("raw_speaker") or seg.get("speaker") or "Unknown")
            m = re.match(r"^speaker[_\-\s]*(\d+)$", raw_speaker, re.IGNORECASE)
            if m and raw_speaker.upper().startswith("SPEAKER_"):
                raw_key = raw_speaker.upper()
                if raw_key not in speaker_map:
                    speaker_map[raw_key] = f"Speaker {speaker_counter}"
                    speaker_counter += 1
        return speaker_map

    def _format_segments(self, segments: List[Dict[str, Any]]) -> str:
        return self._format_diarized_json({"segments": segments})

    def _mock_transcribe(self, audio_file_path: str) -> str:
        raise RuntimeError(f"MOCK_ASR_PROHIBITED: Mock audio transcription is prohibited for {os.path.basename(audio_file_path)}; authentic ASR execution is required.")
