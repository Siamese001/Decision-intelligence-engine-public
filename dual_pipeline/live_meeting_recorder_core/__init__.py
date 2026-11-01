"""live_meeting_recorder_core package with lazy attribute resolution (PEP 562).

Prevents eager loading of the copilot, MLX/Whisper, and retrieval stack when
importing lightweight recording, spooling, or utility submodules.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_IMPORTS_CORE: dict[str, tuple[str, str]] = {
    # symbol -> (relative_submodule, attribute_name)
    "ASRDecodeConfig": ("..asr_decode_config", "ASRDecodeConfig"),
    "AggregatedTurn": (".stream_aggregator", "AggregatedTurn"),
    "ConversationalTurnAggregator": (".stream_aggregator", "ConversationalTurnAggregator"),
    "coalesce_speaker_turns": (".stream_aggregator", "coalesce_speaker_turns"),
    "coalesce_transcript_markdown_turns": (".stream_aggregator", "coalesce_transcript_markdown_turns"),
    "join_speech_fragments": (".stream_aggregator", "join_speech_fragments"),
    "AudioCaptureUnprovenError": (".audio_capture_proof", "AudioCaptureUnprovenError"),
    "AudioReasonCode": (".audio_capture_proof", "AudioReasonCode"),
    "EXIT_AUDIO_UNPROVEN": (".audio_capture_proof", "EXIT_AUDIO_UNPROVEN"),
    "EXIT_MLX_UNREADY": (".mlx_worker", "EXIT_MLX_UNREADY"),
    "MLXInitError": (".mlx_worker", "MLXInitError"),
    "MLXReasonCode": (".mlx_worker", "MLXReasonCode"),
    "AudioVADSegmenter": (".audio_vad", "AudioVADSegmenter"),
    "AudioMasterStitcher": (".audio_master_stitcher", "AudioMasterStitcher"),
    "StitchReceipt": (".audio_master_stitcher", "StitchReceipt"),
    "AudioSubprocessWatchdog": (".audio_watchdog", "AudioSubprocessWatchdog"),
    "AudioEnergyWatchdog": (".audio_watchdog", "AudioEnergyWatchdog"),
    "ChunkHealth": (".chunk_health", "ChunkHealth"),
    "InstantAudioSpooler": (".instant_spooler", "InstantAudioSpooler"),
    "SessionRegistry": (".session_registry", "SessionRegistry"),
    "CopilotGraphRetriever": (".copilot_retriever", "CopilotGraphRetriever"),
    "prefetch_session_packet": (".copilot_retriever", "prefetch_session_packet"),
    "HardwareDeviceClassifier": (".hardware_classifier", "HardwareDeviceClassifier"),
    "CopilotPrefetchBridge": (".prefetch_bridge", "CopilotPrefetchBridge"),
    "SessionContextPacket": (".session_context_packet", "SessionContextPacket"),
    "build_prefetch_query_specs": (".prefetch_query_builder", "build_prefetch_query_specs"),
    "CopilotRequestSnapshot": (".copilot_lifecycle", "CopilotRequestSnapshot"),
    "CopilotEpisode": (".copilot_lifecycle", "CopilotEpisode"),
    "EpisodeTracker": (".copilot_lifecycle", "EpisodeTracker"),
    "GroundedContextCache": (".copilot_lifecycle", "GroundedContextCache"),
    "CopilotDeliberationQueue": (".copilot_lifecycle", "CopilotDeliberationQueue"),
    "CopilotAnswerLedgerAdapter": (".copilot_lifecycle", "CopilotAnswerLedgerAdapter"),
    "ensure_inferred_memory_boundary": (".copilot_lifecycle", "ensure_inferred_memory_boundary"),
}

_LAZY_IMPORTS_EXT: dict[str, tuple[str, str]] = {
    "InflectionDetector": (".inflection_detector", "InflectionDetector"),
    "InflectionEvent": (".inflection_detector", "InflectionEvent"),
    "InflectionTriggerType": (".inflection_detector", "InflectionTriggerType"),
    "IsolatedMLXTranscriberClient": (".isolated_transcriber", "IsolatedMLXTranscriberClient"),
    "LiveAudioCaptureManager": (".audio_capture", "LiveAudioCaptureManager"),
    "LiveCopilotAdvisor": (".copilot", "LiveCopilotAdvisor"),
    "RecoveryResult": (".disaster_recovery", "RecoveryResult"),
    "SessionRecoveryManager": (".disaster_recovery", "SessionRecoveryManager"),
    "recover_live_session": (".disaster_recovery", "recover_live_session"),
    "append_live_transcript_line": (".audio_utils", "append_live_transcript_line"),
    "append_live_transcript_pause": (".audio_utils", "append_live_transcript_pause"),
    "format_chunk_time": (".audio_utils", "format_chunk_time"),
    "format_hh_mm_ss": (".audio_utils", "format_hh_mm_ss"),
    "get_audio_rms": (".audio_utils", "get_audio_rms"),
    "init_live_transcript_file": (".audio_utils", "init_live_transcript_file"),
    "load_processed_chunk_files": (".audio_utils", "load_processed_chunk_files"),
    "parse_attendees": (".attendees", "parse_attendees"),
    "resolve_chunk_startup_state": (".audio_utils", "resolve_chunk_startup_state"),
    "seal_live_transcript_footer": (".audio_utils", "seal_live_transcript_footer"),
    "transcribe_meeting_chunk": (".chunk_transcriber", "transcribe_meeting_chunk"),
    "transcribe_meeting_chunk_with_segments": (".chunk_transcriber", "transcribe_meeting_chunk_with_segments"),
    "write_atomic_session_manifest": (".audio_utils", "write_atomic_session_manifest"),
    "LiveVoiceRegistry": (".live_voice_registry", "LiveVoiceRegistry"),
    "VoiceCluster": (".live_voice_registry", "VoiceCluster"),
    "SpeakerHintManager": (".speaker_hints", "SpeakerHintManager"),
    "SpeakerHintRecord": (".speaker_hints", "SpeakerHintRecord"),
    "HintResult": (".speaker_hints", "HintResult"),
}

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {**_LAZY_IMPORTS_CORE, **_LAZY_IMPORTS_EXT}

__all__ = sorted(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        submod_name, attr_name = _LAZY_IMPORTS[name]
        mod = importlib.import_module(submod_name, package=__name__)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(__all__)
