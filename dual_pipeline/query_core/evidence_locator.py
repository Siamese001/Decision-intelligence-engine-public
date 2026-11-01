"""Evidence Locator Service for targeted forensic transcript text retrieval without unindexed directory scans."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dual_pipeline.core import connect_sqlite


@dataclass(frozen=True)
class ForensicDialogueTurn:
    timestamp: str
    header_line: int
    byte_offset: int
    speaker: str
    utterance: str
    raw_markdown: str


@dataclass(frozen=True)
class EvidenceLocatorResult:
    success: bool
    source_id: str
    cold_archive_ref: str
    resolved_file_path: Optional[str]
    target_timestamp: str
    latency_ms: float
    target_turn: Optional[ForensicDialogueTurn]
    context_turns: List[ForensicDialogueTurn]
    error_message: Optional[str] = None


class EvidenceLocatorService:
    """Provides high-performance, single-file targeted text seeking from cold archives."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self._timestamp_header_pattern = re.compile(r"^###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
        self._speaker_pattern = re.compile(r"^\*\*([^*:]+)(?::\*\*|\*\*:\s*|\*\*)\s*(.*)")

    def resolve_archive_path(self, cold_archive_ref: str) -> Optional[Path]:
        """Resolves cold_archive_ref directly to an absolute filesystem Path within workspace boundaries."""
        if not cold_archive_ref or cold_archive_ref.startswith(("cold_archive://", "chat_intake")):
            # Extract relative path if cold_archive:// URL scheme is used
            if cold_archive_ref.startswith("cold_archive://"):
                cold_archive_ref = cold_archive_ref.replace("cold_archive://", "")
            else:
                return None

        clean_ref = cold_archive_ref.lstrip("/")

        # Candidate paths to evaluate deterministically
        candidate_paths = [
            self.repo_root / clean_ref,
            self.repo_root / "output" / clean_ref,
        ]

        for candidate in candidate_paths:
            try:
                resolved = candidate.resolve()
                if resolved.is_file() and resolved.is_relative_to(self.repo_root):
                    return resolved
            except (ValueError, OSError):
                continue

        # If not directly matched, check if path matches relative to output root
        if "output/" in clean_ref:
            subpath = clean_ref.split("output/", 1)[1]
            candidate = (self.repo_root / "output" / subpath).resolve()
            if candidate.is_file() and candidate.is_relative_to(self.repo_root):
                return candidate

        return None

    def locate_exchange(
        self,
        cold_archive_ref: str,
        locator_kind: str,
        locator_value: str,
        context_window_turns: int = 0,
        source_id: str = "",
    ) -> EvidenceLocatorResult:
        """Performs targeted line-seek on the resolved archive file in < 50ms without directory scanning."""
        start_time = time.perf_counter()

        target_file = self.resolve_archive_path(cold_archive_ref)
        if not target_file:
            latency = (time.perf_counter() - start_time) * 1000.0
            return EvidenceLocatorResult(
                success=False,
                source_id=source_id,
                cold_archive_ref=cold_archive_ref,
                resolved_file_path=None,
                target_timestamp=locator_value,
                latency_ms=latency,
                target_turn=None,
                context_turns=[],
                error_message=f"Archive file could not be resolved within workspace: {cold_archive_ref}",
            )

        # Standardize target timestamp (e.g. '0:04:15' -> '00:04:15')
        norm_target = self._normalize_timestamp(locator_value)

        # Parse turns line-by-line using streaming read
        all_turns: List[ForensicDialogueTurn] = []
        target_idx: Optional[int] = None

        try:
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                current_timestamp: Optional[str] = None
                current_header_line: int = 0
                current_byte_offset: int = 0
                current_speaker: Optional[str] = None
                current_utterance_lines: List[str] = []
                current_raw_lines: List[str] = []

                line_num = 0
                byte_pos = 0

                for line in f:
                    line_num += 1
                    header_match = self._timestamp_header_pattern.match(line.strip())

                    if header_match:
                        # Flush previous turn
                        if current_timestamp and current_speaker:
                            utterance_text = "\n".join(current_utterance_lines).strip()
                            raw_block = "\n".join(current_raw_lines)
                            turn = ForensicDialogueTurn(
                                timestamp=current_timestamp,
                                header_line=current_header_line,
                                byte_offset=current_byte_offset,
                                speaker=current_speaker,
                                utterance=utterance_text,
                                raw_markdown=raw_block,
                            )
                            if self._normalize_timestamp(current_timestamp) == norm_target:
                                target_idx = len(all_turns)
                            all_turns.append(turn)

                        current_timestamp = header_match.group(1)
                        current_header_line = line_num
                        current_byte_offset = byte_pos
                        current_speaker = None
                        current_utterance_lines = []
                        current_raw_lines = [line.rstrip("\r\n")]
                    elif current_timestamp is not None:
                        current_raw_lines.append(line.rstrip("\r\n"))
                        spk_match = self._speaker_pattern.match(line.strip())
                        if spk_match and current_speaker is None:
                            current_speaker = spk_match.group(1).strip()
                            current_utterance_lines.append(spk_match.group(2).strip())
                        elif current_speaker is not None:
                            current_utterance_lines.append(line.strip())

                    byte_pos += len(line.encode("utf-8"))

                # Flush trailing turn
                if current_timestamp and current_speaker:
                    utterance_text = "\n".join(current_utterance_lines).strip()
                    raw_block = "\n".join(current_raw_lines)
                    turn = ForensicDialogueTurn(
                        timestamp=current_timestamp,
                        header_line=current_header_line,
                        byte_offset=current_byte_offset,
                        speaker=current_speaker,
                        utterance=utterance_text,
                        raw_markdown=raw_block,
                    )
                    if self._normalize_timestamp(current_timestamp) == norm_target:
                        target_idx = len(all_turns)
                    all_turns.append(turn)

        except Exception as e:
            latency = (time.perf_counter() - start_time) * 1000.0
            return EvidenceLocatorResult(
                success=False,
                source_id=source_id,
                cold_archive_ref=cold_archive_ref,
                resolved_file_path=str(target_file),
                target_timestamp=locator_value,
                latency_ms=latency,
                target_turn=None,
                context_turns=[],
                error_message=f"I/O error during line seeking: {e}",
            )

        latency = (time.perf_counter() - start_time) * 1000.0

        if target_idx is None:
            return EvidenceLocatorResult(
                success=False,
                source_id=source_id,
                cold_archive_ref=cold_archive_ref,
                resolved_file_path=str(target_file),
                target_timestamp=locator_value,
                latency_ms=latency,
                target_turn=None,
                context_turns=[],
                error_message=f"Timestamp '{locator_value}' not found in archive {target_file.name}",
            )

        target_turn = all_turns[target_idx]
        context_turns: List[ForensicDialogueTurn] = []

        if context_window_turns > 0:
            start_ctx = max(0, target_idx - context_window_turns)
            end_ctx = min(len(all_turns), target_idx + context_window_turns + 1)
            context_turns = [all_turns[i] for i in range(start_ctx, end_ctx) if i != target_idx]

        return EvidenceLocatorResult(
            success=True,
            source_id=source_id,
            cold_archive_ref=cold_archive_ref,
            resolved_file_path=str(target_file),
            target_timestamp=locator_value,
            latency_ms=latency,
            target_turn=target_turn,
            context_turns=context_turns,
        )

    def locate_by_evidence_id(
        self,
        graph_path: Path,
        evidence_id: str,
        context_window_turns: int = 0,
    ) -> EvidenceLocatorResult:
        """One-stop helper: queries graph.sqlite for source and locator, then extracts dialogue exchange."""
        start_time = time.perf_counter()
        conn = connect_sqlite(graph_path, readonly=True)
        try:
            row = conn.execute(
                """
                SELECT e.evidence_id, e.source_id, e.locator_kind, e.locator_value, s.cold_archive_ref
                FROM evidence_item e
                JOIN source s ON e.source_id = s.source_id
                WHERE e.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()

            if not row:
                latency = (time.perf_counter() - start_time) * 1000.0
                return EvidenceLocatorResult(
                    success=False,
                    source_id="",
                    cold_archive_ref="",
                    resolved_file_path=None,
                    target_timestamp="",
                    latency_ms=latency,
                    target_turn=None,
                    context_turns=[],
                    error_message=f"Evidence ID '{evidence_id}' not found in database {graph_path.name}",
                )

            source_id = str(row[1])
            locator_kind = str(row[2])
            locator_value = str(row[3])
            cold_archive_ref = str(row[4]) if row[4] else ""

            return self.locate_exchange(
                cold_archive_ref=cold_archive_ref,
                locator_kind=locator_kind,
                locator_value=locator_value,
                context_window_turns=context_window_turns,
                source_id=source_id,
            )
        finally:
            conn.close()

    @staticmethod
    def _normalize_timestamp(ts: str) -> str:
        parts = ts.strip().split(":")
        if len(parts) == 2:
            return f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
        elif len(parts) == 3:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        return ts.strip()
