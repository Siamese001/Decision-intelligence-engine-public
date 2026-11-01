"""Wave 2 Unit Tests: Audio Context Windows and Sample Coordinate Mapping.

Verifies:
1. Silence Handling: Zero samples or empty speech intervals produce empty window lists.
2. Brief Speech Preservation: Short speech turns (0.2s–0.5s) are preserved intact.
3. Maximum Window Enforcement: Windows never exceed configured limit (30.0s).
4. Seam & Sample Mapping: local_sample_to_global_time maps exact master timeline seconds.
5. Multi-Channel Topology: Physical separation of Channel 0 and Channel 1 is preserved.
6. Invalid Topology Rejection: Negative bounds, inverted ranges, and out-of-bounds channels fail closed.
"""
from __future__ import annotations

import pytest
from dual_pipeline.audio_ingestion_core.context_windows import (
    AudioContextWindow,
    ContextWindowManager,
)


def test_silence_handling():
    mgr = ContextWindowManager(max_window_sec=30.0, sample_rate=16000)
    # Zero samples
    assert mgr.partition_speech_intervals(0, []) == []
    # Silence with positive samples
    assert mgr.partition_speech_intervals(160000, []) == []


def test_brief_speech_island_preservation():
    mgr = ContextWindowManager(max_window_sec=30.0, min_window_sec=0.2, sample_rate=16000)
    total_samples = 16000 * 60  # 1 minute

    # Single brief interjection: "Yeah." (0.3s) at 5.0s
    intervals = [(5.0, 5.3)]
    windows = mgr.partition_speech_intervals(total_samples, intervals, channel=0)

    assert len(windows) == 1
    w = windows[0]
    assert w.channel == 0
    assert w.global_start_sec == 5.0
    assert w.global_end_sec == 5.3
    assert pytest.approx(w.duration_sec, 0.01) == 0.3
    assert w.start_sample == int(5.0 * 16000)
    assert w.end_sample == int(5.3 * 16000)


def test_maximum_window_limit():
    mgr = ContextWindowManager(max_window_sec=30.0, sample_rate=16000, max_gap_sec=1.5)
    total_samples = 16000 * 120

    # Long monologue: 70 seconds of contiguous speech
    intervals = [
        (0.0, 20.0),
        (20.5, 45.0),
        (45.2, 70.0),
    ]
    windows = mgr.partition_speech_intervals(total_samples, intervals, channel=1)

    assert len(windows) >= 2
    for w in windows:
        assert w.duration_sec <= 30.05, f"Window {w.window_id} duration {w.duration_sec} exceeds 30.0s limit"
        assert w.channel == 1


def test_seam_and_sample_mapping():
    w = AudioContextWindow(
        window_id="test_win_01",
        start_sample=32000,  # 2.0s
        end_sample=64000,    # 4.0s
        channel=0,
        sample_rate=16000,
        global_start_sec=2.0,
        global_end_sec=4.0,
    )

    # Offset 0 samples -> global 2.0s
    assert w.local_sample_to_global_time(0) == 2.0
    # Offset 16000 samples (1.0s) -> global 3.0s
    assert w.local_sample_to_global_time(16000) == 3.0
    # Offset 32000 samples (2.0s) -> global 4.0s
    assert w.local_sample_to_global_time(32000) == 4.0

    # Out of bounds sample offset raises ValueError
    with pytest.raises(ValueError):
        w.local_sample_to_global_time(32001)
    with pytest.raises(ValueError):
        w.local_sample_to_global_time(-1)


def test_multi_channel_topology_validation():
    mgr = ContextWindowManager(max_window_sec=30.0, sample_rate=16000)
    total_samples = 16000 * 10

    # Valid Channel 0 window
    w0 = AudioContextWindow(
        window_id="win_ch0",
        start_sample=0,
        end_sample=16000,
        channel=0,
        sample_rate=16000,
    )
    mgr.validate_topology(w0, total_samples)

    # Valid Channel 1 window
    w1 = AudioContextWindow(
        window_id="win_ch1",
        start_sample=16000,
        end_sample=32000,
        channel=1,
        sample_rate=16000,
    )
    mgr.validate_topology(w1, total_samples)

    # Invalid channel 2
    w_bad_ch = AudioContextWindow(
        window_id="win_bad_ch",
        start_sample=0,
        end_sample=16000,
        channel=2,
        sample_rate=16000,
    )
    with pytest.raises(ValueError, match="Invalid channel index"):
        mgr.validate_topology(w_bad_ch, total_samples)


def test_invalid_topology_rejection():
    mgr = ContextWindowManager(max_window_sec=30.0, sample_rate=16000)
    total_samples = 16000 * 10

    # Negative start sample
    w_neg = AudioContextWindow(
        window_id="win_neg",
        start_sample=-100,
        end_sample=16000,
        channel=0,
    )
    with pytest.raises(ValueError, match="Negative start_sample"):
        mgr.validate_topology(w_neg, total_samples)

    # Inverted range: end <= start
    w_inv = AudioContextWindow(
        window_id="win_inv",
        start_sample=16000,
        end_sample=16000,
        channel=0,
    )
    with pytest.raises(ValueError, match="end_sample .* <= start_sample"):
        mgr.validate_topology(w_inv, total_samples)

    # Exceeding total samples
    w_overflow = AudioContextWindow(
        window_id="win_overflow",
        start_sample=0,
        end_sample=total_samples + 5000,
        channel=0,
    )
    with pytest.raises(ValueError, match="exceeds total audio samples"):
        mgr.validate_topology(w_overflow, total_samples)
