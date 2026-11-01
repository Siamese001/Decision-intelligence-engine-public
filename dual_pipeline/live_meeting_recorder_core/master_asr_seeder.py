"""Utility for seeding master audio ASR cache from live transcribed chunk segments."""
from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.master_asr_seeder")


def _resolve_entry_speaker(
    entry: Dict[str, Any], speaker_map: Optional[Dict[str, str]] = None
) -> Tuple[str, str]:
    raw_spk = entry.get("speaker")
    voice_id = entry.get("voice_id")
    if speaker_map:
        if raw_spk and str(raw_spk) in speaker_map:
            raw_spk = speaker_map[str(raw_spk)]
        elif voice_id and str(voice_id) in speaker_map:
            raw_spk = speaker_map[str(voice_id)]

    assigned_spk = "Unknown"
    if raw_spk and str(raw_spk).lower() != "speaker":
        assigned_spk = str(raw_spk)
    elif voice_id and str(voice_id).lower() != "speaker":
        assigned_spk = str(voice_id)

    raw_val = str(raw_spk or voice_id or assigned_spk)
    return assigned_spk, raw_val


def _build_word_timings(text: str, start: float, end: float) -> List[Dict[str, Any]]:
    tokens = str(text).split()
    if not tokens:
        return []
    en = max(start + 0.1, end)
    step = (en - start) / len(tokens)
    return [
        {
            "word": tok,
            "start": round(start + idx * step, 3),
            "end": round(start + (idx + 1) * step, 3),
        }
        for idx, tok in enumerate(tokens)
    ]


def _parse_segment_entry(
    line: str, speaker_map: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    stripped = line.strip()
    if not stripped:
        return None
    entry = json.loads(stripped)
    assigned_spk, raw_speaker = _resolve_entry_speaker(entry, speaker_map=speaker_map)
    st = float(entry.get("start_sec", entry.get("start", entry.get("seconds", 0.0))))
    en = float(entry.get("end_sec", entry.get("end", entry.get("seconds", 0.0))))
    txt = entry.get("verbatim", entry.get("text", ""))

    words = entry.get("words")
    if not words:
        words = _build_word_timings(txt, st, en)

    return {
        "start": st,
        "end": en,
        "text": txt,
        "speaker": assigned_spk,
        "raw_speaker": raw_speaker,
        "words": words,
    }


def _load_disk_speaker_map(recorder: Any) -> Dict[str, str]:
    out_dir = getattr(recorder, "output_dir", None)
    clean_slug = getattr(recorder, "clean_slug", None)
    if not (out_dir and clean_slug):
        return {}
    smap_path = Path(out_dir) / f"{str(clean_slug).replace(' ', '_').lower()}_speaker_map.json"
    if not smap_path.is_file():
        return {}
    try:
        loaded = json.loads(smap_path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in loaded.items() if v} if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _merge_manager_speaker_map(
    smap: Dict[str, str], mgr: Any, skip_identity: bool
) -> None:
    if not (mgr and hasattr(mgr, "get_speaker_map")):
        return
    try:
        raw_map = mgr.get_speaker_map()
    except Exception:
        return
    for k, v in raw_map.items():
        if v and (not skip_identity or str(v) != str(k)):
            smap[str(k)] = str(v)


def _resolve_recorder_speaker_map(recorder: Any) -> Dict[str, str]:
    smap = _load_disk_speaker_map(recorder)
    _merge_manager_speaker_map(smap, getattr(recorder, "speaker_hint_manager", None), False)
    _merge_manager_speaker_map(smap, getattr(recorder, "voice_registry", None), True)
    return smap


def _load_raw_chunk_segments(
    segments_jsonl: Path, speaker_map: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    raw_segments: List[Dict[str, Any]] = []
    with open(segments_jsonl, "r", encoding="utf-8") as f_seg:
        for line in f_seg:
            seg = _parse_segment_entry(line, speaker_map=speaker_map)
            if seg:
                raw_segments.append(seg)
    return raw_segments


def _resolve_recorder_cache_dir(recorder: Any) -> Optional[Path]:
    for attr in ("transcript_cache_dir", "_target_cache_dir", "cache_dir"):
        cd = getattr(recorder, attr, None)
        if isinstance(cd, (str, Path)):
            return Path(cd)
    return None


def _is_segment_coverage_sufficient(
    recorder: Any, receipt: Any, raw_segments: List[Dict[str, Any]]
) -> bool:
    raw_tc = getattr(receipt, "total_chunks", 0)
    total_chunks = int(raw_tc) if isinstance(raw_tc, (int, float)) else 0
    raw_proc = getattr(recorder, "processed_chunks", None)
    processed = len(raw_proc) if isinstance(raw_proc, (list, tuple, set, dict)) else 0
    if total_chunks >= 2 and 0 < processed < int(total_chunks * 0.85):
        logger.warning(
            "[MASTER-ASR-CACHE-SKIPPED] Processed chunks (%d) < 85%% of stitched chunks (%d); skipping partial ASR seed.",
            processed,
            total_chunks,
        )
        return False
    raw_dur = getattr(receipt, "duration_s", 0.0)
    dur_s = float(raw_dur) if isinstance(raw_dur, (int, float)) else 0.0
    max_end = max((float(s.get("end", 0.0)) for s in raw_segments), default=0.0)
    if dur_s >= 10.0 and max_end < dur_s * 0.85:
        logger.warning(
            "[MASTER-ASR-CACHE-SKIPPED] Segment coverage (%.1fs) < 85%% of master duration (%.1fs); skipping partial ASR seed.",
            max_end,
            dur_s,
        )
        return False
    return True


def _resolve_asr_seed_config(recorder: Any) -> tuple[str, str, str]:
    whisper_mod = getattr(recorder, "whisper_model", None)
    if not whisper_mod or str(whisper_mod).lower().strip() == "auto":
        from dual_pipeline.model_registry import FALLBACK_TIERS, ModelTier, resolve_acoustic_asr
        try:
            whisper_mod = resolve_acoustic_asr().model_id
        except Exception:
            whisper_mod = FALLBACK_TIERS[ModelTier.ACOUSTIC_MLX_WHISPER.value]["default_model"]
    raw_lang = getattr(recorder, "language", None)
    rec_lang = raw_lang if isinstance(raw_lang, str) and raw_lang.strip() else "en"
    master_wav = getattr(recorder, "master_wav", None)
    master_stem = master_wav.stem if hasattr(master_wav, "stem") else "audio"
    return str(whisper_mod), rec_lang, master_stem


def _mirror_seeded_master_to_primary_root(recorder: Any, expected_sha256: str) -> None:
    master_wav = getattr(recorder, "master_wav", None)
    if not master_wav or not Path(master_wav).is_file():
        return
    with contextlib.suppress(Exception):
        from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
            mirror_master_wav_to_primary_root,
        )

        mirror_master_wav_to_primary_root(
            recorder, Path(master_wav), expected_sha256=expected_sha256
        )


def _extract_voice_centroids(recorder: Any) -> Dict[str, Any]:
    vr = getattr(recorder, "voice_registry", None)
    if not vr:
        return {}
    if hasattr(vr, "get_all_centroids"):
        with contextlib.suppress(Exception):
            res = vr.get_all_centroids()
            if res:
                return res
    if hasattr(vr, "voice_clusters") and isinstance(vr.voice_clusters, dict):
        return {
            str(vid): list(c.centroid)
            for vid, c in vr.voice_clusters.items()
            if getattr(c, "centroid", None)
        }
    if hasattr(vr, "centroids"):
        return getattr(vr, "centroids", {}) or {}
    return {}


def _build_structured_turns_and_transcript(
    structured_utterances: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], str]:
    structured_turns: List[Dict[str, Any]] = []
    t_lines: List[str] = []
    for s in structured_utterances:
        txt = s.get("text", "").strip()
        if not txt:
            continue
        st = float(s.get("start", s.get("start_time_sec", 0.0)))
        en = float(s.get("end", s.get("end_time_sec", st)))
        dur = round(max(0.0, en - st), 2)
        spk = s.get("speaker", "Unknown")
        structured_turns.append({
            "start": round(st, 2),
            "end": round(en, 2),
            "duration": dur,
            "speaker": spk,
            "raw_speaker": s.get("raw_speaker") or spk,
            "text": txt,
            "words": s.get("words", []),
            "overlap": bool(s.get("overlap", False)),
            "active_speakers": s.get("active_speakers", [spk]),
        })
        m_val, sec = divmod(st, 60)
        h, m_val = divmod(m_val, 60)
        t_lines.append(f"**{spk}** [{int(h):02d}:{int(m_val):02d}:{int(sec):02d}]: {txt}")
    return structured_turns, "\n\n".join(t_lines)


def _seed_tier_a_cache_sidecars(
    recorder: Any,
    receipt: Any,
    raw_chunk_segments: List[Dict[str, Any]],
    cache_dir: Optional[Path],
    master_stem: str,
    whisper_mod: str,
    rec_lang: str,
    speaker_map: Optional[Dict[str, str]] = None,
) -> None:
    with contextlib.suppress(Exception):
        import hashlib
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import get_cache_dir, save_audio_diarization_cache
        from dual_pipeline.audio_ingestion_core.utterance_assembler import assemble_utterances
        from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal

        target_c_dir = get_cache_dir(cache_dir)
        structured_utterances = assemble_utterances(raw_chunk_segments)
        structured_turns, formatted_transcript = _build_structured_turns_and_transcript(structured_utterances)
        audio_hash = receipt.master_wav_sha256[:16]
        cache_key = hashlib.sha256(f"{audio_hash}:{whisper_mod}:{rec_lang}:False:False".encode()).hexdigest()[:16]

        cache_file = target_c_dir / f"{master_stem}_{cache_key}.transcript_cache.json"
        structured_file = target_c_dir / f"{master_stem}_{cache_key}.segments.json"
        utterance_file = target_c_dir / f"{master_stem}_{cache_key}.utterances.json"
        cache_file.write_text(formatted_transcript, encoding="utf-8")
        structured_file.write_text(json.dumps(structured_turns, indent=2), encoding="utf-8")
        utterance_file.write_text(json.dumps(structured_utterances, indent=2), encoding="utf-8")

        save_audio_diarization_cache(
            audio_sha256=audio_hash, turns=structured_turns, model_name="live_recorder",
            stem=master_stem, cache_dir=target_c_dir,
        )
        vp_file = target_c_dir / f"{master_stem}_{cache_key}.voiceprints.json"
        vp_payload = {
            "audio_sha256": audio_hash,
            "project": getattr(recorder, "project", "GLOBAL") or "GLOBAL",
            "centroids": _extract_voice_centroids(recorder) or {},
            "matches": speaker_map or {},
        }
        vp_file.write_text(json.dumps(vp_payload, indent=2), encoding="utf-8")

        create_cache_seal(
            structured_file=structured_file,
            cache_file=cache_file,
            utterance_file=utterance_file,
            audio_sha256=receipt.master_wav_sha256,
            cache_key=cache_key,
            voiceprints_file=vp_file,
            cache_tier="LIVE_PREVIEW",
        )


def seed_master_asr_from_recorder(recorder: Any, receipt: Any) -> bool:
    """Seeds master WAV ASR cache using pre-transcribed chunk segments from recording."""
    if not (receipt and getattr(receipt, "master_wav_sha256", None)):
        return False

    segments_jsonl = getattr(recorder, "segments_jsonl", None)
    if not (segments_jsonl and segments_jsonl.exists()):
        return False

    try:
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import seed_master_from_chunk_segments

        speaker_map = _resolve_recorder_speaker_map(recorder)
        raw_chunk_segments = _load_raw_chunk_segments(segments_jsonl, speaker_map=speaker_map)
        if not raw_chunk_segments or not _is_segment_coverage_sufficient(
            recorder, receipt, raw_chunk_segments
        ):
            return False

        cache_dir = _resolve_recorder_cache_dir(recorder)
        whisper_mod, rec_lang, master_stem = _resolve_asr_seed_config(recorder)

        has_genuine_words = any(
            isinstance(s.get("words"), list)
            and any(isinstance(w, dict) and not w.get("interpolated", True) for w in s["words"])
            for s in raw_chunk_segments
        )
        diarization_complete = bool(getattr(recorder, "_acoustic_diarization_complete", False))
        is_mock = bool(getattr(recorder, "mock_mode", False) is True)

        if (has_genuine_words and diarization_complete) or is_mock:
            seed_master_from_chunk_segments(
                chunk_segments=raw_chunk_segments,
                master_sha256=receipt.master_wav_sha256,
                model_name=whisper_mod,
                language=rec_lang,
                stem=master_stem,
                cache_dir=cache_dir,
                mock=is_mock,
            )

        _seed_tier_a_cache_sidecars(
            recorder=recorder,
            receipt=receipt,
            raw_chunk_segments=raw_chunk_segments,
            cache_dir=cache_dir,
            master_stem=master_stem,
            whisper_mod=whisper_mod,
            rec_lang=rec_lang,
            speaker_map=speaker_map,
        )

        _mirror_seeded_master_to_primary_root(recorder, receipt.master_wav_sha256)
        logger.info(
            "[MASTER-ASR-CACHE-SEEDED] Seeded master ASR and Tier A cache sidecars for %s (%d segments)",
            getattr(getattr(recorder, "master_wav", None), "name", "master"),
            len(raw_chunk_segments),
        )
        return True
    except Exception as e:
        logger.warning("Failed to seed master ASR cache from chunk segments: %s", e)
        return False

def drain_external_chunks(recorder: Any, is_final: bool = False) -> None:
    """Migrates staged audio chunks from external capture manager or instant spooler to recorder chunks_dir."""
    from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import migrate_all_chunk_sources_on_drain

    migrate_all_chunk_sources_on_drain(recorder)


def reconcile_continuous_spooler_master(recorder: Any, receipt: Any) -> Any:
    """Reconciles master audio with continuous spooler and seeds master ASR cache."""
    from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import reconcile_master_wav_and_receipt

    receipt = reconcile_master_wav_and_receipt(recorder, receipt)
    if getattr(recorder, "master_wav", None) and Path(recorder.master_wav).exists() and receipt:
        with contextlib.suppress(Exception):
            seed_master_asr_from_recorder(recorder, receipt)
    return receipt
