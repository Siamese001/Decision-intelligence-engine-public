"""
dual_pipeline/live_meeting_recorder_core/streaming_turn_embedder.py

Wave 5 Streaming Per-Channel VAD Segmenter, Bleed Suppressor & 256-D Turn Embedder.
Segments live recording chunks into 1.5-4.0s voiced windows per channel,
suppresses Ch0 -> Ch1 room acoustic bleed, extracts 16 kHz 256-D embeddings,
resolves live Copilot matches against Stage-0 preloaded centroids, and appends
records to `turn_embeddings.jsonl`.
"""
from __future__ import annotations

import json
import logging
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .acoustic_features import extract_256d_acoustic_embedding, resample_to_16khz
from .stage0_speaker_preloader import Stage0SpeakerPreloadResult

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.streaming_turn_embedder")


@dataclass
class StreamingTurnRecord:
    """Single 256-D turn embedding record persisted to turn_embeddings.jsonl."""
    turn_id: str
    chunk_index: int
    channel: int
    start_sec: float
    end_sec: float
    duration_sec: float
    embedding: List[float]
    rms_energy: float
    bleed_suppressed: bool = False
    live_match_speaker: Optional[str] = None
    live_match_score: float = 0.0
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_wav_stereo_or_mono(wav_path: Path) -> Tuple[np.ndarray, int, int]:
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    if sw == 2:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        arr = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if n_ch > 1:
        arr = arr.reshape(-1, n_ch)
    else:
        arr = arr.reshape(-1, 1)
    return arr, sr, n_ch


def is_ch0_to_ch1_bleed(
    win_ch0: np.ndarray,
    win_ch1: np.ndarray,
    min_active_rms: float = 0.015,
    bleed_ratio_ceiling: float = 0.30,
) -> bool:
    """Detects when Ch1 signal is room acoustic bleed from dominant Ch0 remote playback."""
    rms0 = float(np.sqrt(np.mean(win_ch0 ** 2))) if len(win_ch0) > 0 else 0.0
    rms1 = float(np.sqrt(np.mean(win_ch1 ** 2))) if len(win_ch1) > 0 else 0.0
    if rms1 < min_active_rms:
        return True
    if rms0 < min_active_rms:
        return False
    ratio = rms1 / max(1e-6, rms0)
    if ratio <= bleed_ratio_ceiling:
        return True
    if ratio <= 0.45 and len(win_ch0) == len(win_ch1) and len(win_ch0) > 64:
        c_mat = np.corrcoef(np.abs(win_ch0), np.abs(win_ch1))
        corr = float(abs(c_mat[0, 1])) if c_mat.shape == (2, 2) and np.isfinite(c_mat[0, 1]) else 0.0
        if corr >= 0.65:
            return True
    return False


class StreamingTurnEmbedder:
    """Extracts per-channel 16 kHz 256-D turn embeddings during live recording."""

    def __init__(
        self,
        output_jsonl_path: Union[str, Path],
        stage0_context: Optional[Stage0SpeakerPreloadResult] = None,
        window_sec: float = 2.5,
        min_window_sec: float = 1.5,
        max_window_sec: float = 4.0,
        min_active_rms: float = 0.015,
        bleed_ratio_ceiling: float = 0.30,
        embedding_fn: Optional[Callable[[np.ndarray, int, int], Sequence[float]]] = None,
        similarity_threshold: float = 0.78,
    ) -> None:
        self.output_jsonl_path = Path(output_jsonl_path)
        self.output_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.stage0_context = stage0_context
        self.window_sec = max(min_window_sec, min(max_window_sec, float(window_sec)))
        self.min_window_sec = min_window_sec
        self.max_window_sec = max_window_sec
        self.min_active_rms = min_active_rms
        self.bleed_ratio_ceiling = bleed_ratio_ceiling
        self.embedding_fn = embedding_fn
        self.similarity_threshold = similarity_threshold
        self._turn_counter = 0
        self.suppressed_bleed_windows = 0
        self._observed_bleed_ratios: List[float] = []

    def learn_hardware_bleed_ratio(self, win_ch0: np.ndarray, win_ch1: np.ndarray) -> float:
        """Online EMA calibration of Ch0->Ch1 acoustic bleed ratio ceiling for active hardware."""
        rms0 = float(np.sqrt(np.mean(win_ch0 ** 2))) if len(win_ch0) > 0 else 0.0
        rms1 = float(np.sqrt(np.mean(win_ch1 ** 2))) if len(win_ch1) > 0 else 0.0
        if rms0 >= self.min_active_rms * 2.0 and rms1 > 0.0:
            ratio = rms1 / rms0
            if ratio <= 0.48 and len(win_ch0) == len(win_ch1) and len(win_ch0) > 64:
                c_mat = np.corrcoef(np.abs(win_ch0), np.abs(win_ch1))
                corr = float(abs(c_mat[0, 1])) if c_mat.shape == (2, 2) and np.isfinite(c_mat[0, 1]) else 0.0
                if corr >= 0.60:
                    self._observed_bleed_ratios.append(ratio)
                    ema_target = min(0.45, max(0.20, ratio * 1.15))
                    self.bleed_ratio_ceiling = round(0.75 * self.bleed_ratio_ceiling + 0.25 * ema_target, 4)
        return self.bleed_ratio_ceiling

    def _matches_propagated_centroid(self, rec: Dict[str, Any], new_centroid: Optional[Sequence[float]]) -> bool:
        emb = rec.get("embedding")
        if new_centroid is None or not isinstance(emb, list) or len(emb) != 256:
            return False
        sim = float(np.dot(np.asarray(emb, dtype=np.float32), np.asarray(new_centroid, dtype=np.float32)))
        if sim >= self.similarity_threshold:
            rec["live_match_score"] = round(sim, 4)
            return True
        return False

    def propagate_speaker_binding(
        self,
        speaker_name: str,
        new_centroid: Optional[Sequence[float]] = None,
        match_turn_ids: Optional[Sequence[str]] = None,
    ) -> int:
        """Updates Stage-0 live centroids and backfills matching turn records in turn_embeddings.jsonl."""
        if self.stage0_context is not None and new_centroid is not None and len(new_centroid) == 256:
            self.stage0_context.enrolled_centroids[speaker_name] = [float(x) for x in new_centroid]
        if not self.output_jsonl_path.is_file():
            return 0
        target_ids = set(match_turn_ids or [])
        updated_count = 0
        lines_out: List[str] = []
        for raw_line in (line for line in self.output_jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()):
            rec = json.loads(raw_line)
            if rec.get("turn_id") in target_ids or self._matches_propagated_centroid(rec, new_centroid):
                rec["live_match_speaker"] = speaker_name
                updated_count += 1
            lines_out.append(json.dumps(rec))
        if updated_count > 0:
            self.output_jsonl_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        return updated_count

    def _compute_256d(self, samples_1d: np.ndarray, sr: int, channel: int) -> List[float]:
        samples_16k = resample_to_16khz(samples_1d, sr, 16000)
        if self.embedding_fn is not None:
            raw = self.embedding_fn(samples_16k, 16000, channel)
            arr = np.asarray(raw, dtype=np.float32).ravel()
        else:
            arr = extract_256d_acoustic_embedding(samples_16k, sr=16000)
        if arr.size != 256:
            raise ValueError(f"StreamingTurnEmbedder requires 256-D vector, got {arr.size}-D")
        norm = float(np.linalg.norm(arr))
        if norm <= 1e-8:
            return [0.0] * 256
        return [round(float(x) / norm, 6) for x in arr]

    def _match_stage0_live(self, emb: List[float], channel: int) -> Tuple[Optional[str], float]:
        if self.stage0_context is None:
            return None, 0.0
        if channel == 1 and self.stage0_context.ch1_speaker:
            return self.stage0_context.ch1_speaker, 1.0

        vec = np.asarray(emb, dtype=np.float32)
        best_spk: Optional[str] = None
        best_sim = -1.0
        for name, c_list in self.stage0_context.enrolled_centroids.items():
            if channel == 0 and name == self.stage0_context.ch1_speaker:
                continue
            c_vec = np.asarray(c_list, dtype=np.float32)
            sim = min(1.0, float(np.dot(vec, c_vec)) + 0.05)
            if sim > best_sim:
                best_sim = sim
                best_spk = name
        for name, c_list in self.stage0_context.global_prior_centroids.items():
            c_vec = np.asarray(c_list, dtype=np.float32)
            sim = float(np.dot(vec, c_vec))
            if sim > best_sim:
                best_sim = sim
                best_spk = name
        if best_spk and best_sim >= self.similarity_threshold:
            return best_spk, round(best_sim, 4)
        return None, round(max(0.0, best_sim), 4)

    def _emit_single_channel_record(
        self,
        samples_1d: np.ndarray,
        sr: int,
        channel: int,
        chunk_index: int,
        w_st: float,
        w_en: float,
        w_dur: float,
        rms: float,
    ) -> StreamingTurnRecord:
        emb = self._compute_256d(samples_1d, sr, channel)
        m_spk, m_sc = self._match_stage0_live(emb, channel)
        self._turn_counter += 1
        return StreamingTurnRecord(
            turn_id=f"turn_{self._turn_counter:05d}",
            chunk_index=chunk_index,
            channel=channel,
            start_sec=w_st,
            end_sec=w_en,
            duration_sec=w_dur,
            embedding=emb,
            rms_energy=round(rms, 5),
            bleed_suppressed=False,
            live_match_speaker=m_spk,
            live_match_score=m_sc,
        )

    def _process_window_frame(
        self,
        arr: np.ndarray,
        s_f: int,
        e_f: int,
        sr: int,
        n_ch: int,
        chunk_index: int,
        w_st: float,
        w_en: float,
        w_dur: float,
    ) -> List[StreamingTurnRecord]:
        win_records: List[StreamingTurnRecord] = []
        win_ch0 = arr[s_f:e_f, 0]
        rms0 = float(np.sqrt(np.mean(win_ch0 ** 2)))
        if rms0 >= self.min_active_rms:
            win_records.append(
                self._emit_single_channel_record(win_ch0, sr, 0, chunk_index, w_st, w_en, w_dur, rms0)
            )
        if n_ch < 2:
            return win_records
        win_ch1 = arr[s_f:e_f, 1]
        rms1 = float(np.sqrt(np.mean(win_ch1 ** 2)))
        if rms1 < self.min_active_rms:
            return win_records
        self.learn_hardware_bleed_ratio(win_ch0, win_ch1)
        if is_ch0_to_ch1_bleed(win_ch0, win_ch1, self.min_active_rms, self.bleed_ratio_ceiling):
            self.suppressed_bleed_windows += 1
            return win_records
        win_records.append(
            self._emit_single_channel_record(win_ch1, sr, 1, chunk_index, w_st, w_en, w_dur, rms1)
        )
        return win_records

    def process_chunk_wav(
        self,
        wav_path: Union[str, Path],
        chunk_index: int = 0,
        chunk_start_sec: float = 0.0,
    ) -> List[StreamingTurnRecord]:
        """Segments a chunk WAV per channel, suppresses Ch0->Ch1 bleed, and appends 256-D turns to JSONL."""
        p = Path(wav_path)
        if not p.is_file():
            return []
        arr, sr, n_ch = _read_wav_stereo_or_mono(p)
        n_frames = arr.shape[0]
        win_frames = max(1, int(sr * self.window_sec))
        min_frames = max(1, int(sr * 0.4))

        emitted: List[StreamingTurnRecord] = []
        for s_f in range(0, n_frames, win_frames):
            e_f = min(n_frames, s_f + win_frames)
            if (e_f - s_f) < min_frames:
                continue
            w_st = round(chunk_start_sec + (s_f / float(sr)), 4)
            w_en = round(chunk_start_sec + (e_f / float(sr)), 4)
            w_dur = round(w_en - w_st, 4)
            emitted.extend(
                self._process_window_frame(arr, s_f, e_f, sr, n_ch, chunk_index, w_st, w_en, w_dur)
            )

        if emitted:
            with open(self.output_jsonl_path, "a", encoding="utf-8") as f:
                for rec in emitted:
                    f.write(json.dumps(rec.to_dict()) + "\n")
        return emitted
