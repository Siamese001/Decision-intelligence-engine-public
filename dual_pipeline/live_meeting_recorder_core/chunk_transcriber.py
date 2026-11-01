"""Live Meeting Recorder Core: Hardware-Accelerated Local MLX Chunk Transcriber.
Target: Apple MacBook Pro (Apple Silicon Metal GPU).

Supports:
  1. Local MLX Metal GPU Whisper transcription (sub-300ms, zero cloud egress).
  2. Anchored anti-hallucination filtering, repetition loop suppression, and silence gating via audio_postprocess.
  3. Shared decode configuration via asr_decode_config.
  4. Immutable drop logging to asr_filter_log.jsonl.
  5. Strict fail-closed policy: cloud transcription of raw chunks is prohibited.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
from dual_pipeline.audio_postprocess import (
    ASR_HALLUCINATION_BLOCKLIST,
    filter_segments_by_telemetry,
    is_prompt_echo,
    log_asr_drop,
    sanitize_transcript_text,
    suppress_repetition_loops,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.chunk_transcriber")

_is_prompt_echo = is_prompt_echo
_suppress_repetition_loops = suppress_repetition_loops
HALLUCINATIONS = ASR_HALLUCINATION_BLOCKLIST



def filter_segments_and_collect_valid(
    segments: List[Dict[str, Any]],
    decode_config: Optional[Any] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Evaluates Whisper segment telemetry and returns concatenated text and valid segments."""
    no_speech_thresh = getattr(decode_config, "no_speech_threshold", 0.60)
    comp_ratio_thresh = getattr(decode_config, "compression_ratio_threshold", 2.40)
    logprob_thresh = getattr(decode_config, "logprob_threshold", -1.0)

    valid_texts: List[str] = []
    valid_segments: List[Dict[str, Any]] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        no_speech = seg.get("no_speech_prob", 0.0)
        avg_logprob = seg.get("avg_logprob", 0.0)
        comp_ratio = seg.get("compression_ratio", 1.0)

        if no_speech >= no_speech_thresh:
            logger.debug("Rejected segment with no_speech_prob=%.2f: %s", no_speech, text)
            log_asr_drop(
                filter_log_path,
                text,
                "HIGH_NO_SPEECH_PROB",
                pattern=f"no_speech_prob={no_speech:.2f}",
                chunk_idx=chunk_idx,
                chunk_file=chunk_file,
            )
            continue
        if comp_ratio > comp_ratio_thresh:
            logger.debug("Rejected repetition loop segment (comp_ratio=%.2f): %s", comp_ratio, text)
            log_asr_drop(
                filter_log_path,
                text,
                "HIGH_COMPRESSION_RATIO",
                pattern=f"comp_ratio={comp_ratio:.2f}",
                chunk_idx=chunk_idx,
                chunk_file=chunk_file,
            )
            continue
        if avg_logprob < logprob_thresh:
            logger.debug("Rejected low confidence segment (avg_logprob=%.2f): %s", avg_logprob, text)
            log_asr_drop(
                filter_log_path,
                text,
                "LOW_LOGPROB",
                pattern=f"avg_logprob={avg_logprob:.2f}",
                chunk_idx=chunk_idx,
                chunk_file=chunk_file,
            )
            continue
        valid_texts.append(text)
        valid_segments.append(seg)
    return " ".join(valid_texts).strip(), valid_segments


def extract_chunk_text_and_words(
    trans_res: Any,
    start_sec: float,
) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    """Extracts text and time-offset word list from chunk transcription result."""
    if not isinstance(trans_res, tuple):
        return trans_res, None
    text, raw_segs = trans_res
    if not raw_segs:
        return text, None
    chunk_words: List[Dict[str, Any]] = []
    for s in raw_segs:
        if isinstance(s, dict) and s.get("words"):
            for w in s["words"]:
                chunk_words.append({
                    "word": str(w.get("word", "")).strip(),
                    "start": round(start_sec + float(w.get("start", 0.0)), 3),
                    "end": round(start_sec + float(w.get("end", 0.0)), 3),
                    "probability": round(float(w.get("probability", 1.0)), 3),
                })
    return text, chunk_words or None


def extract_chunk_telemetry(trans_res: Any) -> Tuple[float, float]:
    """Extracts (asr_conf, no_speech_prob) from chunk transcription result."""
    import math

    if not isinstance(trans_res, tuple) or len(trans_res) < 2 or not trans_res[1]:
        return 1.0, 0.0
    segs = [s for s in trans_res[1] if isinstance(s, dict)]
    if not segs:
        return 1.0, 0.0
    no_speech = max(float(s.get("no_speech_prob", 0.0)) for s in segs)
    avg_lp = sum(float(s.get("avg_logprob", -0.2)) for s in segs) / len(segs)
    asr_conf = round(max(0.0, min(1.0, math.exp(avg_lp))), 3)
    return asr_conf, round(no_speech, 4)


def _filter_hallucinations(
    text: str,
    whisper_prompt: Optional[str] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
) -> str:
    """Backward-compatible adapter delegating to audio_postprocess.sanitize_transcript_text."""
    return sanitize_transcript_text(
        text,
        whisper_prompt=whisper_prompt,
        filter_log_path=filter_log_path,
        chunk_idx=chunk_idx,
        chunk_file=chunk_file,
    )


def _filter_segment_metadata(segments: list[dict[str, Any]]) -> str:
    """Backward-compatible adapter delegating to audio_postprocess.filter_segments_by_telemetry."""
    return filter_segments_by_telemetry(segments, decode_config=DEFAULT_ASR_DECODE_CONFIG)



def _resolve_effective_backend(backend: str) -> str:
    effective = backend.lower().strip()
    if effective in ("openai", "cloud"):
        raise ValueError(
            "OpenAI cloud audio transcription for raw chunks is strictly prohibited by repository governance. "
            "All live meeting audio must be transcribed locally on-premise using MLX Whisper ('mlx')."
        )
    if effective in ("auto", "mlx"):
        repo_root = Path(__file__).resolve().parent.parent.parent
        venv_bin = str(repo_root / ".venv" / "bin")
        CredentialManager.prepend_path(venv_bin)

        has_ffmpeg = bool(shutil.which("ffmpeg") or (repo_root / ".venv" / "bin" / "ffmpeg").exists())
        if not has_ffmpeg:
            raise RuntimeError(
                "Local FFmpeg executable not found in PATH or .venv/bin. "
                "Cannot transcribe local audio. Cloud fallback is strictly prohibited."
            )
        try:
            import mlx.core as mx
            if not mx.metal.is_available():
                raise RuntimeError("Apple Silicon Metal acceleration is unavailable for MLX Whisper.")
        except ImportError as e:
            raise RuntimeError(f"mlx is not installed in Python environment: {e}") from e
        return "mlx"
    return effective


def _tag_segments_with_channel(
    valid_segments: list[dict[str, Any]], channel_idx: int, speaker_label: Optional[str]
) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    spk_meta = {"speaker": speaker_label} if speaker_label else {}
    for s in valid_segments:
        sc = {**s, "channel": channel_idx, **spk_meta}
        if isinstance(sc.get("words"), list):
            sc["words"] = [
                {**w, "channel": channel_idx, **spk_meta}
                for w in sc["words"]
                if isinstance(w, dict)
            ]
        tagged.append(sc)
    return tagged


def _transcribe_single_mono_wav_mlx(
    wav_path: Path,
    mlx_whisper: Any,
    kwargs: Dict[str, Any],
    whisper_prompt: Optional[str],
    filter_log_path: Optional[Path | str],
    chunk_idx: Optional[int],
    channel_idx: Optional[int] = None,
    speaker_label: Optional[str] = None,
) -> Tuple[str, list[dict[str, Any]]]:
    res = mlx_whisper.transcribe(str(wav_path), **kwargs)
    segments = res.get("segments", [])
    valid_segments: list[dict[str, Any]] = []
    if segments:
        text, valid_segments = filter_segments_and_collect_valid(
            segments,
            decode_config=DEFAULT_ASR_DECODE_CONFIG,
            filter_log_path=filter_log_path,
            chunk_idx=chunk_idx,
            chunk_file=wav_path.name,
        )
    else:
        text = res.get("text", "")
    has_vad = any(
        isinstance(s, dict) and float(s.get("no_speech_prob", 0.5)) < 0.25 and float(s.get("avg_logprob", -0.2)) > -0.65
        for s in valid_segments
    )
    cleaned_text = sanitize_transcript_text(
        text,
        whisper_prompt=whisper_prompt,
        filter_log_path=filter_log_path,
        chunk_idx=chunk_idx,
        chunk_file=wav_path.name,
        vad_backed=has_vad,
    )
    if not cleaned_text:
        return "", []
    if channel_idx is not None:
        valid_segments = _tag_segments_with_channel(valid_segments, channel_idx, speaker_label)
    return cleaned_text, valid_segments


def _try_transcribe_stereo_chunk_channels(
    wav_path: Path,
    mlx_whisper: Any,
    kwargs: Dict[str, Any],
    whisper_prompt: Optional[str],
    filter_log_path: Optional[Path | str],
    chunk_idx: Optional[int],
) -> Optional[Tuple[str, list[dict[str, Any]]]]:
    """If wav_path is 2-channel stereo with both channels active, transcribes Ch0 and Ch1 separately (D-REC-01)."""
    import tempfile
    import wave
    import numpy as np

    try:
        from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import find_wav_data_offset
        raw_bytes = wav_path.read_bytes()
        with wave.open(str(wav_path), "rb") as wf:
            n_ch = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sr = wf.getframerate()
        if n_ch != 2 or sampwidth != 2:
            return None
        data_off, data_sz = find_wav_data_offset(raw_bytes)
        pcm = raw_bytes[data_off : data_off + data_sz] if data_sz > 0 else raw_bytes[data_off:]
        rem = len(pcm) % 4
        if rem:
            pcm = pcm[:-rem]
        if len(pcm) < 4:
            return None
        stereo = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2)
        rms0 = float(np.sqrt(np.mean(stereo[:, 0].astype(np.float32) ** 2)))
        rms1 = float(np.sqrt(np.mean(stereo[:, 1].astype(np.float32) ** 2)))
        if not (rms0 >= 120.0 and rms1 >= 120.0):
            return None
        texts: List[str] = []
        all_segs: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="mlx_stereo_chunk_") as td:
            td_p = Path(td)
            for ch_i, spk_lbl in ((0, "SPEAKER_00"), (1, "SPEAKER_01")):
                ch_wav = td_p / f"{wav_path.stem}_ch{ch_i}.wav"
                with wave.open(str(ch_wav), "wb") as w_out:
                    w_out.setnchannels(1)
                    w_out.setsampwidth(2)
                    w_out.setframerate(sr)
                    w_out.writeframes(stereo[:, ch_i].tobytes())
                t_ch, s_ch = _transcribe_single_mono_wav_mlx(
                    ch_wav, mlx_whisper, kwargs, whisper_prompt, filter_log_path, chunk_idx,
                    channel_idx=ch_i, speaker_label=spk_lbl,
                )
                if t_ch:
                    texts.append(t_ch)
                    all_segs.extend(s_ch)
        all_segs.sort(key=lambda x: (float(x.get("start", 0.0)), int(x.get("channel", 0))))
        return " ".join(texts).strip(), all_segs
    except Exception:
        return None


def _transcribe_mlx_internal(
    wav_path: Path,
    model_name: str,
    whisper_prompt: Optional[str] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
) -> Tuple[str, list[dict[str, Any]]]:
    repo_root = Path(__file__).resolve().parent.parent.parent
    venv_bin = str(repo_root / ".venv" / "bin")
    CredentialManager.prepend_path(venv_bin)

    import mlx_whisper

    from pipeline_a_intake.local_stt_core import resolve_model_path

    resolved_model = resolve_model_path(model_name)
    kwargs = DEFAULT_ASR_DECODE_CONFIG.to_live_kwargs(
        model_path=resolved_model,
        initial_prompt=whisper_prompt,
    )

    try:
        stereo_res = _try_transcribe_stereo_chunk_channels(
            wav_path, mlx_whisper, kwargs, whisper_prompt, filter_log_path, chunk_idx
        )
        if stereo_res is not None:
            return stereo_res
        return _transcribe_single_mono_wav_mlx(
            wav_path, mlx_whisper, kwargs, whisper_prompt, filter_log_path, chunk_idx
        )
    except Exception as e:
        raise RuntimeError(f"Local MLX Whisper transcription failed for {wav_path.name}: {e}. Cloud fallback is prohibited.") from e


def _transcribe_mlx(
    wav_path: Path,
    model_name: str,
    whisper_prompt: Optional[str] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
) -> str:
    text, _ = _transcribe_mlx_internal(
        wav_path=wav_path,
        model_name=model_name,
        whisper_prompt=whisper_prompt,
        filter_log_path=filter_log_path,
        chunk_idx=chunk_idx,
    )
    return text


def transcribe_meeting_chunk(
    wav_path: Path,
    backend: str = "auto",
    client: Optional[Any] = None,
    whisper_prompt: Optional[str] = None,
    model_name: Optional[str] = None,
    mock_mode: bool = False,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    return_segments: bool = False,
) -> Union[str, Tuple[str, list[dict[str, Any]]]]:
    """Transcribes a single audio slice with silence gating and anti-hallucination filtering.

    Hardware target: Local Apple Silicon Metal GPU via MLX Whisper.
    Cloud fallback is strictly forbidden.
    """
    if client is not None:
        logger.debug("Cloud client parameter ignored; raw audio chunk egress is prohibited.")
    if mock_mode:
        raise RuntimeError(f"MOCK_ASR_PROHIBITED: Mock transcription is prohibited for {wav_path.name}; authentic ASR execution is required.")

    effective_backend = _resolve_effective_backend(backend)

    if effective_backend == "mlx":
        effective_model = model_name
        if not effective_model or str(effective_model).lower().strip() == "auto":
            try:
                from dual_pipeline.model_registry import resolve_acoustic_asr
                effective_model = resolve_acoustic_asr().model_id
            except Exception:
                effective_model = None

        text, segs = _transcribe_mlx_internal(
            wav_path,
            effective_model,
            whisper_prompt=whisper_prompt,
            filter_log_path=filter_log_path,
            chunk_idx=chunk_idx,
        )
        if return_segments:
            return text, segs
        return text

    raise ValueError(f"Unsupported transcription backend: {effective_backend}. Only 'mlx' or mock mode is permitted.")


def transcribe_meeting_chunk_with_segments(
    wav_path: Path,
    backend: str = "auto",
    client: Optional[Any] = None,
    whisper_prompt: Optional[str] = None,
    model_name: Optional[str] = None,
    mock_mode: bool = False,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
) -> Tuple[str, list[dict[str, Any]]]:
    """Transcribes chunk and returns both sanitized text and segment list with word timestamps."""
    res = transcribe_meeting_chunk(
        wav_path=wav_path,
        backend=backend,
        client=client,
        whisper_prompt=whisper_prompt,
        model_name=model_name,
        mock_mode=mock_mode,
        filter_log_path=filter_log_path,
        chunk_idx=chunk_idx,
        return_segments=True,
    )
    if isinstance(res, tuple):
        return res
    return res, []


def clear_mlx_transcriber_memory() -> None:
    """Reclaims active MLX Metal memory and unloads ModelHolder weights."""
    try:
        from dual_pipeline.audio_ingestion_core.mlx_transcriber import clear_mlx_whisper_metal_cache
        clear_mlx_whisper_metal_cache()
    except Exception as exc:
        logger.debug("clear_mlx_transcriber_memory encountered exception: %s", exc)
