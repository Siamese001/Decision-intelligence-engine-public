"""Resolve the live Copilot session and rebuild bounded context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    LiveBufferReader,
    _parse_markdown_turns,
)


def _parse_segments_content(content: str) -> List[Dict[str, Any]]:
    from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn

    turns: List[Dict[str, Any]] = []
    for line in content.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        try:
            d = json.loads(line_str)
        except Exception:
            # Skip incomplete final line from concurrent write
            continue
        spk = str(d.get("speaker") or "Unknown")
        txt = str(d.get("normalized") or d.get("text") or d.get("verbatim") or "")
        if not txt or is_hallucinated_or_prompt_echo_turn(txt, d.get("no_speech_prob")):
            continue
        ts = str(d.get("timestamp") or "00:00:00")
        sec = float(d.get("start_sec") if d.get("start_sec") is not None else d.get("seconds", len(turns) * 3.0))
        item: Dict[str, Any] = {"speaker": spk, "text": txt, "timestamp": ts, "turn_sec": sec}
        if d.get("voice_id") is not None:
            item["voice_id"] = d["voice_id"]
        turns.append(item)
    return turns


def _read_markdown_fallback_turns(output_dir: Path) -> List[Dict[str, Any]]:
    md_dirs = [output_dir] + ([output_dir.parent] if output_dir.name.startswith(".staging_") else [])
    for d in md_dirs:
        for md_file in sorted(d.glob("*_live_transcript.md")):
            if not md_file.is_file():
                continue
            try:
                turns = _parse_markdown_turns(md_file.read_text(encoding="utf-8", errors="replace"))
                if turns:
                    return turns
            except Exception:
                continue
    return []


def _read_turns_from_jsonl(output_dir: Path, attendees: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    if not output_dir.is_dir():
        return []
    from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
        _find_segments_candidates,
        _prepend_same_day_sibling_turns,
    )
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import reconcile_turns_with_hint_queue

    turns: List[Dict[str, Any]] = []
    matched_cand: Optional[Path] = None
    for c in _find_segments_candidates(output_dir):
        if not c.is_file():
            continue
        try:
            content = c.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        turns = _parse_segments_content(content)
        if turns:
            matched_cand = c
            break
    turns = turns or _read_markdown_fallback_turns(output_dir)
    if turns:
        turns = _prepend_same_day_sibling_turns(output_dir, turns, matched_cand)
    return reconcile_turns_with_hint_queue(turns, [output_dir], attendees=attendees)


def _check_candidate_match(candidate: Optional[Any], output_dir: Path) -> Optional[Any]:
    if candidate and candidate.output_dir:
        if (Path(candidate.repo_root) / candidate.output_dir).resolve() == output_dir.resolve():
            return candidate
    return None


def _find_session_by_output_dir(reader: LiveBufferReader, output_dir: Path) -> Optional[Any]:
    resolved = output_dir.resolve()
    for s in reader.list_active_sessions():
        if s.output_dir and (Path(s.repo_root) / s.output_dir).resolve() == resolved:
            return s
    return None


def _matching_manifest(
    reader: LiveBufferReader,
    session_id: Optional[str],
    active: bool,
    output_dir: Path,
    target_eng_id: Optional[str] = None,
) -> Optional[Any]:
    if session_id:
        return _check_candidate_match(reader.get_session(session_id), output_dir)
    candidate = _find_session_by_output_dir(reader, output_dir)
    if candidate:
        return candidate
    if active:
        return _check_candidate_match(
            reader.get_active_session(engagement_id=target_eng_id), output_dir
        )
    return None


def _select_by_meeting_dir(
    args: argparse.Namespace,
    reader: LiveBufferReader,
    target_eng_id: Optional[str],
) -> Tuple[Optional[Path], Optional[Any], str]:
    output_dir = Path(args.meeting_dir).resolve()
    manifest = _matching_manifest(
        reader,
        getattr(args, "session", None),
        getattr(args, "active", False),
        output_dir,
        target_eng_id,
    )
    if manifest and target_eng_id:
        from dual_pipeline.live_meeting_recorder_core.session_identity import match_engagement

        if not match_engagement(manifest.engagement_id, target_eng_id):
            return None, None, "conflicting_engagement"
    return output_dir, manifest, output_dir.name


def _find_reconcilable_legacy_session(
    all_active: List[Any],
    target_eng_id: str,
) -> Optional[Any]:
    from dual_pipeline.live_meeting_recorder_core.session_identity import match_engagement

    legacy = [m for m in all_active if getattr(m, "engagement_id", None) is None]
    if len(legacy) != 1:
        return None
    for m in all_active:
        if m.engagement_id and not match_engagement(m.engagement_id, target_eng_id):
            return None
    return legacy[0]


def _resolve_session_id_manifest(
    session_id: str,
    target_eng_id: Optional[str],
    reader: LiveBufferReader,
) -> Tuple[Optional[Any], Optional[str]]:
    from dual_pipeline.live_meeting_recorder_core.session_identity import match_engagement

    manifest = reader.get_session(session_id)
    if not manifest:
        return None, "meeting"
    if manifest.engagement_id is not None and target_eng_id:
        if not match_engagement(manifest.engagement_id, target_eng_id):
            return None, "conflicting_engagement"
    return manifest, None


def _resolve_active_manifest(
    target_eng_id: Optional[str],
    reader: LiveBufferReader,
) -> Tuple[Optional[Any], Optional[str]]:
    from dual_pipeline.live_meeting_recorder_core.session_identity import (
        detect_engagement_conflicts,
        match_engagement,
        normalize_engagement_id,
    )

    all_active = reader.list_active_sessions()
    if all_active:
        norm_target = normalize_engagement_id(target_eng_id)
        if norm_target:
            matched = [
                m for m in all_active
                if match_engagement(m.engagement_id, norm_target) or match_engagement(m.engagement_name, norm_target)
            ]
            if matched:
                matched.sort(key=lambda m: m.last_heartbeat, reverse=True)
                return matched[0], None
        elif not detect_engagement_conflicts(all_active):
            all_active.sort(key=lambda m: m.last_heartbeat, reverse=True)
            return all_active[0], None

    if target_eng_id:
        legacy = _find_reconcilable_legacy_session(all_active, target_eng_id)
        if legacy:
            return legacy, None
        legacy_count = sum(1 for m in all_active if getattr(m, "engagement_id", None) is None)
        if legacy_count > 1:
            return None, "conflicting_engagement"

    if detect_engagement_conflicts(all_active) and not target_eng_id:
        return None, "conflicting_engagement"
    return None, "meeting"


def _resolve_manifest_candidate(
    session_id: Optional[str],
    target_eng_id: Optional[str],
    reader: LiveBufferReader,
) -> Tuple[Optional[Any], Optional[str]]:
    if session_id:
        return _resolve_session_id_manifest(session_id, target_eng_id, reader)
    return _resolve_active_manifest(target_eng_id, reader)


def _select_session(
    args: argparse.Namespace, reader: LiveBufferReader
) -> Tuple[Optional[Path], Optional[Any], str]:
    from dual_pipeline.live_meeting_recorder_core.session_identity import (
        normalize_engagement_id,
        reconcile_legacy_session,
    )

    req_engagement = getattr(args, "project", None) or getattr(args, "engagement", None)
    target_eng_id = normalize_engagement_id(req_engagement) if req_engagement else None

    if getattr(args, "meeting_dir", None):
        return _select_by_meeting_dir(args, reader, target_eng_id)

    if (
        not getattr(args, "active", False)
        and not getattr(args, "session", None)
        and str(getattr(args, "intent", "") or "").lower() in ("gaps", "graph-context")
        and target_eng_id
    ):
        if not reader.get_active_session(engagement_id=target_eng_id):
            return None, None, "meeting"

    manifest, error_slug = _resolve_manifest_candidate(
        getattr(args, "session", None), target_eng_id, reader
    )
    if error_slug:
        return None, None, error_slug

    if getattr(manifest, "engagement_id", None) is None:
        if not target_eng_id:
            return None, manifest, "unreconciled_legacy_session"
        manifest = reconcile_legacy_session(
            manifest,
            target_eng_id,
            req_engagement,
            registry_dir=reader.registry.registry_dir,
        )

    out_dir = (Path(manifest.repo_root) / manifest.output_dir).resolve() if manifest.output_dir else None
    return out_dir, manifest, manifest.meeting_slug or "meeting"


def _build_default_reader(args: argparse.Namespace) -> LiveBufferReader:
    runtime_root = Path(getattr(args, "runtime_root", None) or ROOT).resolve()
    registry_root = ROOT
    if (runtime_root / ".session_registry").is_dir():
        manifests = list((runtime_root / ".session_registry").glob("*.json"))
        if any(not f.name.startswith(".tmp_") for f in manifests):
            registry_root = runtime_root
    return LiveBufferReader(registry_root)


def _resolve_session_and_context_model(
    args: argparse.Namespace,
    reader: Optional[LiveBufferReader] = None,
) -> Tuple[Optional[Path], ConversationContextModel, Optional[Any], str]:
    """Resolves meeting output directory, reconstructed context model, manifest, and meeting slug."""
    reader = reader or _build_default_reader(args)
    output_dir, manifest, meeting_slug = _select_session(args, reader)

    if meeting_slug in ("conflicting_engagement", "unreconciled_legacy_session"):
        return None, ConversationContextModel(), manifest, meeting_slug

    if not output_dir and not manifest:
        return None, ConversationContextModel(), None, "meeting"

    context_scope = getattr(manifest, "engagement_id", None) or meeting_slug
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import _infer_attendees_from_dirs

    manifest_attendees = list(getattr(manifest, "attendees", []) or _infer_attendees_from_dirs([output_dir]))
    context_model = ConversationContextModel(
        project_scope=context_scope,
        attendees=manifest_attendees,
        focus_seconds=getattr(args, "_parsed_window_seconds", None),
    )
    raw_req = getattr(args, "request", None) or getattr(args, "topic", None)
    if raw_req:
        context_model._raw_request = str(raw_req)
    turns = reader.read_turns(manifest) if manifest else _read_turns_from_jsonl(output_dir, attendees=manifest_attendees)

    for t in turns:
        context_model.add_turn(
            speaker=t["speaker"],
            text=t["text"],
            timestamp=t["timestamp"],
            turn_sec=t.get("turn_sec"),
            is_sibling_session=bool(t.get("is_sibling_session")),
        )

    return output_dir, context_model, manifest, meeting_slug


def _resolve_session_and_transcript(
    args: argparse.Namespace,
) -> Tuple[Optional[Path], str, Optional[Any], str]:
    """Legacy helper resolving output_dir and transcript text from context model focus turns."""
    output_dir, model, manifest, slug = _resolve_session_and_context_model(args)
    focus = model.focus_turns()
    transcript = "\n".join(f"{t['speaker']}: {t['text']}" for t in focus) if focus else ""
    return output_dir, transcript, manifest, slug
