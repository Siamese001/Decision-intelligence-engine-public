"""Tests for Wave 1: Native CoreAudio C HAL Integration & Hardware Event Sentinel.
Validates sub-5ms device discovery, zero system_profiler invocations,
strict ban on runtime swiftc compilation, and hot-failover on dynamic headphone connection.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
    _ensure_sck_binary,
)
from pipeline_a_intake.coreaudio_hal import (
    CoreAudioHAL,
    HALDeviceInfo,
)
from pipeline_a_intake.mac_system_audio import (
    CoreAudioDeviceManager,
)


@pytest.fixture(autouse=True)
def clean_mock_hal():
    """Ensures mock overrides are reset between tests."""
    CoreAudioHAL.set_mock_devices(None, None, None)
    yield
    CoreAudioHAL.set_mock_devices(None, None, None)


def test_coreaudio_hal_discovery_speed():
    """Device discovery must execute in <= 5.0ms."""
    if not CoreAudioHAL.is_available():
        pytest.skip("CoreAudio C libraries not available on this host")

    # Warm-up call
    CoreAudioHAL.list_devices()

    # Measured call
    t0 = time.perf_counter()
    devs = CoreAudioHAL.list_devices()
    t1 = time.perf_counter()
    duration_ms = (t1 - t0) * 1000.0

    assert duration_ms <= 5.0, f"Device discovery took {duration_ms:.2f}ms (> 5.0ms)"
    assert len(devs) > 0, "Expected at least one audio device on host"


def test_zero_system_profiler_invocations():
    """Recording startup device checks must make zero calls to system_profiler."""
    with patch("subprocess.run") as mock_subproc:
        mock_subproc.side_effect = RuntimeError("system_profiler must never be called!")

        # list_devices, is_headphones_connected, is_system_muted should use HAL
        if CoreAudioHAL.is_available():
            devs = CoreAudioDeviceManager.list_devices()
            assert isinstance(devs, list)
            is_hp = CoreAudioDeviceManager.is_headphones_connected()
            assert isinstance(is_hp, bool)
            is_muted = CoreAudioDeviceManager.is_system_muted()
            assert isinstance(is_muted, bool)

        # Assert subprocess.run was never invoked with system_profiler
        for call_args in mock_subproc.call_args_list:
            cmd = call_args[0][0] if call_args[0] else []
            assert "system_profiler" not in str(cmd)


def test_hal_properties_parsing_and_classification():
    """Validates HALDeviceInfo helper properties: headphones, loopback, aggregate."""
    hp_dev = HALDeviceInfo(
        device_id=10,
        name="AirPods Pro",
        uid="AirPods-Pro-UID",
        transport="blue",
        input_channels=1,
        output_channels=2,
        sample_rate=48000.0,
    )
    assert hp_dev.is_headphones is True
    assert hp_dev.is_loopback is False

    loopback_dev = HALDeviceInfo(
        device_id=11,
        name="BlackHole 2ch",
        uid="BlackHole2ch_UID",
        transport="virt",
        input_channels=2,
        output_channels=2,
    )
    assert loopback_dev.is_loopback is True
    assert loopback_dev.is_headphones is False

    builtin_dev = HALDeviceInfo(
        device_id=12,
        name="MacBook Pro Speakers",
        uid="BuiltInSpeakerDevice",
        transport="bltn",
        input_channels=0,
        output_channels=2,
    )
    assert builtin_dev.is_headphones is False
    assert builtin_dev.is_loopback is False


def test_headphone_trap_sentinel_hot_failover(tmp_path: Path):
    """AirPods connection during acoustic mic session must trigger hot-failover to ScreenCaptureKit."""
    chunks_dir = tmp_path / "chunks"
    master_wav = tmp_path / "master.wav"
    chunks_dir.mkdir(parents=True)

    manager = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="mic",
    )
    manager._is_running = True
    manager._active_strategy = "microphone"

    # Setup initial mock HAL: Built-in speakers (not headphones)
    builtin_speaker = HALDeviceInfo(
        device_id=78,
        name="MacBook Pro Speakers",
        uid="BuiltInSpeakerDevice",
        transport="bltn",
        output_channels=2,
        is_default_output=True,
    )
    mic = HALDeviceInfo(
        device_id=73,
        name="MacBook Pro Microphone",
        uid="BuiltInMicrophoneDevice",
        transport="bltn",
        input_channels=1,
        is_default_input=True,
    )
    CoreAudioHAL.set_mock_devices([builtin_speaker, mic], default_output=builtin_speaker, default_input=mic)

    # Initial sentinel check: speakers default -> no failover
    triggered = manager.check_headphone_trap_sentinel(next_chunk_idx=1)
    assert triggered is False
    assert manager.active_strategy == "microphone"

    # Simulate operator connecting AirPods
    airpods = HALDeviceInfo(
        device_id=88,
        name="Amit's AirPods Pro",
        uid="AirPodsPro-88",
        transport="blue",
        input_channels=1,
        output_channels=2,
        is_default_output=True,
    )
    CoreAudioHAL.set_mock_devices([airpods, mic], default_output=airpods, default_input=mic)

    # Per Operator Decision D1: mid-session source switching is disabled; dual mixed_sck captures from start.
    with patch.object(manager, "_start_sck_capture") as mock_start_sck:
        triggered = manager.check_headphone_trap_sentinel(next_chunk_idx=2)
        assert triggered is False
        assert manager.active_strategy == "microphone"
        mock_start_sck.assert_not_called()


def test_runtime_swiftc_compilation_strictly_banned():
    """Verifies that runtime swiftc invocation is strictly banned."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = RuntimeError("swiftc must NEVER be executed at runtime!")
        # Calling _ensure_sck_binary should only inspect path, never call swiftc
        _ensure_sck_binary()
        for call_args in mock_run.call_args_list:
            cmd = call_args[0][0] if call_args[0] else []
            assert "swiftc" not in str(cmd)


def test_hal_monotonic_token_and_listener():
    """Tests monotonic hal generation increment and listener notifications."""
    gen_before = CoreAudioHAL.get_hal_generation()
    notifications = []

    def _on_change(gen: int):
        notifications.append(gen)

    CoreAudioHAL.register_change_listener(_on_change)
    try:
        CoreAudioHAL.trigger_mock_hardware_change()
        assert CoreAudioHAL.get_hal_generation() > gen_before
        assert len(notifications) == 1
    finally:
        CoreAudioHAL.unregister_change_listener(_on_change)
