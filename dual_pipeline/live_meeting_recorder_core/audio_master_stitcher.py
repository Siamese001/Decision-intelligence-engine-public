"""
dual_pipeline/live_meeting_recorder_core/audio_master_stitcher.py

Layer 1: Resilient Multi-Epoch Audio Master Stitcher.
Stitches continuous master WAV file from rolling chunk_%04d.wav files across
failover epochs, guaranteeing a single gapless master recording upon meeting conclusion.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    MIN_VALID_CHUNK_BYTES,
    AudioChunkValidator,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_master_stitcher")


@dataclass
class StitchReceipt:
    """Immutable runtime receipt for master WAV audio stitching."""

    success: bool
    valid_chunks: int
    duration_s: float
    status: str = "SUCCESS"  # "SUCCESS", "PADDED_SUCCESS", "FAILED"
    total_chunks: int = 0
    padded_chunks: int = 0
    master_wav_sha256: Optional[str] = None
    master_wav_path: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        """Enable backwards-compatible 3-tuple unpacking: (success, valid_chunks, duration_s)."""
        return iter((self.success, self.valid_chunks, self.duration_s))

    def __getitem__(self, idx: int) -> Any:
        return (self.success, self.valid_chunks, self.duration_s)[idx]

    def __len__(self) -> int:
        return 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "valid_chunks": self.valid_chunks,
            "total_chunks": self.total_chunks,
            "padded_chunks": self.padded_chunks,
            "duration_s": round(self.duration_s, 3),
            "master_wav_sha256": self.master_wav_sha256,
            "master_wav_path": self.master_wav_path,
            "details": self.details,
        }


class AudioMasterStitcher:
    """
    Concatenates validated audio chunks into a unified master WAV file.
    Follows the atomic .tmp -> fsync -> os.replace lifecycle.
    Supports skip-and-pad mode to replace corrupt or missing chunks with silence frames.
    """

    @staticmethod
    def extract_chunk_epoch_and_index(path: Path) -> Tuple[int, int]:
        match = re.search(r"^chunk_(?:e(\d+)_)?(\d+)\.wav$", path.name)
        if not match:
            raise ValueError(f"Malformed chunk filename: {path.name}")
        epoch = int(match.group(1)) if match.group(1) is not None else 0
        return epoch, int(match.group(2))

    @staticmethod
    def extract_chunk_index(path: Path) -> int:
        _, idx = AudioMasterStitcher.extract_chunk_epoch_and_index(path)
        return idx

    @classmethod
    def get_valid_chunks(
        cls,
        chunks_dir: Path,
        min_bytes: int = MIN_VALID_CHUNK_BYTES,
    ) -> List[Path]:
        """Finds and sorts all valid chunks in directory by (epoch, index), failing fast on gaps or corrupt chunks."""
        chunk_files = list(chunks_dir.glob("chunk_*.wav"))
        if not chunk_files:
            raise RuntimeError(f"[STITCHER-EMPTY] No audio chunks found in {chunks_dir} to stitch.")

        epochs_map: Dict[int, List[Tuple[int, Path]]] = {}
        for p in chunk_files:
            epoch, idx = cls.extract_chunk_epoch_and_index(p)
            epochs_map.setdefault(epoch, []).append((idx, p))

        ordered_paths: List[Path] = []
        for epoch in sorted(epochs_map.keys()):
            epoch_chunks = sorted(epochs_map[epoch], key=lambda x: x[0])
            first_idx = epoch_chunks[0][0]
            for offset, (actual_idx, path) in enumerate(epoch_chunks):
                expected_idx = first_idx + offset
                if actual_idx != expected_idx:
                    raise RuntimeError(
                        f"[STITCHER-GAP-DETECTED] Missing audio chunk in sequence (epoch {epoch}): "
                        f"expected chunk index {expected_idx} but found {actual_idx} ({path.name}). "
                        f"Refusing to stitch incomplete meeting audio."
                    )
                ordered_paths.append(path)

        valid_chunks: List[Path] = []
        for p in ordered_paths:
            res = AudioChunkValidator.validate_chunk(p, min_bytes=min_bytes, allow_digital_zero=True)
            if not res.is_valid:
                logger.error("[STITCHER-CORRUPT-CHUNK] Corrupt or invalid chunk detected: %s (%s)", p.name, res.reason)
                raise RuntimeError(
                    f"[STITCHER-CORRUPT-CHUNK] Audio chunk {p.name} failed integrity validation: {res.reason}. "
                    f"Refusing to silently drop audio."
                )
            valid_chunks.append(p)
        return valid_chunks

    @classmethod
    def stitch_chunk_files(
        cls,
        valid_chunks: List[Path],
        target_master_wav: Path,
        transcript_segments: Optional[List[Dict[str, Any]]] = None,
    ) -> StitchReceipt:
        """
        Losslessly concatenates an explicit list of valid chunks into target_master_wav atomically.
        Returns: StitchReceipt (supports backwards-compatible (success, count, duration) unpacking).
        """
        if not valid_chunks:
            logger.error("[STITCHER-EMPTY] No valid audio chunks provided to stitch.")
            return StitchReceipt(
                success=False,
                valid_chunks=0,
                duration_s=0.0,
                status="FAILED",
                total_chunks=0,
                padded_chunks=0,
            )

        target_master_wav = Path(target_master_wav)
        target_master_wav.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target_master_wav.with_suffix(".stitch.tmp")

        total_frames = 0
        framerate = 16000

        try:
            # Read format from first valid chunk
            with wave.open(str(valid_chunks[0]), "rb") as first_w:
                nchannels = first_w.getnchannels()
                sampwidth = first_w.getsampwidth()
                framerate = first_w.getframerate()
                comptype = first_w.getcomptype()
                compname = first_w.getcompname()

            with wave.open(str(tmp_target), "wb") as out_w:
                out_w.setparams((nchannels, sampwidth, framerate, 0, comptype, compname))
                for chunk_p in valid_chunks:
                    try:
                        with wave.open(str(chunk_p), "rb") as in_w:
                            # Verify compatible parameters
                            if (
                                in_w.getframerate() != framerate
                                or in_w.getnchannels() != nchannels
                                or in_w.getsampwidth() != sampwidth
                            ):
                                raise RuntimeError(
                                    f"[STITCHER-PARAM-MISMATCH] Chunk {chunk_p.name} format mismatch: "
                                    f"expected {framerate}Hz/{nchannels}ch/{sampwidth}B, "
                                    f"got {in_w.getframerate()}Hz/{in_w.getnchannels()}ch/{in_w.getsampwidth()}B."
                                )
                            n = in_w.getnframes()
                            data = in_w.readframes(n)
                            out_w.writeframes(data)
                            total_frames += n
                    except Exception as ce:
                        logger.error("[STITCHER-READ-ERROR] Error reading chunk %s: %s", chunk_p.name, ce)
                        raise RuntimeError(f"[STITCHER-READ-ERROR] Failed to read chunk {chunk_p.name}: {ce}") from ce

            total_duration = total_frames / float(framerate) if framerate > 0 else 0.0

            if transcript_segments is not None:
                from dual_pipeline.audio_duration_gate import reconcile_transcript_coverage
                reconcile_transcript_coverage(total_duration, transcript_segments)

            # Atomic replace
            os.replace(tmp_target, target_master_wav)

            # Compute sha256
            hasher = hashlib.sha256()
            with open(target_master_wav, "rb") as f_wav:
                for b_chunk in iter(lambda: f_wav.read(65536), b""):
                    hasher.update(b_chunk)
            master_sha = hasher.hexdigest()

            logger.info(
                "[STITCHER-SUCCESS] Successfully stitched %d chunks (%0.2fs) into %s",
                len(valid_chunks), total_duration, target_master_wav.name,
            )
            return StitchReceipt(
                success=True,
                valid_chunks=len(valid_chunks),
                duration_s=total_duration,
                status="SUCCESS",
                total_chunks=len(valid_chunks),
                padded_chunks=0,
                master_wav_sha256=master_sha,
                master_wav_path=str(target_master_wav),
            )

        except Exception as e:
            logger.critical("[STITCHER-FAILED] Failed to stitch master WAV: %s", e)
            if tmp_target.exists():
                with contextlib.suppress(Exception):
                    tmp_target.unlink()
            raise RuntimeError(f"Failed to stitch master WAV: {e}") from e

    @classmethod
    def stitch_master_wav(
        cls,
        chunks_dir: Path,
        target_master_wav: Path,
        min_bytes: int = MIN_VALID_CHUNK_BYTES,
        transcript_segments: Optional[List[Dict[str, Any]]] = None,
        pad_missing_or_corrupt: bool = False,
    ) -> StitchReceipt:
        """
        Losslessly concatenates chunks from chunks_dir into target_master_wav atomically.
        When pad_missing_or_corrupt is True, missing or corrupt chunks are replaced with silence frames.
        Returns: StitchReceipt (supports backwards-compatible (success, count, duration) unpacking).
        """
        chunks_dir = Path(chunks_dir)
        target_master_wav = Path(target_master_wav)

        if not pad_missing_or_corrupt:
            valid_chunks = cls.get_valid_chunks(chunks_dir, min_bytes=min_bytes)
            if not valid_chunks:
                raise RuntimeError(f"[STITCHER-EMPTY] No valid audio chunks found in {chunks_dir} to stitch.")
            return cls.stitch_chunk_files(valid_chunks, target_master_wav, transcript_segments=transcript_segments)

        return cls._stitch_master_wav_with_padding(
            chunks_dir=chunks_dir,
            target_master_wav=target_master_wav,
            min_bytes=min_bytes,
            transcript_segments=transcript_segments,
        )

    @classmethod
    def _is_chunk_valid(cls, p: Optional[Path], min_bytes: int) -> bool:
        if p is None or not p.is_file():
            return False
        res = AudioChunkValidator.validate_chunk(p, min_bytes=min_bytes, allow_digital_zero=True)
        if not res.is_valid:
            logger.warning("[STITCHER-PAD] Chunk %s corrupt (%s); padding with silence.", p.name, res.reason)
            return False
        try:
            with wave.open(str(p), "rb") as test_w:
                return test_w.getnframes() > 0
        except Exception as e:
            logger.warning("[STITCHER-PAD] Cannot open chunk %s (%s); padding with silence.", p.name, e)
            return False

    @staticmethod
    def _write_single_chunk(
        out_w: Any,
        chunk_p: Path,
        silence_chunk_data: bytes,
        default_chunk_frames: int,
        expected_params: Optional[Tuple[int, int, int]] = None,
    ) -> Tuple[int, bool]:
        try:
            with wave.open(str(chunk_p), "rb") as in_w:
                if expected_params is not None:
                    actual = (in_w.getnchannels(), in_w.getsampwidth(), in_w.getframerate())
                    if actual != expected_params:
                        raise ValueError(
                            f"Chunk {chunk_p.name} audio params {actual} != expected {expected_params}"
                        )
                n = in_w.getnframes()
                data = in_w.readframes(n)
                out_w.writeframes(data)
                return n, False
        except Exception as e:
            logger.warning("[STITCHER-PAD-FALLBACK] Failed reading %s on write: %s; padding.", chunk_p.name, e)
            out_w.writeframes(silence_chunk_data)
            return default_chunk_frames, True

    @staticmethod
    def _compute_file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f_wav:
            for b_chunk in iter(lambda: f_wav.read(65536), b""):
                hasher.update(b_chunk)
        return hasher.hexdigest()

    @classmethod
    def _scan_and_validate_chunks(
        cls, chunks_dir: Path, min_bytes: int,
    ) -> Tuple[int, int, Dict[int, Path], List[int], Path]:
        from dual_pipeline.live_meeting_recorder_core.chunk_health import build_epoch_indexed_chunk_map
        chunk_files = list(chunks_dir.glob("chunk_*.wav"))
        if not chunk_files:
            raise RuntimeError(f"[STITCHER-EMPTY] No audio chunks found in {chunks_dir} to stitch.")
        min_idx, max_idx, indexed_paths = build_epoch_indexed_chunk_map(chunk_files)
        if not indexed_paths:
            raise RuntimeError(f"[STITCHER-EMPTY] No properly named chunk_*.wav files found in {chunks_dir}.")
        total_expected = max_idx - min_idx + 1
        valid_map: Dict[int, Path] = {}
        padded_indices: List[int] = []
        first_valid_path: Optional[Path] = None
        for idx in range(min_idx, max_idx + 1):
            p = indexed_paths.get(idx)
            if not cls._is_chunk_valid(p, min_bytes):
                padded_indices.append(idx)
                continue
            assert p is not None
            valid_map[idx] = p
            first_valid_path = first_valid_path or p
        if not valid_map or first_valid_path is None:
            raise RuntimeError(f"[STITCHER-ALL-CORRUPT] All {total_expected} chunks in {chunks_dir} are missing or corrupt.")
        return min_idx, max_idx, valid_map, padded_indices, first_valid_path

    @classmethod
    def _stitch_master_wav_with_padding(
        cls,
        chunks_dir: Path,
        target_master_wav: Path,
        min_bytes: int = MIN_VALID_CHUNK_BYTES,
        transcript_segments: Optional[List[Dict[str, Any]]] = None,
    ) -> StitchReceipt:
        # Skip-and-pad mode: resilient concatenation replacing corrupt or missing chunks with silence
        min_idx, max_idx, valid_map, padded_indices, first_valid_path = cls._scan_and_validate_chunks(chunks_dir, min_bytes)
        total_expected = max_idx - min_idx + 1

        # Determine audio parameters and chunk frame count from first valid chunk
        with wave.open(str(first_valid_path), "rb") as ref_w:
            nchannels, sampwidth, framerate = ref_w.getnchannels(), ref_w.getsampwidth(), ref_w.getframerate()
            comptype, compname, default_chunk_frames = ref_w.getcomptype(), ref_w.getcompname(), ref_w.getnframes()

        if default_chunk_frames <= 0:
            default_chunk_frames = framerate * 15
        silence_chunk_data = (b"\x00" * (sampwidth * nchannels)) * default_chunk_frames
        target_master_wav.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target_master_wav.with_suffix(".stitch.tmp")

        total_frames = 0
        try:
            with wave.open(str(tmp_target), "wb") as out_w:
                out_w.setparams((nchannels, sampwidth, framerate, 0, comptype, compname))
                for idx in range(min_idx, max_idx + 1):
                    if idx in valid_map:
                        n, fell_back = cls._write_single_chunk(
                            out_w, valid_map[idx], silence_chunk_data, default_chunk_frames,
                            expected_params=(nchannels, sampwidth, framerate),
                        )
                        total_frames += n
                        if fell_back and idx not in padded_indices:
                            padded_indices.append(idx)
                    else:
                        out_w.writeframes(silence_chunk_data)
                        total_frames += default_chunk_frames

            total_duration = total_frames / float(framerate) if framerate > 0 else 0.0
            if transcript_segments is not None:
                from dual_pipeline.audio_duration_gate import reconcile_transcript_coverage
                reconcile_transcript_coverage(total_duration, transcript_segments)

            os.replace(tmp_target, target_master_wav)
            master_sha = cls._compute_file_sha256(target_master_wav)
            status = "SUCCESS" if not padded_indices else "PADDED_SUCCESS"
            logger.info(
                "[STITCHER-%s] Stitched %d chunks (%d valid, %d padded, %0.2fs) into %s",
                status, total_expected, len(valid_map), len(padded_indices), total_duration, target_master_wav.name,
            )
            return StitchReceipt(
                success=True, valid_chunks=len(valid_map), duration_s=total_duration, status=status,
                total_chunks=total_expected, padded_chunks=len(padded_indices), master_wav_sha256=master_sha,
                master_wav_path=str(target_master_wav), details={"padded_indices": padded_indices},
            )
        except Exception as e:
            logger.critical("[STITCHER-FAILED] Failed to stitch master WAV with padding: %s", e)
            if tmp_target.exists():
                with contextlib.suppress(Exception):
                    tmp_target.unlink()
            raise RuntimeError(f"Failed to stitch master WAV: {e}") from e
