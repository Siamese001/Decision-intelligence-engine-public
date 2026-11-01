"""Audio Context Windowing and Sample Coordinate Mapping.

Decouples raw audio capture file cadence from ASR model inference context.
Provides exact sample-level coordinate mappings, piecewise sample accounting,
multi-channel topology verification, and bounded window management.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from dual_pipeline.parameters import get_audio_streaming_parameters

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.context_windows")

_AUDIO_CTX_CFG = get_audio_streaming_parameters()
DEFAULT_MAX_WINDOW_SEC = float(_AUDIO_CTX_CFG["max_window_sec"])
DEFAULT_MIN_WINDOW_SEC = float(_AUDIO_CTX_CFG["min_window_sec"])
DEFAULT_SAMPLE_RATE = int(_AUDIO_CTX_CFG["sample_rate_hz"])


@dataclass
class AudioContextWindow:
    """Represents a bounded recognition window mapped to exact master audio coordinates."""
    window_id: str
    start_sample: int
    end_sample: int
    channel: int
    sample_rate: int = DEFAULT_SAMPLE_RATE
    global_start_sec: float = 0.0
    global_end_sec: float = 0.0
    is_padded: bool = False
    pad_samples_start: int = 0
    pad_samples_end: int = 0
    source_spans: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def duration_sec(self) -> float:
        return (self.end_sample - self.start_sample) / self.sample_rate

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample

    def local_sample_to_global_time(self, local_sample: int) -> float:
        """Translates a sample index within this window to the master timeline in seconds."""
        if local_sample < 0 or local_sample > self.sample_count:
            raise ValueError(
                f"local_sample {local_sample} out of bounds [0, {self.sample_count}] for window {self.window_id}"
            )
        abs_sample = self.start_sample + local_sample
        return round(abs_sample / self.sample_rate, 4)

    def to_dict(self) -> dict:
        return {
            "window_id": self.window_id,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "channel": self.channel,
            "sample_rate": self.sample_rate,
            "global_start_sec": self.global_start_sec,
            "global_end_sec": self.global_end_sec,
            "duration_sec": round(self.duration_sec, 4),
            "is_padded": self.is_padded,
            "source_spans": self.source_spans,
        }


class ContextWindowManager:
    """Partitions audio into bounded recognition windows respecting model and sample limits."""

    def __init__(
        self,
        max_window_sec: float = DEFAULT_MAX_WINDOW_SEC,
        min_window_sec: float = DEFAULT_MIN_WINDOW_SEC,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        max_gap_sec: float = 1.5,
    ):
        self.max_window_sec = max_window_sec
        self.min_window_sec = min_window_sec
        self.sample_rate = sample_rate
        self.max_gap_sec = max_gap_sec

    def validate_topology(self, window: AudioContextWindow, total_samples: int) -> None:
        """Validates that a window satisfies physical channel and bounds constraints."""
        if window.channel not in (0, 1):
            raise ValueError(f"Invalid channel index {window.channel}; must be 0 (local) or 1 (remote)")
        if window.start_sample < 0:
            raise ValueError(f"Negative start_sample: {window.start_sample}")
        if window.end_sample <= window.start_sample:
            raise ValueError(
                f"Invalid sample range: end_sample ({window.end_sample}) <= start_sample ({window.start_sample})"
            )
        if window.end_sample > total_samples:
            raise ValueError(
                f"end_sample {window.end_sample} exceeds total audio samples {total_samples}"
            )
        if window.duration_sec > self.max_window_sec + 0.05:  # small float tolerance
            raise ValueError(
                f"Window duration {window.duration_sec:.2f}s exceeds limit {self.max_window_sec}s"
            )

    def partition_speech_intervals(
        self,
        total_samples: int,
        speech_intervals_sec: List[Tuple[float, float]],
        channel: int = 0,
    ) -> List[AudioContextWindow]:
        """Partitions speech intervals into bounded windows without exceeding max_window_sec."""
        if total_samples <= 0:
            return []
        if not speech_intervals_sec:
            # Silence case: no speech intervals, emit single bounded silence window or empty
            return []

        windows: List[AudioContextWindow] = []
        cur_spans: List[Tuple[int, int]] = []
        cur_start_sec = speech_intervals_sec[0][0]
        cur_end_sec = speech_intervals_sec[0][1]

        for start_s, end_s in speech_intervals_sec:
            s_samp = int(start_s * self.sample_rate)
            e_samp = int(end_s * self.sample_rate)

            # Check if adding this span exceeds max_window_sec
            proposed_duration = end_s - cur_start_sec
            gap_from_prev = start_s - cur_end_sec

            if cur_spans and (proposed_duration > self.max_window_sec or gap_from_prev > self.max_gap_sec):
                # Emit current window
                w_start_samp = int(cur_start_sec * self.sample_rate)
                w_end_samp = int(cur_end_sec * self.sample_rate)
                w = AudioContextWindow(
                    window_id=f"win_ch{channel}_{len(windows):04d}",
                    start_sample=max(0, w_start_samp),
                    end_sample=min(total_samples, w_end_samp),
                    channel=channel,
                    sample_rate=self.sample_rate,
                    global_start_sec=round(cur_start_sec, 4),
                    global_end_sec=round(cur_end_sec, 4),
                    source_spans=list(cur_spans),
                )
                self.validate_topology(w, total_samples)
                windows.append(w)
                cur_spans = []
                cur_start_sec = start_s

            cur_spans.append((s_samp, e_samp))
            cur_end_sec = end_s

        if cur_spans:
            w_start_samp = int(cur_start_sec * self.sample_rate)
            w_end_samp = int(cur_end_sec * self.sample_rate)
            w = AudioContextWindow(
                window_id=f"win_ch{channel}_{len(windows):04d}",
                start_sample=max(0, w_start_samp),
                end_sample=min(total_samples, w_end_samp),
                channel=channel,
                sample_rate=self.sample_rate,
                global_start_sec=round(cur_start_sec, 4),
                global_end_sec=round(cur_end_sec, 4),
                source_spans=list(cur_spans),
            )
            self.validate_topology(w, total_samples)
            windows.append(w)

        return windows
