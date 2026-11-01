"""
dual_pipeline/live_meeting_recorder_core/speaker_hint_queue.py

Durable persistent queue for speaker attribution hints.
Enforces:
  1. Zero synchronous LLM/conversational dialogue for hints during active meetings.
  2. Single-save durability with deduplication across identical speaker/quote pairs.
  3. Survival of early hints before segments exist (truthfully QUEUED).
  4. Asynchronous reconciliation to voice clusters once segments are transcribed.
  5. Sample/monotonic timestamp preservation with explicit None for unknown times.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.speaker_hint_queue")
_QUEUE_MUTEX = threading.Lock()


def normalize_hint_text(text: str) -> str:
    """Lowercases, removes punctuation, and collapses whitespace for deduplication and matching."""
    if not text:
        return ""
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(clean.split())


@dataclass
class QueuedSpeakerHint:
    hint_id: str
    speaker_name: str
    quote: str
    raw_input: str = ""
    session_elapsed_seconds: Optional[float] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "QUEUED"  # "QUEUED", "RECONCILED", "CONFLICT"
    voice_id: Optional[str] = None
    updated_segments_count: int = 0
    ledger_digest: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueuedSpeakerHint":
        return cls(**data)


class DurableSpeakerHintQueue:
    """Thread-safe persistent queue for managing pending and reconciled speaker hints."""

    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        cache_dir: Optional[Union[str, Path]] = None,
        queue_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else None
        self.cache_dir = Path(cache_dir) if cache_dir else (self.output_dir if self.output_dir else None)
        if queue_path:
            self.queue_path = Path(queue_path)
        elif self.cache_dir:
            self.queue_path = self.cache_dir / "speaker_hints_queue.json"
        elif self.output_dir:
            self.queue_path = self.output_dir / "speaker_hints_queue.json"
        else:
            self.queue_path = Path("speaker_hints_queue.json")

        self._hints: List[QueuedSpeakerHint] = []
        self.load()

    def _find_existing(self, speaker_name: str, quote: str) -> Optional[QueuedSpeakerHint]:
        norm_speaker = speaker_name.strip().lower()
        norm_quote = normalize_hint_text(quote)
        for h in self._hints:
            if h.speaker_name.strip().lower() == norm_speaker and normalize_hint_text(h.quote) == norm_quote:
                return h
        return None

    def enqueue_hint(
        self,
        speaker: str,
        quote: str,
        raw_input: str = "",
        elapsed_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[QueuedSpeakerHint, bool]:
        """
        Enqueues a speaker hint into the durable store.
        Returns (QueuedSpeakerHint, is_new). If duplicate, returns (existing, False).
        """
        with _QUEUE_MUTEX:
            self._load_internal()
            existing = self._find_existing(speaker, quote)
            if existing:
                return existing, False

            hint_id = f"hint_{uuid.uuid4().hex[:8]}"
            new_hint = QueuedSpeakerHint(
                hint_id=hint_id,
                speaker_name=speaker.strip(),
                quote=quote.strip(),
                raw_input=raw_input or f"{speaker} - \"{quote}\"",
                session_elapsed_seconds=elapsed_seconds,
                status="QUEUED",
                metadata=metadata or {},
            )
            self._hints.append(new_hint)
            self._save_internal()
            return new_hint, True

    def mark_reconciled(
        self,
        hint_id: str,
        voice_id: str,
        updated_segments_count: int = 0,
        ledger_digest: Optional[str] = None,
    ) -> bool:
        """Marks a queued hint as reconciled with an acoustic voice cluster."""
        for h in self._hints:
            if h.hint_id == hint_id:
                h.status = "RECONCILED"
                h.voice_id = voice_id
                h.updated_segments_count = updated_segments_count
                h.ledger_digest = ledger_digest
                self.save()
                return True
        return False

    def _reconcile_single_hint(
        self,
        h: QueuedSpeakerHint,
        speaker_hint_manager: Any,
        segments_jsonl_path: Path,
        transcript_md_path: Optional[Path],
        meeting_slug: str,
        force: bool,
        v_reg: Optional[Any],
    ) -> Optional[Any]:
        try:
            res = speaker_hint_manager.apply_hint(
                quote=h.quote,
                speaker_name=h.speaker_name,
                segments_jsonl_path=segments_jsonl_path,
                transcript_md_path=transcript_md_path,
                meeting_slug=meeting_slug,
                force=force,
                elapsed_seconds=h.session_elapsed_seconds,
            )
            if res.status == "PASS":
                target_vid = res.voice_id or "Voice A"
                if v_reg and hasattr(v_reg, "bind_speaker_hint"):
                    v_reg.bind_speaker_hint(
                        voice_id=target_vid,
                        speaker_name=h.speaker_name,
                        quote=h.quote,
                    )
                self.mark_reconciled(
                    hint_id=h.hint_id,
                    voice_id=target_vid,
                    updated_segments_count=res.updated_segments_count,
                    ledger_digest=res.ledger_digest,
                )
                logger.info(
                    "[HINT-RECONCILED] Hint %s reconciled: %s -> %s (%d segments)",
                    h.hint_id,
                    h.speaker_name,
                    target_vid,
                    res.updated_segments_count,
                )
                return res
        except Exception as exc:
            logger.warning("Error reconciling hint %s: %s", h.hint_id, exc)
        return None

    def _partition_pending_and_sync_registry(self, v_reg: Optional[Any]) -> List[QueuedSpeakerHint]:
        pending: List[QueuedSpeakerHint] = []
        for h in self._hints:
            if h.status == "RECONCILED" and h.voice_id and h.speaker_name and v_reg and hasattr(v_reg, "bind_speaker_hint"):
                v_reg.bind_speaker_hint(voice_id=h.voice_id, speaker_name=h.speaker_name, quote=h.quote)
            elif h.status == "QUEUED":
                if h.quote.startswith("[ROLE_HINT:") or (isinstance(h.metadata, dict) and h.metadata.get("attribution_type") == "role_hint"):
                    h.status = "RECONCILED"
                else:
                    pending.append(h)
        return pending

    def reconcile_pending_hints(
        self,
        speaker_hint_manager: Any,
        segments_jsonl_path: Path,
        transcript_md_path: Optional[Path] = None,
        meeting_slug: str = "meeting",
        force: bool = False,
        voice_registry: Optional[Any] = None,
    ) -> List[Any]:
        """
        Reconciles all pending QUEUED hints against currently available segments.
        If a hint matches transcribed speech, binds voice cluster retroactively,
        signs cryptographic ledger entry, and marks the hint as RECONCILED.
        """
        with _QUEUE_MUTEX:
            self._load_internal()
            if not self._hints:
                return []

        if not segments_jsonl_path or not segments_jsonl_path.is_file():
            return []

        try:
            if not segments_jsonl_path.read_text(encoding="utf-8").strip():
                return []
        except Exception:
            return []

        v_reg = voice_registry or getattr(speaker_hint_manager, "voice_registry", None)
        pending = self._partition_pending_and_sync_registry(v_reg)
        reconciled_results: List[Any] = []
        for h in pending:
            res = self._reconcile_single_hint(
                h=h,
                speaker_hint_manager=speaker_hint_manager,
                segments_jsonl_path=segments_jsonl_path,
                transcript_md_path=transcript_md_path,
                meeting_slug=meeting_slug,
                force=force,
                v_reg=v_reg,
            )
            if res is not None:
                reconciled_results.append(res)

        return reconciled_results

    def get_pending_hints(self) -> List[QueuedSpeakerHint]:
        return [h for h in self._hints if h.status == "QUEUED"]

    def get_reconciled_hints(self) -> List[QueuedSpeakerHint]:
        return [h for h in self._hints if h.status == "RECONCILED"]

    def _save_internal(self) -> Path:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [h.to_dict() for h in self._hints]
        temp_path = self.queue_path.with_name(f"{self.queue_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp_path.replace(self.queue_path)
        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise

        # Mirror to sibling directory if both cache_dir and output_dir exist
        if self.cache_dir and self.output_dir and self.cache_dir.resolve() != self.output_dir.resolve():
            sibling_dir = self.output_dir if self.queue_path.parent.resolve() == self.cache_dir.resolve() else self.cache_dir
            sibling_dir.mkdir(parents=True, exist_ok=True)
            sibling_file = sibling_dir / "speaker_hints_queue.json"
            try:
                sibling_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug("Could not mirror speaker hints queue to %s: %s", sibling_file, e)

        return self.queue_path

    def _load_internal(self) -> List[QueuedSpeakerHint]:
        candidate_paths = [self.queue_path]
        if self.cache_dir:
            candidate_paths.append(self.cache_dir / "speaker_hints_queue.json")
        if self.output_dir:
            candidate_paths.append(self.output_dir / "speaker_hints_queue.json")
        for p in candidate_paths:
            if p and p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        self._hints = [QueuedSpeakerHint.from_dict(d) for d in data if isinstance(d, dict)]
                        return self._hints
                except Exception as exc:
                    logger.warning("Failed to load speaker hints queue from %s: %s", p, exc)
        return self._hints

    def save(self) -> Path:
        """Saves hint queue atomically to disk."""
        with _QUEUE_MUTEX:
            return self._save_internal()

    def load(self) -> List[QueuedSpeakerHint]:
        """Loads hints from disk."""
        with _QUEUE_MUTEX:
            return self._load_internal()


# Backward compatibility alias
SpeakerHintQueue = DurableSpeakerHintQueue

