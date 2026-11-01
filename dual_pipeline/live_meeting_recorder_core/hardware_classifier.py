"""Hardware Device Classifier for Live Audio Capture on macOS.

Distinguishes physical streaming microphones from BLE beacons (Find My, AirTags),
non-streaming virtual devices, and loopback endpoints.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Tuple, Union

from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.hardware_classifier")

NON_STREAMING_DEVICE_KEYWORDS: Tuple[str, ...] = (
    "find my",
    "findmy",
    "beacon",
    "tracker",
    "locator",
    "airtag",
    "tile",
)

BUILTIN_MIC_KEYWORDS: Tuple[str, ...] = (
    "macbook pro microphone",
    "built-in microphone",
    "internal microphone",
    "macbook microphone",
    "builtin microphone",
)


class HardwareDeviceClassifier:
    """Classifies audio devices to distinguish physical streaming microphones
    from non-streaming virtual endpoints, BLE beacons (Find My, AirTags),
    and aggregate/loopback devices.
    """

    @classmethod
    def is_ble_or_non_streaming(cls, target: Union[str, AudioDeviceInfo, Any]) -> bool:
        """Determines whether a device is a non-streaming BLE beacon or virtual tracker."""
        if target is None:
            return False
        if isinstance(target, str):
            clean_str = target.lower().strip()
            return any(k in clean_str for k in NON_STREAMING_DEVICE_KEYWORDS)

        name = getattr(target, "name", "")
        if name and cls.is_ble_or_non_streaming(name):
            return True

        if getattr(target, "is_non_streaming_virtual", False):
            return True

        # Input devices with 0 input channels cannot stream audio
        dev_type = getattr(target, "device_type", None)
        in_ch = getattr(target, "input_channels", 0)
        return bool(dev_type == AudioDeviceType.INPUT and in_ch <= 0)

    @classmethod
    def is_physical_microphone(cls, device: AudioDeviceInfo) -> bool:
        """Returns True if the device is a genuine physical input endpoint capable of streaming."""
        if not isinstance(device, AudioDeviceInfo):
            return False
        if cls.is_ble_or_non_streaming(device):
            return False
        if device.is_loopback:
            return False
        if device.input_channels <= 0:
            return False
        return device.device_type in (AudioDeviceType.INPUT, AudioDeviceType.HEADSET)

    @classmethod
    def filter_physical_microphones(cls, devices: List[AudioDeviceInfo]) -> List[AudioDeviceInfo]:
        """Filters a device list, returning only valid physical streaming microphones."""
        return [d for d in devices if cls.is_physical_microphone(d)]

    @classmethod
    def _match_preferred_microphone(
        cls,
        devices: List[AudioDeviceInfo],
        physical_devices: List[AudioDeviceInfo],
        preferred_index: str,
    ) -> Optional[AudioDeviceInfo]:
        clean_idx = preferred_index.replace(":", "").strip()
        try:
            target_avf_idx = int(clean_idx)
        except ValueError:
            return None

        for d in physical_devices:
            if d.avfoundation_index == target_avf_idx:
                return d

        for d in devices:
            if d.avfoundation_index == target_avf_idx and cls.is_ble_or_non_streaming(d):
                logger.warning(
                    "[HARDWARE-CLASSIFIER] Preferred device at index %s (%s) is a non-streaming BLE beacon! "
                    "Explicitly rejecting and selecting built-in physical microphone.",
                    preferred_index,
                    d.name,
                )
        return None

    @classmethod
    def _find_builtin_microphone(cls, physical_devices: List[AudioDeviceInfo]) -> Optional[AudioDeviceInfo]:
        for d in physical_devices:
            name_lower = d.name.lower()
            if any(k in name_lower for k in BUILTIN_MIC_KEYWORDS) or getattr(d, "transport", "") == "builtin":
                return d
        return None

    @classmethod
    def select_default_microphone(
        cls,
        devices: List[AudioDeviceInfo],
        preferred_index: Optional[str] = None,
        prefer_builtin_mic: bool = False,
    ) -> Optional[AudioDeviceInfo]:
        """Selects the best physical streaming microphone, strictly rejecting BLE beacons.
        If prefer_builtin_mic=True, built-in Mac microphone is selected over Bluetooth headsets (AirPods).
        """
        physical_devices = cls.filter_physical_microphones(devices)
        if not physical_devices:
            return None

        if preferred_index:
            matched = cls._match_preferred_microphone(devices, physical_devices, preferred_index)
            if matched:
                return matched

        builtin = cls._find_builtin_microphone(physical_devices)
        if prefer_builtin_mic and builtin:
            return builtin

        for d in physical_devices:
            if d.is_default_input and not (prefer_builtin_mic and getattr(d, "is_headphones", False)):
                return d

        if builtin:
            return builtin

        return physical_devices[0]

    @classmethod
    def _is_preferred_device_rejected(
        cls,
        clean_pref: str,
        devices: List[AudioDeviceInfo],
        prefer_builtin_mic: bool,
    ) -> bool:
        """Determines if the preferred index is a BLE beacon or headphone when prefer_builtin_mic."""
        try:
            target_idx = int(clean_pref)
        except ValueError:
            return False

        for d in devices:
            if d.avfoundation_index != target_idx:
                continue
            if cls.is_ble_or_non_streaming(d):
                logger.warning(
                    "[HARDWARE-CLASSIFIER-REJECT] Index :%d matches BLE beacon '%s'. Rejecting :%d.",
                    target_idx,
                    d.name,
                    target_idx,
                )
                return True
            if prefer_builtin_mic and getattr(d, "is_headphones", False):
                logger.info(
                    "[HARDWARE-CLASSIFIER-BYPASS] Bypassing Bluetooth headset '%s' at :%d in favor of built-in mic.",
                    d.name,
                    target_idx,
                )
                return True
            break
        return False

    @classmethod
    def resolve_safe_device_index(
        cls,
        preferred_index: str = ":0",
        devices: Optional[List[AudioDeviceInfo]] = None,
        prefer_builtin_mic: bool = False,
    ) -> str:
        """Resolves a safe AVFoundation index (:N).

        If preferred_index is :0 and points to a BLE device or non-streaming endpoint,
        returns the index of the built-in microphone (defaulting to :1).
        If prefer_builtin_mic is True, prioritizes the built-in microphone over AirPods.
        """
        if devices is None:
            from pipeline_a_intake.mac_system_audio import CoreAudioDeviceManager
            devices = CoreAudioDeviceManager.list_devices()

        clean_pref = preferred_index.replace(":", "").strip()
        is_pref_ble = cls._is_preferred_device_rejected(clean_pref, devices, prefer_builtin_mic)

        if not is_pref_ble and clean_pref != "0":
            return f":{clean_pref}"

        best_mic = cls.select_default_microphone(devices, prefer_builtin_mic=prefer_builtin_mic)
        if best_mic and best_mic.avfoundation_index is not None:
            return f":{best_mic.avfoundation_index}"

        # If best mic was not indexed, search for any physical device with an index
        for d in cls.filter_physical_microphones(devices):
            if d.avfoundation_index is not None:
                return f":{d.avfoundation_index}"

        return ":1"

    @classmethod
    def parse_avfoundation_device_lines(cls, lines: List[str]) -> List[Tuple[int, str, bool]]:
        """Parses lines from `ffmpeg -list_devices true`.

        Returns list of (index, name, is_valid_physical_mic).
        """
        results: List[Tuple[int, str, bool]] = []
        in_audio_section = False
        for line in lines:
            if "AVFoundation audio devices:" in line:
                in_audio_section = True
                continue
            if not in_audio_section or not line.strip():
                continue
            m = re.search(r"\[(\d+)\]\s+(.*)", line)
            if not m:
                continue
            idx = int(m.group(1))
            dev_name = m.group(2).strip()
            is_ble = cls.is_ble_or_non_streaming(dev_name)
            results.append((idx, dev_name, not is_ble))
        return results
