"""dual_pipeline/audio_ingestion_core/streaming_diarizer.py

Lock-free circular CoreAudio ring buffer, silence filtering, and streaming
sliding-window speaker diarization for continuous live meeting ingestion.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from dual_pipeline.parameters import get_audio_streaming_parameters

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.streaming_diarizer")

_AUDIO_STREAM_CFG = get_audio_streaming_parameters()
SAMPLE_RATE_HZ = int(_AUDIO_STREAM_CFG["sample_rate_hz"])
DEFAULT_RING_BUFFER_SECONDS = int(_AUDIO_STREAM_CFG["ring_buffer_seconds"])
DEFAULT_WINDOW_SECONDS = float(_AUDIO_STREAM_CFG["diarizer_window_seconds"])
DEFAULT_STEP_SECONDS = float(_AUDIO_STREAM_CFG["diarizer_step_seconds"])
SILENCE_ENERGY_THRESHOLD = float(_AUDIO_STREAM_CFG["silence_energy_threshold"])


@dataclass(frozen=True)
class StreamingSpeakerTurn:
    """Incremental speaker turn hypothesis emitted by streaming diarization."""
    speaker_id: str
    start_s: float
    end_s: float
    confidence: float
    emission_lag_ms: float
    is_final: bool = False


@dataclass
class AudioStreamMetrics:
    """Metrics tracking audio frame ingestion, drops, and silence rejection."""
    total_frames_received: int = 0
    total_frames_dropped: int = 0
    silence_frames_rejected: int = 0
    speech_frames_accepted: int = 0
    max_emission_lag_ms: float = 0.0

    @property
    def drop_ratio(self) -> float:
        total = self.total_frames_received
        return (self.total_frames_dropped / total) if total > 0 else 0.0

    @property
    def silence_rejection_ratio(self) -> float:
        total = self.silence_frames_rejected + self.speech_frames_accepted
        return (self.silence_frames_rejected / total) if total > 0 else 0.0


class LockFreeAudioRingBuffer:
    """Circular ring buffer for CoreAudio HAL stream absorption without dynamic reallocations."""

    def __init__(self, capacity_seconds: int = DEFAULT_RING_BUFFER_SECONDS, sample_rate: int = SAMPLE_RATE_HZ):
        self.capacity_frames = int(capacity_seconds * sample_rate)
        self.sample_rate = sample_rate
        self._buffer = np.zeros(self.capacity_frames, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._available_frames = 0
        self._dropped_frames = 0
        self._lock = threading.Lock()

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def available_frames(self) -> int:
        with self._lock:
            return self._available_frames

    def write_frames(self, frames: np.ndarray) -> int:
        """Writes audio frames into circular buffer. Returns number of frames written."""
        if frames.size == 0:
            return 0
        frames_flat = frames.flatten().astype(np.float32)
        num_frames = frames_flat.size

        with self._lock:
            free_space = self.capacity_frames - self._available_frames
            if num_frames > free_space:
                dropped = num_frames - free_space
                self._dropped_frames += dropped
                writable = free_space
            else:
                writable = num_frames

            if writable <= 0:
                return 0

            to_write = frames_flat[:writable]
            end_pos = self._write_pos + writable
            if end_pos <= self.capacity_frames:
                self._buffer[self._write_pos:end_pos] = to_write
            else:
                first_part = self.capacity_frames - self._write_pos
                self._buffer[self._write_pos:] = to_write[:first_part]
                self._buffer[:writable - first_part] = to_write[first_part:]

            self._write_pos = (self._write_pos + writable) % self.capacity_frames
            self._available_frames += writable
            return writable

    def read_frames(self, num_frames: int) -> np.ndarray:
        """Reads up to num_frames from circular buffer."""
        with self._lock:
            readable = min(num_frames, self._available_frames)
            if readable <= 0:
                return np.empty(0, dtype=np.float32)

            end_pos = self._read_pos + readable
            if end_pos <= self.capacity_frames:
                data = self._buffer[self._read_pos:end_pos].copy()
            else:
                first_part = self.capacity_frames - self._read_pos
                data = np.concatenate([
                    self._buffer[self._read_pos:],
                    self._buffer[:readable - first_part],
                ])

            self._read_pos = (self._read_pos + readable) % self.capacity_frames
            self._available_frames -= readable
            return data


class VADSilenceFilter:
    """Pre-filters non-speech audio chunks to minimize downstream ASR workloads."""

    def __init__(self, energy_threshold: float = SILENCE_ENERGY_THRESHOLD):
        self.energy_threshold = energy_threshold
        self._rms_history: List[float] = []

    def adapt_noise_floor(self, chunk: np.ndarray) -> float:
        """Dynamically calibrates VAD energy threshold from rolling 15th-percentile background noise floor."""
        if chunk.size == 0:
            return self.energy_threshold
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        self._rms_history.append(rms)
        if len(self._rms_history) > 80:
            self._rms_history = self._rms_history[-80:]
        if len(self._rms_history) >= 6:
            noise_floor = float(np.percentile(self._rms_history, 15))
            target = min(0.045, max(0.004, noise_floor * 2.2))
            self.energy_threshold = round(0.80 * self.energy_threshold + 0.20 * target, 5)
        return self.energy_threshold

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Determines whether audio chunk contains active speech energy."""
        if chunk.size == 0:
            return False
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64)**2)))
        return rms >= self.energy_threshold

    def filter_stream(self, frames: np.ndarray, chunk_size: int = 512) -> Tuple[np.ndarray, int, int]:
        """Splits stream into speech and rejected silence frames."""
        total_chunks = len(frames) // chunk_size
        if total_chunks == 0:
            return np.empty(0, dtype=np.float32), 0, 0

        speech_chunks: List[np.ndarray] = []
        silence_count = 0
        speech_count = 0

        for i in range(total_chunks):
            chunk = frames[i * chunk_size : (i + 1) * chunk_size]
            if self.is_speech(chunk):
                speech_chunks.append(chunk)
                speech_count += chunk_size
            else:
                silence_count += chunk_size

        filtered_audio = np.concatenate(speech_chunks) if speech_chunks else np.empty(0, dtype=np.float32)
        return filtered_audio, silence_count, speech_count


class StreamingDiarizer:
    """Sliding-window speaker diarizer pipelined concurrently with speech recognition."""

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        step_seconds: float = DEFAULT_STEP_SECONDS,
        sample_rate: int = SAMPLE_RATE_HZ,
        enrolled_centroids: Optional[Dict[str, List[float]]] = None,
        similarity_threshold: float = 0.75,
    ):
        self.window_frames = int(window_seconds * sample_rate)
        self.step_frames = int(step_seconds * sample_rate)
        self.sample_rate = sample_rate
        self.ring_buffer = LockFreeAudioRingBuffer(capacity_seconds=60, sample_rate=sample_rate)
        self.silence_filter = VADSilenceFilter()
        self.metrics = AudioStreamMetrics()
        self._current_time_s = 0.0
        self._lock = threading.Lock()
        self._emitted_turns: List[StreamingSpeakerTurn] = []
        self.similarity_threshold = similarity_threshold
        self._cluster_centroids: Dict[str, np.ndarray] = {}
        self._cluster_counts: Dict[str, int] = {}
        for spk_name, vec in (enrolled_centroids or {}).items():
            arr = np.asarray(vec, dtype=np.float32).ravel()
            norm = float(np.linalg.norm(arr))
            if arr.size == 256 and norm > 1e-6:
                self._cluster_centroids[spk_name] = arr / norm
                self._cluster_counts[spk_name] = 5

    def _resolve_streaming_speaker(self, speech_audio: np.ndarray) -> Tuple[str, float]:
        from dual_pipeline.live_meeting_recorder_core.acoustic_features import extract_256d_acoustic_embedding
        emb = extract_256d_acoustic_embedding(speech_audio, sr=self.sample_rate)
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-6:
            return "SPEAKER_00", 0.50
        emb_u = emb / norm
        best_spk: Optional[str] = None
        best_sim = -1.0
        for spk_id, c_vec in self._cluster_centroids.items():
            sim = float(np.dot(emb_u, c_vec))
            if sim > best_sim:
                best_sim = sim
                best_spk = spk_id
        if best_spk is not None and best_sim >= self.similarity_threshold:
            cnt = self._cluster_counts.get(best_spk, 1)
            upd = (cnt * self._cluster_centroids[best_spk] + emb_u) / (cnt + 1)
            u_norm = float(np.linalg.norm(upd))
            if u_norm > 1e-6:
                self._cluster_centroids[best_spk] = upd / u_norm
            self._cluster_counts[best_spk] = cnt + 1
            return best_spk, round(min(0.99, max(0.60, best_sim)), 3)
        new_id = f"SPEAKER_{len(self._cluster_centroids):02d}"
        self._cluster_centroids[new_id] = emb_u
        self._cluster_counts[new_id] = 1
        return new_id, 0.90

    def ingest_audio_chunk(self, audio_data: np.ndarray) -> int:
        """Ingests live audio chunk into ring buffer and records arrival metrics."""
        written = self.ring_buffer.write_frames(audio_data)
        with self._lock:
            self.metrics.total_frames_received += audio_data.size
            self.metrics.total_frames_dropped = self.ring_buffer.dropped_frames
        return written

    def process_next_window(self) -> List[StreamingSpeakerTurn]:
        """Extracts next audio window, filters silence, and computes incremental speaker turns."""
        t_start = time.perf_counter()
        raw_audio = self.ring_buffer.read_frames(self.step_frames)
        if raw_audio.size == 0:
            return []

        self.silence_filter.adapt_noise_floor(raw_audio)
        speech_audio, silence_frames, speech_frames = self.silence_filter.filter_stream(raw_audio)
        with self._lock:
            self.metrics.silence_frames_rejected += silence_frames
            self.metrics.speech_frames_accepted += speech_frames

        step_s = raw_audio.size / self.sample_rate
        window_start_s = self._current_time_s
        window_end_s = window_start_s + step_s
        self._current_time_s = window_end_s

        if speech_audio.size == 0:
            return []

        speaker_id, conf = self._resolve_streaming_speaker(speech_audio)
        emission_lag_ms = (time.perf_counter() - t_start) * 1000.0

        with self._lock:
            if emission_lag_ms > self.metrics.max_emission_lag_ms:
                self.metrics.max_emission_lag_ms = emission_lag_ms

        turn = StreamingSpeakerTurn(
            speaker_id=speaker_id,
            start_s=round(window_start_s, 3),
            end_s=round(window_end_s, 3),
            confidence=conf,
            emission_lag_ms=round(emission_lag_ms, 2),
            is_final=True,
        )
        self._emitted_turns.append(turn)
        return [turn]

    def get_emitted_turns(self) -> List[StreamingSpeakerTurn]:
        """Returns all emitted speaker turns."""
        return list(self._emitted_turns)
