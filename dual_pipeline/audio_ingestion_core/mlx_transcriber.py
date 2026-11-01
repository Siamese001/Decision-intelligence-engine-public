"""Local MLX Audio Transcriber using Apple Silicon Metal GPU."""
from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from ..audio_postprocess import (
    build_initial_prompt,
    filter_segments_and_collect_valid,
    sanitize_transcript_text,
)
from .asr_deduplicator import deduplicate_consecutive_asr_segments
from ..model_registry import resolve_acoustic_asr
from .acoustic_airgap import (
    assert_acoustic_headroom,
    clear_mlx_whisper_metal_cache,
    enforce_process_airgap,
    is_mlx_available,
    write_acoustic_receipt,
)
from .acoustic_cache import (
    build_acoustic_cache_key,
    compute_audio_digest,
    find_matching_sealed_cache,
    resolve_transcript_cache_dir,
    save_transcript_cache_bundle,
    try_load_transcript_cache,
)
from .audio_asr_cache import (
    load_audio_asr_cache,
    load_audio_diarization_cache,
    save_audio_asr_cache,
)
from .base import BaseAudioTranscriber
from .ffmpeg import ensure_ffmpeg
from .speaker_alignment import align_segments_with_diarization as _align_segments_with_diarization
from .voiceprint_binding import (
    _clean_attendee_entry,
    _extract_and_bind_voiceprints,
    _flatten_attendee_roster,
    _match_cluster_centroids_against_roster,
    _save_voiceprints_sidecar,
    _split_attendee_string,
    handle_diarization_collapse,
)

logger = logging.getLogger(__name__)

# Re-export cache loader for backwards compatibility
_try_load_cache = try_load_transcript_cache


class LocalMLXAudioTranscriber(BaseAudioTranscriber):
    """Local acoustic transcription engine leveraging MLX Whisper and pyannote diarization."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        mock: bool = False,
        language: Optional[str] = None,
        without_timestamps: bool = False,
    ):
        super().__init__(mock=mock)
        self.api_key = api_key
        if model_name is None:
            self.model_name = resolve_acoustic_asr().model_id
        else:
            self.model_name = model_name
        self.language = language
        self.without_timestamps = without_timestamps
        if not self.mock:
            from .diarization_engine import get_diarization_engine
            self.diarization_engine = get_diarization_engine(lazy=True)
        else:
            self.diarization_engine = None

    def _resolve_backend_and_model(self, metadata: Optional[Dict[str, Any]]) -> tuple[str, str]:
        req_backend = str(getattr(self, "backend", None) or (metadata.get("backend") if metadata else None) or "auto").lower().replace("_", "-")
        eff_model = (metadata.get("whisper_model") if metadata else None) or self.model_name
        return req_backend, eff_model

    def _transcribe_single_channel_path(
        self, ch_wav: str, req_backend: str, eff_model: str, whisper_prompt: str
    ) -> List[Dict[str, Any]]:
        from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
        if req_backend not in ("faster-whisper", "whisperx", "cpu") and is_mlx_available():
            import mlx_whisper
            kw = DEFAULT_ASR_DECODE_CONFIG.to_batch_kwargs(model_path=eff_model, initial_prompt=whisper_prompt)
            if self.language:
                kw["language"] = self.language
            return list(mlx_whisper.transcribe(ch_wav, **kw).get("segments", []))
        return self._transcribe_raw_with_local_fallback(
            effective_audio_path=ch_wav, model_name=eff_model, initial_prompt=whisper_prompt, backend=req_backend,
        )

    def _transcribe_raw_with_mlx(
        self, effective_audio_path: str, safe_stem: str, cache_dir: Path, audio_hash: str, metadata: Optional[Dict[str, Any]],
        source_audio_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Invokes MLX Whisper or local airgapped CPU/MPS fallback under strict process airgap and GPU serialization."""
        from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import serialized_mlx_gpu_execution
        from .subcluster_splitter import detect_stereo_channel_activity
        from .stereo_alignment import transcribe_stereo_channels_independently

        enforce_process_airgap()
        self._apply_stage0_acoustic_preflight(effective_audio_path, metadata)
        whisper_prompt = build_initial_prompt(metadata)
        req_backend, eff_model = self._resolve_backend_and_model(metadata)
        lock_timeout = float((metadata or {}).get("mlx_lock_timeout_seconds", 600.0))

        with serialized_mlx_gpu_execution(
            session_id="batch_mlx",
            meeting_slug=safe_stem,
            project_name=str((metadata or {}).get("project") or ""),
            timeout_seconds=lock_timeout,
        ):
            stereo_cand = source_audio_path or effective_audio_path
            stereo_act = detect_stereo_channel_activity(stereo_cand) if (stereo_cand and Path(str(stereo_cand)).is_file()) else {}
            if stereo_act.get("is_dual_active_stereo"):
                if metadata is not None:
                    metadata["stereo_channel_activity"] = stereo_act
                raw_segments = transcribe_stereo_channels_independently(
                    stereo_cand,
                    lambda ch_w, _idx: self._transcribe_single_channel_path(ch_w, req_backend, eff_model, whisper_prompt),
                    boundary_overlap_sec=1.5,
                )
            else:
                raw_segments = self._transcribe_single_channel_path(
                    effective_audio_path, req_backend, eff_model, whisper_prompt
                )

        save_audio_asr_cache(
            audio_sha256=audio_hash, raw_segments=raw_segments, model_name=eff_model,
            language=self.language, stem=safe_stem, cache_dir=cache_dir, mock=self.mock,
        )
        return raw_segments

    def _align_whisperx_segments(self, whisperx: Any, segs: List[Dict[str, Any]], audio: Any, lang_code: str, dev: str) -> List[Dict[str, Any]]:
        if not (segs and hasattr(whisperx, "load_align_model")):
            return segs
        try:
            model_a, align_meta = whisperx.load_align_model(language_code=lang_code, device=dev)
            aligned_res = whisperx.align(segs, model_a, align_meta, audio, dev, return_char_alignments=False)
            return list(aligned_res["segments"]) if (isinstance(aligned_res, dict) and aligned_res.get("segments")) else segs
        except Exception as align_err:
            logger.debug("WhisperX inline word alignment skipped: %s", align_err)
            return segs

    def _try_whisperx_transcribe(self, effective_audio_path: str, clean_model: str) -> Optional[List[Dict[str, Any]]]:
        try:
            import whisperx
            dev = getattr(self, "device", "cpu")
            lang_code = self.language or "en"
            model = whisperx.load_model(clean_model, device=dev, compute_type=getattr(self, "compute_type", "int8"), language=lang_code)
            audio = whisperx.load_audio(effective_audio_path)
            segs = list(model.transcribe(audio, batch_size=8).get("segments", []))
            return self._align_whisperx_segments(whisperx, segs, audio, lang_code, dev)
        except ImportError:
            return None

    def _try_faster_whisper_transcribe(self, effective_audio_path: str, clean_model: str, initial_prompt: str) -> Optional[List[Dict[str, Any]]]:
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(clean_model, device=getattr(self, "device", "cpu"), compute_type=getattr(self, "compute_type", "int8"), local_files_only=True)
            segs_iter, _ = model.transcribe(effective_audio_path, language=self.language, initial_prompt=initial_prompt or None, word_timestamps=True, vad_filter=True)
            return [
                {
                    "start": float(s.start), "end": float(s.end), "text": str(s.text),
                    "avg_logprob": float(getattr(s, "avg_logprob", -0.2)),
                    "no_speech_prob": float(getattr(s, "no_speech_prob", 0.05)),
                    "compression_ratio": float(getattr(s, "compression_ratio", 1.2)),
                    "words": [{"word": w.word, "start": float(w.start), "end": float(w.end), "probability": float(getattr(w, "probability", 1.0))} for w in (getattr(s, "words", None) or [])],
                }
                for s in segs_iter
            ]
        except ImportError:
            return None

    def _transcribe_raw_with_local_fallback(
        self, effective_audio_path: str, model_name: str, initial_prompt: str, backend: str = "faster-whisper",
    ) -> List[Dict[str, Any]]:
        """Executes local airgapped faster-whisper or whisperx transcription when MLX is not active."""
        enforce_process_airgap()
        clean_model = model_name.split("/")[-1].replace("-mlx", "") if "/" in model_name else model_name
        if backend == "whisperx":
            segs = self._try_whisperx_transcribe(effective_audio_path, clean_model)
            if segs is not None:
                return segs
        fw_segs = self._try_faster_whisper_transcribe(effective_audio_path, clean_model, initial_prompt)
        if fw_segs is not None:
            return fw_segs
        if is_mlx_available():
            import mlx_whisper
            from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
            transcribe_kwargs = DEFAULT_ASR_DECODE_CONFIG.to_batch_kwargs(model_path=self.model_name, initial_prompt=initial_prompt)
            if self.language:
                transcribe_kwargs["language"] = self.language
            return list(mlx_whisper.transcribe(effective_audio_path, **transcribe_kwargs).get("segments", []))
        raise RuntimeError(f"No local ASR backend available for '{backend}' (neither faster_whisper, whisperx, nor mlx_whisper could be loaded).")

    def _sanitize_raw_segments(
        self,
        raw_segments: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Sanitizes ASR segments by telemetry gating, suppressing loops, and collapsing stutter artifacts."""
        from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG

        whisper_prompt = build_initial_prompt(metadata)
        filter_log_path = metadata.get("filter_log_path") if metadata else None
        _, telemetry_valid = filter_segments_and_collect_valid(
            raw_segments,
            decode_config=DEFAULT_ASR_DECODE_CONFIG,
            filter_log_path=filter_log_path,
        )
        sanitized_segments: List[Dict[str, Any]] = []
        for s in telemetry_valid:
            is_vad_backed = bool(
                s.get("vad_backed")
                or (
                    float(s.get("no_speech_prob", 0.5)) < 0.25
                    and float(s.get("avg_logprob", -0.2)) > -0.65
                    and bool(s.get("words"))
                )
            )
            txt = sanitize_transcript_text(
                s.get("text", ""),
                whisper_prompt=whisper_prompt,
                filter_log_path=filter_log_path,
                no_speech_prob=s.get("no_speech_prob"),
                avg_logprob=s.get("avg_logprob"),
                vad_backed=is_vad_backed,
            )
            if txt:
                s_copy = dict(s)
                s_copy["text"] = txt
                sanitized_segments.append(s_copy)

        return deduplicate_consecutive_asr_segments(
            sanitized_segments,
            filter_log_path=filter_log_path,
        )

    def _ensure_diarization_engine_ready(self) -> None:
        if self.mock:
            return
        if self.diarization_engine is None:
            from .diarization_engine import get_diarization_engine
            self.diarization_engine = get_diarization_engine()
        elif not self.diarization_engine._initialized:
            self.diarization_engine.initialize()

    def _resolve_raw_segments_for_live(
        self, raw_segments: Optional[List[Dict[str, Any]]], effective_audio_path: str,
        safe_stem: str, cache_dir: Path, audio_hash: str, metadata: Optional[Dict[str, Any]],
        source_audio_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
        if metadata and metadata.get("force_rerun"):
            raw_segments = None
        requires_words = getattr(DEFAULT_ASR_DECODE_CONFIG, "word_timestamps", True)
        if raw_segments is not None and requires_words and not self.mock:
            if not any(isinstance(seg, dict) and seg.get("words") for seg in raw_segments):
                logger.warning("Cached ASR segments for %s lack word-level timestamps; re-transcribing.", safe_stem)
                raw_segments = None
        if raw_segments is None:
            raw_segments = self._transcribe_raw_with_mlx(
                effective_audio_path=effective_audio_path, safe_stem=safe_stem,
                cache_dir=cache_dir, audio_hash=audio_hash, metadata=metadata,
                source_audio_path=source_audio_path,
            )
            self._clear_mlx_metal_cache()
        else:
            logger.info("Audio-keyed ASR cache hit: Reusing %d raw segments for %s", len(raw_segments), safe_stem)
        return raw_segments

    def _assemble_structured_utterances(
        self, segments: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if segments and any(s.get("_whisperx_aligned") for s in segments):
            from .whisperx_aligner import whisperx_segments_to_utterances
            return whisperx_segments_to_utterances(segments)
        from .utterance_assembler import AssemblyConfig, assemble_utterances
        pacing_map: Dict[str, float] = {}
        if isinstance(metadata, dict):
            if isinstance(metadata.get("speaker_pacing_map"), dict):
                pacing_map.update({str(k): float(v) for k, v in metadata["speaker_pacing_map"].items() if v})
            try:
                from dual_pipeline.speaker_memory import SpeakerMemory
                mem = SpeakerMemory(
                    memory_path=Path(metadata["speaker_memory_path"]) if metadata.get("speaker_memory_path") else None
                )
                for prof in getattr(mem, "_profiles", {}).values():
                    if getattr(prof, "avg_words_per_minute", 0.0) > 0:
                        pacing_map.setdefault(prof.name, float(prof.avg_words_per_minute))
            except Exception as exc:
                logger.debug("Speaker pacing map hydration skipped: %s", exc)
        cfg = AssemblyConfig(speaker_pacing_map=pacing_map) if pacing_map else None
        return assemble_utterances(segments, config=cfg)

    def _check_early_transcript_cache(
        self, cache_dir: Path, safe_stem: str, cache_key: str, audio_hash: str,
        full_audio_sha256: str, metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if metadata and metadata.get("force_rerun"):
            return None
        cache_file = cache_dir / f"{safe_stem}_{cache_key}.transcript_cache.json"
        structured_file = cache_dir / f"{safe_stem}_{cache_key}.segments.json"
        utterance_file = cache_dir / f"{safe_stem}_{cache_key}.utterances.json"
        return (
            try_load_transcript_cache(cache_file, structured_file, utterance_file, metadata)
            or find_matching_sealed_cache(cache_dir, audio_hash, full_audio_sha256, metadata)
        )

    def _try_reuse_cached_asr_and_diarization(
        self, raw_segments: Optional[List[Dict[str, Any]]], cache_dir: Path,
        safe_stem: str, cache_key: str, audio_hash: str, metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not raw_segments:
            return None
        has_cached_speakers = any(
            s.get("speaker") and str(s.get("speaker")).lower() not in ("unknown", "speaker unknown", "speaker")
            for s in raw_segments
        )
        diar_model = getattr(self.diarization_engine, "model_id", "pinned") if self.diarization_engine else "pinned"
        cached_diar = (
            load_audio_diarization_cache(audio_sha256=audio_hash, model_name=diar_model, stem=safe_stem, cache_dir=cache_dir)
            if not has_cached_speakers else None
        )
        if has_cached_speakers or cached_diar:
            return self._finalize_cached_acoustic_hit(
                raw_segments, cached_diar, has_cached_speakers, cache_dir, safe_stem, cache_key, audio_hash, metadata
            )
        return None

    def _live_transcribe(self, audio_file_path: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Local transcription path using MLX-Whisper and pyannote.audio."""
        enforce_process_airgap()
        safe_stem = re.sub(r'[<>:"/\|?*]', '_', Path(audio_file_path).stem)
        cache_dir = resolve_transcript_cache_dir(audio_file_path, metadata)
        full_audio_sha256, audio_hash = compute_audio_digest(audio_file_path)
        if metadata is not None:
            metadata["audio_sha256"] = full_audio_sha256

        cache_key = build_acoustic_cache_key(
            audio_hash=audio_hash, model_name=self.model_name, language=self.language,
            without_timestamps=self.without_timestamps, mock=self.mock, diarization_engine=self.diarization_engine,
        )
        early_hit = self._check_early_transcript_cache(cache_dir, safe_stem, cache_key, audio_hash, full_audio_sha256, metadata)
        if early_hit is not None:
            return early_hit

        force_rerun = bool(metadata and metadata.get("force_rerun"))
        raw_segments = None if force_rerun else load_audio_asr_cache(
            audio_sha256=audio_hash, model_name=self.model_name, language=self.language,
            stem=safe_stem, cache_dir=cache_dir, mock=self.mock,
        )
        acoustic_hit = None if force_rerun else self._try_reuse_cached_asr_and_diarization(
            raw_segments, cache_dir, safe_stem, cache_key, audio_hash, metadata
        )
        if acoustic_hit is not None:
            return acoustic_hit

        ensure_ffmpeg()
        from pipeline_a_intake.media_extractor import extract_normalized_audio
        try:
            effective_audio_path = str(extract_normalized_audio(audio_file_path, output_dir=cache_dir, force_reextract=force_rerun))
        except Exception as e:
            logger.error("Media normalization failed: %s. Failing closed.", e)
            raise RuntimeError(f"Media normalization failed for {audio_file_path}: {e}") from e

        self._ensure_diarization_engine_ready()
        assert_acoustic_headroom()
        raw_segments = self._resolve_raw_segments_for_live(
            raw_segments, effective_audio_path, safe_stem, cache_dir, audio_hash, metadata,
            source_audio_path=audio_file_path,
        )
        sanitized_segments = self._sanitize_raw_segments(raw_segments, metadata)
        segments = self._diarize_and_align(sanitized_segments, effective_audio_path, metadata, source_audio_path=audio_file_path)
        vp_audio = audio_file_path if (metadata and metadata.get("stereo_channel_activity", {}).get("is_dual_active_stereo") and Path(audio_file_path).is_file()) else effective_audio_path
        cluster_centroids, voiceprint_matches = _extract_and_bind_voiceprints(self.diarization_engine, vp_audio, segments, metadata, mock=self.mock)
        structured_utterances = self._assemble_structured_utterances(segments, metadata=metadata)
        formatted_text = self._format_diarized_json({"segments": structured_utterances}, metadata=metadata)
        structured_turns = self._build_structured_turns(structured_utterances)
        if not (metadata and metadata.get("diarization_status") == "HITL_REQUIRED"):
            save_transcript_cache_bundle(cache_dir, safe_stem, cache_key, audio_hash, formatted_text, structured_turns, structured_utterances, cluster_centroids, voiceprint_matches, metadata)
        self._clear_mlx_metal_cache()
        self._write_acoustic_receipt(safe_stem)
        return formatted_text

    def _try_stereo_diarization(
        self, sanitized_segments: List[Dict[str, Any]], stereo_source: str,
        expected_roster_count: Optional[int], metadata: Optional[Dict[str, Any]],
    ) -> Optional[List[Dict[str, Any]]]:
        if not (stereo_source and Path(str(stereo_source)).is_file()):
            return None
        from .subcluster_splitter import align_and_diarize_dual_channel_stereo
        stereo_aligned = align_and_diarize_dual_channel_stereo(
            sanitized_segments, stereo_source, expected_roster_count=expected_roster_count,
            diarization_engine=self.diarization_engine, metadata=metadata,
        )
        if stereo_aligned and metadata is not None:
            metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
            metadata["has_acoustic_diarization"] = True
        return stereo_aligned

    def _execute_pyannote_diarization(
        self, sanitized_segments: List[Dict[str, Any]], effective_audio_path: str,
        stereo_source: str, min_speakers: int, max_speakers: Optional[int],
        expected_roster_count: Optional[int], was_whisperx_aligned: bool, metadata: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        from .subcluster_splitter import split_merged_remote_cluster
        from .diarization_engine import validate_acoustic_diarization
        from dual_pipeline.speaker_attribution_core.collapse_detector import _verify_collapse_approval_receipt

        diarization = self.diarization_engine.diarize(effective_audio_path, min_speakers=min_speakers, max_speakers=max_speakers)
        if not (metadata and _verify_collapse_approval_receipt(metadata)):
            validate_acoustic_diarization(diarization, expected_roster_count=expected_roster_count, source_name=Path(effective_audio_path).name)
        else:
            logger.info("Acoustic diarization validation: Cluster count and skew tolerated under verified operator approval receipt.")

        if was_whisperx_aligned:
            from .speaker_alignment import extract_acoustic_intervals
            from .whisperx_aligner import assign_speakers_with_whisperx
            aligned = assign_speakers_with_whisperx(
                sanitized_segments, extract_acoustic_intervals(diarization), fill_nearest=True, lock_sentence_speaker=False
            )
        else:
            aligned = _align_segments_with_diarization(sanitized_segments, diarization)

        if expected_roster_count and expected_roster_count >= 3:
            aligned, _ = split_merged_remote_cluster(aligned, audio_path=stereo_source, metadata=metadata, diarization_engine=self.diarization_engine)
        if metadata is not None:
            metadata.setdefault("diarization_method", "whisperx_pyannote")
            metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
            metadata["has_acoustic_diarization"] = True
        return aligned

    def _apply_stage0_acoustic_preflight(self, audio_path: str, metadata: Optional[Dict[str, Any]]) -> None:
        if not isinstance(metadata, dict) or _flatten_attendee_roster(metadata):
            return
        try:
            from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
                run_acoustic_roster_preflight,
            )
            inferred = run_acoustic_roster_preflight(
                audio_path=audio_path,
                sample_embeddings=metadata.get("preflight_sample_embeddings"),
                project=str(metadata.get("project") or "GLOBAL"),
                voiceprint_db_path=metadata.get("voiceprint_db_path"),
                speaker_memory_path=metadata.get("speaker_memory_path"),
                graph_db_path=metadata.get("graph_db_path"),
            )
            if inferred:
                metadata["inferred_acoustic_candidates"] = inferred
        except Exception as exc:
            logger.debug("Stage-0 acoustic preflight skipped: %s", exc)

    def _compute_speaker_bounds(self, metadata: Optional[Dict[str, Any]]) -> tuple[Optional[int], int, Optional[int]]:
        attendees = _flatten_attendee_roster(metadata)
        if not attendees and isinstance(metadata, dict):
            inferred = metadata.get("inferred_acoustic_candidates") or []
            attendees = [str(it.get("name") if isinstance(it, dict) else it).strip() for it in inferred if it]
        expected = len(attendees) if attendees else None
        max_spk = max(1, expected) if expected else None
        min_spk = min(max_spk, max(2, (expected + 1) // 2)) if max_spk and max_spk >= 2 else 1
        return expected, min_spk, max_spk

    def _diarize_and_align(
        self, sanitized_segments: List[Dict[str, Any]], effective_audio_path: str,
        metadata: Optional[Dict[str, Any]], source_audio_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Performs acoustic diarization and segment alignment with failure containment."""
        from .platform_transcript_fusion import try_fuse_metadata_platform_transcript
        if (fused := try_fuse_metadata_platform_transcript(sanitized_segments, metadata)) is not None:
            return fused
        if self.diarization_engine is None:
            return sanitized_segments
        was_whisperx_aligned = False
        if not self.mock:
            from .whisperx_aligner import align_with_whisperx
            sanitized_segments, was_whisperx_aligned = align_with_whisperx(sanitized_segments, effective_audio_path, language=self.language or "en")
        self._last_was_whisperx_aligned = was_whisperx_aligned
        if metadata is not None:
            metadata["alignment_backend"] = "whisperx" if was_whisperx_aligned else "mlx_word_timestamps_fallback"
        expected_roster_count, min_speakers, max_speakers = self._compute_speaker_bounds(metadata)
        stereo_source = source_audio_path or effective_audio_path
        if stereo_aligned := self._try_stereo_diarization(sanitized_segments, stereo_source, expected_roster_count, metadata):
            return stereo_aligned
        logger.info("Running local acoustic diarization via WhisperX + Pyannote DiarizationEngine...")
        try:
            return self._execute_pyannote_diarization(
                sanitized_segments, effective_audio_path, stereo_source, min_speakers,
                max_speakers, expected_roster_count, was_whisperx_aligned, metadata,
            )
        except Exception as e:
            from dual_pipeline.exceptions import DiarizationCollapseError
            from .subcluster_splitter import split_merged_remote_cluster
            if not isinstance(e, DiarizationCollapseError):
                raise
            collapsed = handle_diarization_collapse(
                sanitized_segments=sanitized_segments, diarization=getattr(e, "diarization", None),
                metadata=metadata, error=e, align_func=_align_segments_with_diarization,
            )
            if expected_roster_count and expected_roster_count >= 3:
                collapsed, _ = split_merged_remote_cluster(collapsed, audio_path=stereo_source, metadata=metadata, diarization_engine=self.diarization_engine)
            return collapsed

    def _finalize_cached_acoustic_hit(
        self,
        raw_segments: List[Dict[str, Any]],
        cached_diar: Optional[Any],
        has_cached_speakers: bool,
        cache_dir: Path,
        safe_stem: str,
        cache_key: str,
        audio_hash: str,
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        logger.info("Acoustic cache hit: Reusing %d cached ASR segments with cached diarization for %s", len(raw_segments), safe_stem)
        sanitized = self._sanitize_raw_segments(raw_segments, metadata)
        expected_roster_count, _, _ = self._compute_speaker_bounds(metadata)
        if cached_diar and cached_diar[0]:
            from .diarization_engine import validate_acoustic_diarization
            from dual_pipeline.speaker_attribution_core.collapse_detector import _verify_collapse_approval_receipt
            if not (metadata and _verify_collapse_approval_receipt(metadata)):
                validate_acoustic_diarization(cached_diar[0], expected_roster_count=expected_roster_count, source_name=safe_stem)
        segments = _align_segments_with_diarization(sanitized, cached_diar[0]) if (cached_diar and not has_cached_speakers) else sanitized
        cluster_centroids = (cached_diar[1] or {}) if cached_diar else {}
        from .utterance_assembler import assemble_utterances
        from .voiceprint_binding import _match_cluster_centroids_against_roster
        structured_utterances = assemble_utterances(segments)
        formatted_text = self._format_diarized_json({"segments": structured_utterances}, metadata=metadata)
        structured_turns = self._build_structured_turns(structured_utterances)
        vp_matches = _match_cluster_centroids_against_roster(cluster_centroids, metadata) if cluster_centroids else {}
        if not (metadata and metadata.get("diarization_status") == "HITL_REQUIRED"):
            save_transcript_cache_bundle(
                cache_dir=cache_dir, safe_stem=safe_stem, cache_key=cache_key, audio_hash=audio_hash,
                formatted_text=formatted_text, structured_turns=structured_turns,
                structured_utterances=structured_utterances, cluster_centroids=cluster_centroids,
                voiceprint_matches=vp_matches, metadata=metadata,
            )
        if metadata is not None:
            metadata["has_acoustic_diarization"] = True
            metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
        self._write_acoustic_receipt(safe_stem)
        return formatted_text

    def _clear_mlx_metal_cache(self) -> None:
        """Clear MLX Metal Cache and release VRAM immediately."""
        clear_mlx_whisper_metal_cache()

    def _write_acoustic_receipt(self, safe_stem: str) -> None:
        """Acoustic Provenance Receipt."""
        align_backend = "whisperx" if getattr(self, "_last_was_whisperx_aligned", True) else "mlx_word_timestamps_fallback"
        write_acoustic_receipt(safe_stem, self.model_name, self.diarization_engine, alignment_backend=align_backend)


__all__ = [
    "LocalMLXAudioTranscriber",
    "is_mlx_available",
    "clear_mlx_whisper_metal_cache",
    "_try_load_cache",
    "_clean_attendee_entry",
    "_split_attendee_string",
    "_flatten_attendee_roster",
    "_match_cluster_centroids_against_roster",
    "_extract_and_bind_voiceprints",
    "_save_voiceprints_sidecar",
    "_align_segments_with_diarization",
]
