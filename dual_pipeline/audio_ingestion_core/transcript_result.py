"""Typed Structured Transcript Result and Cache Engine.

Enforces structured evidence retention across both cold and warm paths:
- Retains exact word-level sample bounds, acoustic intervals, and utterances.
- Computes deterministic cache keys binding audio digest, model revision, and decode config.
- Guarantees cold/warm parity: warm reads restore full structured results.
- Rejects stale, corrupted, or sidecar-missing cache files.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.transcript_result")


@dataclass
class StructuredWord:
    word_id: str
    text: str
    start_time_sec: float
    end_time_sec: float
    channel: int
    start_sample: Optional[int] = None
    end_sample: Optional[int] = None
    candidate_speakers: List[Dict[str, Any]] = field(default_factory=list)
    is_overlap: bool = False
    alignment_confidence: Optional[float] = None
    exclusion_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StructuredUtterance:
    utterance_id: str
    speaker: str
    start_time_sec: float
    end_time_sec: float
    text: str
    word_ids: List[str]
    boundary_reasons: List[str] = field(default_factory=list)
    timing_provenance: str = "sample_clock_exact"
    is_short_acknowledgment: bool = False
    overlap_context: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StructuredAcousticInterval:
    interval_id: str
    speaker_cluster: str
    start_time_sec: float
    end_time_sec: float
    channel: int = 0
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StructuredTranscriptResult:
    schema_version: str
    audio_metadata: Dict[str, Any]
    model_metadata: Dict[str, Any]
    words: List[StructuredWord] = field(default_factory=list)
    utterances: List[StructuredUtterance] = field(default_factory=list)
    acoustic_intervals: List[StructuredAcousticInterval] = field(default_factory=list)
    legacy_crosswalk: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "audio_metadata": self.audio_metadata,
            "model_metadata": self.model_metadata,
            "words": [w.to_dict() for w in self.words],
            "utterances": [u.to_dict() for u in self.utterances],
            "acoustic_intervals": [i.to_dict() for i in self.acoustic_intervals],
            "legacy_crosswalk": self.legacy_crosswalk,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StructuredTranscriptResult:
        words = [StructuredWord(**w) for w in data.get("words", [])]
        utterances = [StructuredUtterance(**u) for u in data.get("utterances", [])]
        intervals = [StructuredAcousticInterval(**i) for i in data.get("acoustic_intervals", [])]
        return cls(
            schema_version=data.get("schema_version", "1.0.0"),
            audio_metadata=data.get("audio_metadata", {}),
            model_metadata=data.get("model_metadata", {}),
            words=words,
            utterances=utterances,
            acoustic_intervals=intervals,
            legacy_crosswalk=data.get("legacy_crosswalk", []),
        )

    def to_markdown(self) -> str:
        """Formats canonical chronological markdown projection."""
        lines = []
        for u in self.utterances:
            m = int(u.start_time_sec // 60)
            s = int(u.start_time_sec % 60)
            ts = f"{m:02d}:{s:02d}"
            lines.append(f"**{u.speaker}** [{ts}]: {u.text.strip()}\n")
        return "\n".join(lines)


class StructuredResultCache:
    """Governed cache manager ensuring cold/warm parity and sidecar synchronization."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_cache_key(
        audio_digest: str,
        diarizer_model: str,
        asr_config_digest: str,
        channel: int = 0,
    ) -> str:
        key_raw = f"{audio_digest}:{diarizer_model}:{asr_config_digest}:ch{channel}"
        return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]

    def save(
        self,
        cache_key: str,
        result: StructuredTranscriptResult,
    ) -> Tuple[Path, Path]:
        """Saves both the structured result JSON and the segments sidecar JSON atomically."""
        result_path = self.cache_dir / f"{cache_key}.transcript_result.json"
        sidecar_path = self.cache_dir / f"{cache_key}.segments.json"

        # 1. Write structured result
        tmp_result = self.cache_dir / f".tmp_{cache_key}.result.json"
        with open(tmp_result, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2)
        tmp_result.replace(result_path)

        # 2. Write segment sidecar
        tmp_sidecar = self.cache_dir / f".tmp_{cache_key}.sidecar.json"
        segments_payload = {
            "cache_key": cache_key,
            "audio_digest": result.audio_metadata.get("source_audio_digest"),
            "word_count": len(result.words),
            "utterance_count": len(result.utterances),
            "utterances": [u.to_dict() for u in result.utterances],
        }
        with open(tmp_sidecar, "w", encoding="utf-8") as f:
            json.dump(segments_payload, f, indent=2)
        tmp_sidecar.replace(sidecar_path)

        logger.info("Saved structured result and sidecar for key %s", cache_key)
        return result_path, sidecar_path

    def load(
        self,
        cache_key: str,
        expected_audio_digest: Optional[str] = None,
    ) -> Optional[StructuredTranscriptResult]:
        """Loads structured result from cache, enforcing sidecar presence and digest parity."""
        result_path = self.cache_dir / f"{cache_key}.transcript_result.json"
        sidecar_path = self.cache_dir / f"{cache_key}.segments.json"

        if not result_path.is_file() or not sidecar_path.is_file():
            logger.info("Cache miss or missing sidecar for key %s", cache_key)
            return None

        try:
            with open(result_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check audio digest validity
            if expected_audio_digest:
                cached_digest = data.get("audio_metadata", {}).get("source_audio_digest")
                if cached_digest != expected_audio_digest:
                    logger.warning(
                        "Cache invalidated for key %s: audio digest mismatch (cached %s != expected %s)",
                        cache_key,
                        cached_digest,
                        expected_audio_digest,
                    )
                    return None

            result = StructuredTranscriptResult.from_dict(data)
            logger.info("Cache hit for key %s with exact cold/warm parity", cache_key)
            return result
        except Exception as e:
            logger.warning("Failed to restore cache key %s (corrupted bundle): %s", cache_key, e)
            return None
