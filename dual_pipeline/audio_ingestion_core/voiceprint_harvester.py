"""Historical Wave File Voiceprint Harvester.

Scans past meeting directories containing master audio files and resolved Output 01 transcripts,
extracts clean acoustic cluster centroids for verified attendees, and enrolls them into VoiceprintStore.
Enforces fail-closed verification: unverified, generic, unknown, and silent speakers are excluded.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .audio_asr_cache import compute_audio_sha256
from .platform_transcript_fusion import _GENERIC_SPEAKER_RE as _GENERIC_SPEAKER_PATTERN, extract_platform_per_channel_turns
from .voiceprint_store import VoiceprintStore

logger = logging.getLogger(__name__)

_TIMESTAMP_TURN_RE = re.compile(
    r"###\s*\[([0-9:]+)\]\s*\*\*([^*:]+):?\*\*:?\s*(.*?)(?=\n+###|\Z)",
    re.DOTALL,
)
_INLINE_TIMESTAMP_TURN_RE = re.compile(
    r"(?m)^\[([0-9:]+)\]\s*\*\*([^*:\n]+):?\*\*:?\s*(.+)$"
)

_SILENT_TOKENS = (
    "non-speaking",
    "silent",
    "zero vocal participation",
    "no discrete dialogue",
    "0 turns",
    "unattributed",
    "unknown",
)


def _clean_harvester_speaker_name(raw_name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", (raw_name or "").strip()).strip()


def _timestamp_to_seconds(ts_str: str) -> float:
    """Parses HH:MM:SS or MM:SS to float seconds."""
    parts = [float(p) for p in ts_str.strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60.0 + parts[1]
    return parts[0] if parts else 0.0


def _parse_summary_table_row(line: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    line_s = line.strip()
    if not line_s.startswith("|") or line_s.startswith("|---") or "Attendee" in line_s:
        return None
    cols = [c.strip() for c in line_s.split("|")[1:-1]]
    if len(cols) < 4:
        return None
    name = _clean_harvester_speaker_name(cols[0])
    turns_str = cols[3].lower() if len(cols) > 3 else ""
    conf_str = cols[4].lower() if len(cols) > 4 else ""
    anchor_str = cols[5].lower() if len(cols) > 5 else ""
    if any(tok in turns_str or tok in conf_str or tok in anchor_str for tok in _SILENT_TOKENS):
        return None
    if not name or _GENERIC_SPEAKER_PATTERN.fullmatch(name):
        return None
    m_turns = re.search(r"(\d+)\s*turns?", turns_str)
    turn_count = int(m_turns.group(1)) if m_turns else 1
    if turn_count <= 0:
        return None
    data = {
        "canonical_name": name,
        "turns_count": turn_count,
        "status": cols[4] if len(cols) > 4 else "VERIFIED",
        "source": "attribution_summary",
    }
    return name, data


def _parse_attribution_summary_section(transcript_text: str) -> Dict[str, Dict[str, Any]]:
    if "## Attribution Summary" not in transcript_text:
        return {}
    section = transcript_text.split("## Attribution Summary", 1)[1].split("\n---", 1)[0]
    verified: Dict[str, Dict[str, Any]] = {}
    for line in section.strip().splitlines():
        parsed = _parse_summary_table_row(line)
        if parsed:
            verified[parsed[0]] = parsed[1]
    return verified


def _parse_participants_section(transcript_text: str) -> Dict[str, Dict[str, Any]]:
    verified: Dict[str, Dict[str, Any]] = {}
    if "## Participants" not in transcript_text:
        return verified
    section = transcript_text.split("## Participants", 1)[1].split("## Verbatim Attributed Transcript", 1)[0]
    for line in section.strip().splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or "Participant" in line:
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 3:
            continue
        name = _clean_harvester_speaker_name(cols[0])
        coverage = cols[2].lower()
        if any(tok in coverage for tok in _SILENT_TOKENS):
            continue
        if name and "discrete turns present" in coverage and not _GENERIC_SPEAKER_PATTERN.fullmatch(name):
            verified[name] = {
                "canonical_name": name,
                "turns_count": 1,
                "status": "DISCRETE_TURNS_PRESENT",
                "source": "participants_table",
            }
    return verified


def parse_verified_speakers_from_transcript(transcript_text: str) -> Dict[str, Dict[str, Any]]:
    """Extracts verified speaking attendees from Output 01 tables.

    Excludes silent attendees, non-speaking roles, and unverified placeholders.
    """
    verified = _parse_attribution_summary_section(transcript_text)
    if not verified:
        verified = _parse_participants_section(transcript_text)
    return verified


def extract_speaker_turns_from_transcript(
    transcript_text: str, verified_speakers: Optional[Set[str]] = None
) -> List[Dict[str, Any]]:
    """Extracts timestamped speaker turns from Output 01 dialogue text."""
    turns: List[Dict[str, Any]] = []
    matches = _TIMESTAMP_TURN_RE.findall(transcript_text) or _INLINE_TIMESTAMP_TURN_RE.findall(transcript_text)
    if verified_speakers is None:
        parsed_v = parse_verified_speakers_from_transcript(transcript_text)
        if parsed_v:
            verified_speakers = set(parsed_v.keys())
    norm_verified = {_clean_harvester_speaker_name(v).lower() for v in verified_speakers} if verified_speakers else None
    for i, (ts_str, spk_raw, text) in enumerate(matches):
        spk = _clean_harvester_speaker_name(spk_raw)
        if norm_verified and spk.lower() not in norm_verified:
            continue
        if not spk or _GENERIC_SPEAKER_PATTERN.fullmatch(spk) or "speaker" in spk.lower() or "unknown" in spk.lower():
            continue
        start_sec = _timestamp_to_seconds(ts_str)
        next_start = _timestamp_to_seconds(matches[i + 1][0]) if i + 1 < len(matches) else (start_sec + len(text.split()) * 0.4)
        dur = max(2.0, min(15.0, next_start - start_sec))
        turns.append({
            "speaker": spk, "start": start_sec, "end": start_sec + dur,
            "duration": dur, "text": text.strip(), "is_overlap": False,
        })
    return turns


def _find_first_matching_file(meeting_dir: Path, patterns: Tuple[str, ...]) -> Optional[Path]:
    if not meeting_dir.is_dir():
        return None
    for s_dir in (meeting_dir, meeting_dir / "referenced_artifacts"):
        if s_dir.is_dir():
            for pattern in patterns:
                matches = sorted(s_dir.glob(pattern))
                if matches:
                    return matches[0]
    return None


def find_meeting_audio_file(meeting_dir: Path) -> Optional[Path]:
    """Finds master WAV or audio recording inside meeting directory or referenced_artifacts."""
    return _find_first_matching_file(meeting_dir, ("00_*_master_*.wav", "master_*.wav", "00_*.wav", "*.wav", "*.mp3", "*.m4a"))


def find_meeting_utterances_file(meeting_dir: Path) -> Optional[Path]:
    """Finds structured utterances or segments JSON in meeting directory or referenced_artifacts."""
    return _find_first_matching_file(meeting_dir, ("*.utterances.json", "*.segments.json"))


def _read_dir_cluster_map(s_dir: Path) -> Dict[str, str]:
    cmap: Dict[str, str] = {}
    for cf in sorted(s_dir.glob("*.cluster_map.json")):
        try:
            loaded = json.loads(cf.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cmap.update({str(k): str(v) for k, v in loaded.items()})
        except Exception as exc:
            logger.debug("Failed to read cluster map %s: %s", cf, exc)
    return cmap


def _map_sidecar_centroids(
    raw_c: Dict[str, Any], matches: Dict[str, Any], cmap: Dict[str, str], verified_map: Dict[str, Any]
) -> Dict[str, Any]:
    mapped: Dict[str, Any] = {}
    for k, vec in raw_c.items():
        cand = k if k in verified_map else (str(matches.get(k) or cmap.get(k) or ""))
        if cand in verified_map:
            mapped[cand] = vec
    return mapped


def _parse_voiceprint_sidecar(
    vpf: Path, cmap: Dict[str, str], verified_map: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        vp_data = json.loads(vpf.read_text(encoding="utf-8"))
        raw_c = vp_data.get("centroids")
        if isinstance(raw_c, dict) and raw_c:
            mapped = _map_sidecar_centroids(raw_c, vp_data.get("matches") or {}, cmap, verified_map)
            if mapped:
                return mapped, vp_data.get("audio_sha256")
    except Exception as exc:
        logger.debug("Failed to parse voiceprint sidecar %s: %s", vpf, exc)
    return {}, None


def _load_cached_meeting_centroids(
    meeting_dir: Path, verified_map: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Loads cached 256-D speaker centroids from *.voiceprints.json if present."""
    for s_dir in (meeting_dir, meeting_dir / "referenced_artifacts"):
        if not s_dir.is_dir():
            continue
        cmap = _read_dir_cluster_map(s_dir)
        for vpf in sorted(s_dir.glob("*.voiceprints.json")):
            mapped, sha = _parse_voiceprint_sidecar(vpf, cmap, verified_map)
            if mapped:
                return mapped, sha
    return {}, None


def _infer_meeting_project(meeting_dir: Path, project: Optional[str]) -> str:
    if project:
        return project.strip().upper()
    parts = meeting_dir.resolve().parts
    if "output" in parts:
        idx = parts.index("output")
        if len(parts) > idx + 1:
            return parts[idx + 1].upper()
    return "GLOBAL"


def _parse_single_utterance_turn(u: Dict[str, Any], verified_map: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if u.get("is_overlap", False):
        return None
    spk = u.get("speaker", "").strip()
    s_sec = float(u.get("start", 0.0))
    e_sec = float(u.get("end", s_sec + 2.0))
    if spk not in verified_map or e_sec - s_sec < 1.5:
        return None
    return {
        "speaker": spk, "start": s_sec, "end": e_sec,
        "duration": e_sec - s_sec, "is_overlap": False,
        "channel_profile": str(u.get("channel_profile") or "mono"),
    }


def _load_verified_turns(
    meeting_dir: Path, transcript_text: str, verified_map: Dict[str, Any]
) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    utterances_path = find_meeting_utterances_file(meeting_dir)
    if utterances_path:
        try:
            for u in json.loads(utterances_path.read_text(encoding="utf-8")):
                parsed = _parse_single_utterance_turn(u, verified_map)
                if parsed is not None:
                    turns.append(parsed)
        except Exception as e:
            logger.warning("Failed parsing utterances file %s: %s", utterances_path, e)
    return turns or extract_speaker_turns_from_transcript(transcript_text, set(verified_map.keys()))


def _resolve_meeting_transcript_and_turns(
    meeting_dir: Path,
) -> Tuple[Optional[Path], Dict[str, Any], List[Dict[str, Any]]]:
    resolved_mds = sorted(meeting_dir.glob("01_*_hitl_resolved_transcript.md"))
    if resolved_mds:
        t_path = resolved_mds[0]
        t_text = t_path.read_text(encoding="utf-8")
        v_map = parse_verified_speakers_from_transcript(t_text)
        turns = _load_verified_turns(meeting_dir, t_text, v_map) if v_map else []
        return t_path, v_map, turns
    v_map, turns, p_path = extract_platform_per_channel_turns(meeting_dir)
    return p_path, v_map, turns


def _is_contaminated_outlier_centroid(vp_store: VoiceprintStore, spk_name: str, proj: str, cand_vec: Any) -> bool:
    existing = vp_store.get_voiceprint(spk_name, project=proj)
    if not existing or int(existing.get("sample_count", 0)) < 2:
        return False
    ex_emb = existing.get("embedding")
    if not isinstance(ex_emb, list) or len(ex_emb) != len(cand_vec):
        return False
    import math
    n1 = math.sqrt(sum(float(x) * float(x) for x in ex_emb))
    n2 = math.sqrt(sum(float(y) * float(y) for y in cand_vec))
    if n1 < 1e-9 or n2 < 1e-9:
        return True
    sim = sum(float(a) * float(b) for a, b in zip(ex_emb, cand_vec)) / (n1 * n2)
    return sim < 0.55


def _enroll_verified_speaker_centroids(
    verified_map: Dict[str, Any],
    centroids: Dict[str, Any],
    turns: List[Dict[str, Any]],
    vp_store: VoiceprintStore,
    proj: str,
    meeting_name: str,
    audio_sha: str,
    max_duration_sec: float,
    dry_run: bool,
) -> List[Dict[str, Any]]:
    enrolled_list: List[Dict[str, Any]] = []
    for spk_name in verified_map:
        if spk_name not in centroids:
            continue
        spk_turns = [t for t in turns if t.get("speaker") == spk_name and not t.get("is_overlap", False)]
        speech_sec = max(2.0, min(max_duration_sec, sum(float(t.get("duration", 0.0)) for t in spk_turns)))
        ch_prof = next((str(t.get("channel_profile")) for t in spk_turns if t.get("channel_profile")), "mono")
        if _is_contaminated_outlier_centroid(vp_store, spk_name, proj, centroids[spk_name]):
            enrolled_list.append({
                "canonical_name": spk_name, "project": proj, "action": "SKIPPED_OUTLIER",
                "audio_sha256": audio_sha, "clean_speech_sec": round(speech_sec, 2),
            })
            continue
        action = "DRY_RUN_ENROLLED"
        if not dry_run:
            res = vp_store.enroll_voiceprint(
                speaker_name=spk_name, embedding=centroids[spk_name], project=proj, is_verified=True,
                verification_source=f"harvest:{meeting_name}", audio_sha256=audio_sha,
                duration_sec=speech_sec, clean_speech_sec=speech_sec, channel_profile=ch_prof,
            )
            action = res.get("action", "ENROLLED")
        enrolled_list.append({
            "canonical_name": spk_name, "project": proj, "action": action,
            "audio_sha256": audio_sha, "clean_speech_sec": round(speech_sec, 2),
        })
    return enrolled_list


def harvest_voiceprints_from_meeting(
    meeting_dir: Path,
    store: Optional[VoiceprintStore] = None,
    diarization_engine: Optional[Any] = None,
    dry_run: bool = False,
    project: Optional[str] = None,
    max_duration_per_speaker_sec: float = 60.0,
) -> Dict[str, Any]:
    """Harvests verified speaker voiceprints from a single historical meeting directory."""
    transcript_path, verified_map, turns = _resolve_meeting_transcript_and_turns(meeting_dir)
    if not transcript_path:
        return {"status": "SKIPPED_NO_TRANSCRIPT", "meeting": meeting_dir.name, "enrolled": []}
    if not verified_map:
        return {"status": "SKIPPED_NO_VERIFIED_SPEAKERS", "meeting": meeting_dir.name, "enrolled": []}

    cached_centroids, cached_sha = (
        _load_cached_meeting_centroids(meeting_dir, verified_map) if diarization_engine is None else ({}, None)
    )
    audio_path = find_meeting_audio_file(meeting_dir)
    if not audio_path and not cached_centroids:
        return {"status": "SKIPPED_NO_AUDIO", "meeting": meeting_dir.name, "enrolled": []}
    if not turns and not cached_centroids:
        return {"status": "SKIPPED_NO_TURNS", "meeting": meeting_dir.name, "enrolled": []}

    audio_sha = compute_audio_sha256(audio_path) if audio_path else (cached_sha or compute_audio_sha256(transcript_path))
    proj = _infer_meeting_project(meeting_dir, project)
    if cached_centroids:
        centroids = cached_centroids
    else:
        if diarization_engine is None:
            from .diarization_engine import get_diarization_engine
            diarization_engine = get_diarization_engine()
        centroids = diarization_engine.extract_cluster_centroids(
            audio_path, turns, max_segments_per_cluster=8, min_segment_duration=2.0
        )

    vp_store = store if store is not None else VoiceprintStore()
    enrolled_list = _enroll_verified_speaker_centroids(
        verified_map, centroids, turns, vp_store, proj, meeting_dir.name, audio_sha, max_duration_per_speaker_sec, dry_run
    )
    return {
        "status": "SUCCESS", "meeting": meeting_dir.name, "project": proj,
        "audio_sha256": audio_sha, "enrolled_count": len(enrolled_list),
        "enrolled": enrolled_list, "dry_run": dry_run,
    }


def harvest_voiceprints_from_directory(
    root_dir: Path,
    project: Optional[str] = None,
    store: Optional[VoiceprintStore] = None,
    diarization_engine: Optional[Any] = None,
    dry_run: bool = False,
    max_meetings: Optional[int] = None,
) -> Dict[str, Any]:
    """Recursively scans directory for meetings with Output 01 transcripts and harvests voiceprints."""
    root_path = Path(root_dir)
    if not root_path.is_dir():
        return {"total_meetings_scanned": 0, "total_speakers_enrolled": 0, "results": []}
    meeting_dirs: List[Path] = []
    for md_file in sorted(root_path.glob("**/01_*_hitl_resolved_transcript.md")):
        md_dir = md_file.parent
        if project and project.strip().lower() not in str(md_dir).lower():
            continue
        if md_dir not in meeting_dirs:
            meeting_dirs.append(md_dir)
            if max_meetings and len(meeting_dirs) >= max_meetings:
                break
    results = [
        harvest_voiceprints_from_meeting(
            meeting_dir=m_dir, store=store, diarization_engine=diarization_engine, dry_run=dry_run, project=project
        )
        for m_dir in meeting_dirs
    ]
    total_enrolled = sum(int(r.get("enrolled_count", 0)) for r in results)
    return {
        "total_meetings_scanned": len(meeting_dirs), "total_speakers_enrolled": total_enrolled,
        "dry_run": dry_run, "results": results,
    }
