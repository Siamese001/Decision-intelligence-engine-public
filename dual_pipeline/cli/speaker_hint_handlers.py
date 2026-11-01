"""
dual_pipeline/cli/speaker_hint_handlers.py

CLI handler for `nee speaker-hint`.
Matches quoted text against live or recorded transcript segments internally,
binds the matching acoustic voice cluster to the operator-provided speaker name,
retroactively updates segments and markdown, logs conflicts, and writes an
authentic Ed25519-signed record to hitl_decision_ledger.json.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.speaker_hints import SpeakerHintManager

logger = logging.getLogger("dual_pipeline.cli.speaker_hint_handlers")


def _update_manifest_files_attendees(candidate_manifest_files: List[Path], new_attendees: List[Any]) -> None:
    seen_paths: set[Path] = set()
    for mf in candidate_manifest_files:
        if mf in seen_paths or not mf.is_file() or mf.name.startswith(".tmp_"):
            continue
        seen_paths.add(mf)
        try:
            m_data = json.loads(mf.read_text(encoding="utf-8"))
            m_data["attendees"] = list(new_attendees)
            tmp_mf = mf.parent / f".tmp_{mf.name}_{sys.platform}"
            tmp_mf.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
            tmp_mf.replace(mf)
        except Exception as e:
            logger.debug("Failed updating manifest file %s: %s", mf, e)


def _sync_attendee_rosters(
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    new_attendees: List[Any],
) -> None:
    """Synchronizes attendee changes to disk manifests and .session_registry."""
    candidate_manifest_files: List[Path] = []
    for d in (target_cache_dir, target_output_dir):
        if d and d.is_dir():
            candidate_manifest_files.extend([*list(d.glob("*_manifest.json")), d / "session_manifest.json"])
    _update_manifest_files_attendees(candidate_manifest_files, new_attendees)
    if session_id:
        try:
            from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry

            reg = SessionRegistry(repo_root)
            reg.update_attendees(session_id, [a.get("name") if isinstance(a, dict) else str(a) for a in new_attendees])
        except Exception as e:
            logger.debug("Failed updating session registry for %s: %s", session_id, e)


def _remove_attendee_from_roster(
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    attendees: List[Any],
    attendee_to_remove: str,
) -> Tuple[List[Any], bool]:
    """Removes an attendee matching name and persists the change to manifests."""
    target_clean = attendee_to_remove.strip().lower()
    updated: List[Any] = []
    removed = False

    for a in attendees:
        name = a.get("name") if isinstance(a, dict) else str(a)
        name_clean = name.strip().lower()
        if name_clean == target_clean or target_clean in name_clean:
            removed = True
            continue
        updated.append(a)

    if removed:
        _sync_attendee_rosters(target_output_dir, target_cache_dir, session_id, repo_root, updated)
    return updated, removed


def _read_first_manifest_metadata(
    manifest_candidates: List[Path],
    meeting_slug: str,
    attendees: List[Any],
    project_name: Optional[str],
    target_segments_path: Optional[Path],
) -> Tuple[str, List[Any], Optional[str], Optional[Path]]:
    for mf in manifest_candidates:
        if not mf.is_file():
            continue
        try:
            m_data = json.loads(mf.read_text(encoding="utf-8"))
            meeting_slug = m_data.get("meeting", meeting_slug)
            attendees = attendees or m_data.get("attendees", attendees)
            project_name = project_name or m_data.get("project", project_name)
            if not target_segments_path and m_data.get("segments_jsonl") and Path(m_data["segments_jsonl"]).is_file():
                target_segments_path = Path(m_data["segments_jsonl"])
            break
        except Exception:
            continue
    return meeting_slug, attendees, project_name, target_segments_path


def _resolve_sibling_tc_dir(target_output_dir: Path) -> Optional[Path]:
    if len(target_output_dir.parents) >= 3 and target_output_dir.parents[2].name == "output":
        cand_tc = target_output_dir.parents[2] / "transcript_cache" / target_output_dir.parents[1].name / target_output_dir.parent.name
        if cand_tc.is_dir():
            return cand_tc
    return None


def _resolve_target_paths_from_dir(
    meeting_dir_arg: str,
) -> Tuple[Path, Optional[Path], Optional[Path], Optional[Path], str, List[Any], Optional[str], None]:
    target_output_dir = Path(meeting_dir_arg).resolve()
    meeting_slug = target_output_dir.name
    sibling_tc_dir = _resolve_sibling_tc_dir(target_output_dir)
    sub_caches = [d for d in (target_output_dir / ".cache", target_output_dir / "cache") if d.is_dir()]
    candidates = [
        target_output_dir / ".cache" / "segments.jsonl",
        target_output_dir / "cache" / "segments.jsonl",
        target_output_dir / "segments.jsonl",
        *list(target_output_dir.glob("*segments*.jsonl")),
        *[p for d in sub_caches for p in d.glob("*segments*.jsonl")],
        *(list(sibling_tc_dir.glob(f"*{meeting_slug}*segments*.jsonl")) + list(sibling_tc_dir.glob("*segments*.jsonl")) if sibling_tc_dir else []),
    ]
    target_segments_path = next((c for c in candidates if c.is_file()), None)
    target_cache_dir = target_segments_path.parent if target_segments_path else (sub_caches[0] if sub_caches else sibling_tc_dir)
    md_candidates = [*list(target_output_dir.glob("*live_transcript*.md")), *list(target_output_dir.glob("*.md")), *(list(sibling_tc_dir.glob("*live_transcript*.md")) if sibling_tc_dir else [])]
    target_md_path = md_candidates[0] if md_candidates else None
    base_m = target_cache_dir or target_output_dir
    manifest_candidates = [base_m / "session_manifest.json", *list(base_m.glob("*_manifest.json")), *list(target_output_dir.glob("*manifest.json")), *(list(sibling_tc_dir.glob("*manifest.json")) if sibling_tc_dir else [])]
    meeting_slug, attendees, project_name, target_segments_path = _read_first_manifest_metadata(
        manifest_candidates, meeting_slug, [], None, target_segments_path
    )
    return target_output_dir, target_cache_dir, target_segments_path, target_md_path, meeting_slug, attendees, project_name, None


def _find_session_segments_path(
    manifest: Any,
    target_cache_dir: Path,
    target_output_dir: Path,
    meeting_slug: str,
    project_name: Optional[str],
    repo_root: Path,
) -> Optional[Path]:
    raw_seg = getattr(manifest, "segments_jsonl", None)
    if raw_seg:
        for p in (Path(raw_seg), Path(manifest.repo_root) / raw_seg):
            if p.is_file():
                return p.resolve()
    candidates = [
        target_cache_dir / "segments.jsonl",
        target_output_dir / "segments.jsonl",
        *(list(target_cache_dir.glob(f"*{meeting_slug}*segments*.jsonl")) if target_cache_dir.is_dir() else []),
        *(list(target_output_dir.glob(f"*{meeting_slug}*segments*.jsonl")) if target_output_dir.is_dir() else []),
    ]
    if project_name:
        for base in (Path(manifest.repo_root), repo_root):
            tc_dir = base / "output" / "transcript_cache" / project_name
            if tc_dir.is_dir():
                candidates.extend(tc_dir.glob(f"**/*{meeting_slug}*segments*.jsonl"))
    tc_root = repo_root / "output" / "transcript_cache"
    if tc_root.is_dir():
        candidates.extend(tc_root.glob(f"**/*{meeting_slug}*segments*.jsonl"))
    return next((c for c in candidates if c.is_file()), None)


def _resolve_target_paths_from_session(
    args: argparse.Namespace,
    repo_root: Path,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path], Optional[Path], str, List[Any], Optional[str], Optional[str]]:
    reader = LiveBufferReader(repo_root)
    session_id = getattr(args, "session", None)
    manifest = reader.get_session(session_id) if session_id else reader.get_active_session()
    if not manifest:
        return None, None, None, None, "meeting", [], None, None

    meeting_slug = manifest.meeting_slug or "meeting"
    target_output_dir = (Path(manifest.repo_root) / manifest.output_dir).resolve() if manifest.output_dir else repo_root
    target_cache_dir = (
        (Path(manifest.repo_root) / manifest.cache_dir).resolve()
        if getattr(manifest, "cache_dir", None)
        else (target_output_dir / "cache" if (target_output_dir / "cache").is_dir() else target_output_dir)
    )
    target_md_path = (Path(manifest.repo_root) / manifest.transcript_md) if manifest.transcript_md else None
    project_name = manifest.engagement_name or manifest.engagement_id or getattr(args, "project", None)
    attendees: List[Any] = list(manifest.attendees or [])
    target_segments_path = _find_session_segments_path(manifest, target_cache_dir, target_output_dir, meeting_slug, project_name, repo_root)
    if not getattr(manifest, "cache_dir", None) and target_segments_path:
        target_cache_dir = target_segments_path.parent
    m_cands = [
        target_cache_dir / "session_manifest.json",
        target_output_dir / "session_manifest.json",
        *(list(target_cache_dir.glob("*_manifest.json")) if target_cache_dir.is_dir() else []),
        *(list(target_output_dir.glob("*_manifest.json")) if target_output_dir.is_dir() else []),
    ]
    _, attendees, project_name, target_segments_path = _read_first_manifest_metadata(
        m_cands, meeting_slug, attendees, project_name, target_segments_path
    )
    return target_output_dir, target_cache_dir, target_segments_path, target_md_path, meeting_slug, attendees, project_name, manifest.session_id


def _match_session_attendee(clean: str, clean_lower: str, session_attendees: List[Any]) -> Optional[str]:
    from dual_pipeline.live_meeting_recorder_core.attendees import resolve_roster_name

    roster_resolved = resolve_roster_name(clean, session_attendees)
    if roster_resolved and roster_resolved.lower() != clean_lower:
        return roster_resolved
    for att in session_attendees:
        att_name = (att.get("name") if isinstance(att, dict) else str(att)).strip()
        if att_name and att_name.lower() == clean_lower:
            return att_name
    return None


def _resolve_speaker_name(
    speaker: str,
    project_name: Optional[str] = None,
    session_attendees: Optional[List[Any]] = None,
) -> str:
    if not speaker:
        return speaker
    clean = speaker.strip()
    if session_attendees:
        matched = _match_session_attendee(clean, clean.lower().rstrip("."), session_attendees)
        if matched:
            return matched
    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import PreSynthesisEntityResolver

        resolved = PreSynthesisEntityResolver().resolve(speaker, project_name=project_name)
        if resolved and resolved.canonical_name:
            return resolved.canonical_name
    except Exception:
        pass
    return speaker


def _resolve_repo_root(args: argparse.Namespace) -> Path:
    rr = getattr(args, "runtime_root", None)
    if rr is not None:
        return Path(rr).resolve()
    return Path.cwd().resolve() if (Path.cwd() / ".session_registry").is_dir() else ROOT.resolve()


def _handle_remove_attendee(
    remove_attendee_arg: str,
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    attendees: List[Any],
    is_json: bool,
) -> Dict[str, Any]:
    updated_attendees, removed = _remove_attendee_from_roster(
        target_output_dir=target_output_dir,
        target_cache_dir=target_cache_dir,
        session_id=session_id,
        repo_root=repo_root,
        attendees=attendees,
        attendee_to_remove=remove_attendee_arg,
    )
    if removed:
        if not is_json:
            print(f"✅ [ATTENDEE-REMOVED] Removed '{remove_attendee_arg}' from active session roster ({len(updated_attendees)} remaining).")
        return {"status": "PASS", "command": "speaker-hint", "action": "ATTENDEE_REMOVED", "removed_attendee": remove_attendee_arg, "remaining_count": len(updated_attendees)}
    if not is_json:
        print(f"⚠️ [ATTENDEE-NOT-FOUND] '{remove_attendee_arg}' was not found in active session roster.")
    return {"status": "PASS", "command": "speaker-hint", "action": "ATTENDEE_NOT_FOUND", "target_attendee": remove_attendee_arg, "current_count": len(attendees)}


def _persist_role_hint_to_files(
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    hint_id: str,
    speaker: str,
    quote: str,
    role_hint: str,
    org_hint: str,
) -> None:
    for target_d in [d for d in (target_output_dir, target_cache_dir) if d and d.is_dir()]:
        sh_path = target_d / "speaker_hints.json"
        existing_sh: List[Dict[str, Any]] = []
        if sh_path.is_file():
            try:
                loaded_sh = json.loads(sh_path.read_text(encoding="utf-8"))
                if isinstance(loaded_sh, list):
                    existing_sh = loaded_sh
            except Exception:
                existing_sh = []
        existing_sh.append({"hint_id": hint_id, "speaker_name": speaker, "quote": quote, "role": role_hint, "org": org_hint or None, "attribution_type": "role_hint", "status": "APPLIED"})
        sh_path.write_text(json.dumps(existing_sh, indent=2), encoding="utf-8")


def _upsert_attendee_role_entry(attendees: List[Any], speaker: str, role_hint: str, org_hint: str) -> None:
    new_entry = {"name": speaker, "role": role_hint, **({"org": org_hint} if org_hint else {})}
    for idx, att in enumerate(attendees):
        att_name = (att.get("name") if isinstance(att, dict) else str(att)).split("(")[0].strip()
        if att_name.lower() == speaker.strip().lower():
            attendees[idx] = {**att, **new_entry} if isinstance(att, dict) else new_entry
            return
    attendees.append(new_entry)


def _apply_role_hint_update(
    attendees: List[Any],
    speaker: str,
    role_hint: str,
    org_hint: str,
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    queued_hint: Any,
    quote: str,
) -> None:
    _upsert_attendee_role_entry(attendees, speaker, role_hint, org_hint)
    _sync_attendee_rosters(target_output_dir, target_cache_dir, session_id, repo_root, attendees)
    _persist_role_hint_to_files(target_output_dir, target_cache_dir, queued_hint.hint_id, speaker, quote, role_hint, org_hint)


def _handle_missing_segments_hint(
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    speaker: str,
    quote: str,
    hint_queue: Any,
    queued_hint: Any,
    is_new: bool,
    is_json: bool,
) -> Dict[str, Any]:
    from .speaker_hint_post_recording import reconcile_post_recording_utterances

    post_rec = reconcile_post_recording_utterances(
        target_output_dir=target_output_dir, target_cache_dir=target_cache_dir, speaker=speaker, quote=quote, hint_queue=hint_queue, queued_hint=queued_hint
    )
    if post_rec:
        if not is_json:
            print(f"✅ [SPEAKER-HINT-APPLIED] {post_rec['details']}\n   Voice Cluster: {post_rec['voice_id']} -> {post_rec['speaker_name']}\n   Updated Segments: {post_rec['updated_segments_count']}")
        return post_rec
    msg = f"Segments JSONL file not found for session in {target_output_dir}. Hint queued in durable queue for async reconciliation."
    if not is_json:
        print(f"⏳ [SPEAKER-HINT-QUEUED] {msg}")
    return {"status": "PASS", "command": "speaker-hint", "action": "QUEUED", "hint_id": queued_hint.hint_id, "speaker_name": speaker, "quote": quote, "is_new": is_new, "details": msg}


def _finalize_applied_hint_result(
    result: Any,
    hint_queue: Any,
    queued_hint: Any,
    attendees: List[Any],
    role_hint: str,
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    is_json: bool,
) -> Dict[str, Any]:
    hint_queue.mark_reconciled(
        hint_id=queued_hint.hint_id, voice_id=result.voice_id or "Voice A", updated_segments_count=result.updated_segments_count, ledger_digest=result.ledger_digest
    )
    existing_names = {(a.get("name") if isinstance(a, dict) else str(a)).split("(")[0].strip().lower() for a in attendees}
    if result.speaker_name and result.speaker_name.strip().lower() not in existing_names:
        attendees.append({"name": result.speaker_name, "role": role_hint} if role_hint else result.speaker_name)
        _sync_attendee_rosters(target_output_dir, target_cache_dir, session_id, repo_root, attendees)
    if not is_json:
        print(f"✅ [SPEAKER-HINT-APPLIED] {result.details}\n   Voice Cluster: {result.voice_id} -> {result.speaker_name}\n   Updated Segments: {result.updated_segments_count}")
        if result.ledger_digest:
            print(f"   Ledger Digest: {result.ledger_digest}")
        if result.conflicts_logged > 0:
            print(f"   ⚠️ Conflicts Logged: {result.conflicts_logged} (hint override recorded)")
    return {"status": "PASS", "command": "speaker-hint", "action": "APPLIED", **result.to_dict()}


def _validate_speaker_hint_inputs(raw_speaker: str, quote: str, role_hint: str, is_json: bool) -> Optional[Dict[str, Any]]:
    if not raw_speaker:
        if not is_json:
            print("❌ [SPEAKER-HINT-ERROR] Must provide non-empty --speaker.", file=sys.stderr)
        return {"status": "FAIL", "command": "speaker-hint", "error": "EMPTY_SPEAKER"}
    if not quote and not role_hint:
        if not is_json:
            print("❌ [SPEAKER-HINT-ERROR] Must provide non-empty --quote or --role.", file=sys.stderr)
        return {"status": "FAIL", "command": "speaker-hint", "error": "EMPTY_QUOTE"}
    return None


def _dispatch_manager_result(
    result: Any,
    hint_queue: Any,
    queued_hint: Any,
    attendees: List[Any],
    role_hint: str,
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    session_id: Optional[str],
    repo_root: Path,
    speaker: str,
    quote: str,
    is_new: bool,
    is_json: bool,
) -> Dict[str, Any]:
    if result.status == "PASS":
        return _finalize_applied_hint_result(result, hint_queue, queued_hint, attendees, role_hint, target_output_dir, target_cache_dir, session_id, repo_root, is_json)
    if result.error == "QUOTE_NOT_FOUND":
        msg = "Quote not yet transcribed in active segments. Hint queued in durable queue for async reconciliation."
        if not is_json:
            print(f"⏳ [SPEAKER-HINT-QUEUED] {msg}")
        return {"status": "PASS", "command": "speaker-hint", "action": "QUEUED", "hint_id": queued_hint.hint_id, "speaker_name": speaker, "quote": quote, "is_new": is_new, "details": msg}
    if not is_json:
        print(f"❌ [SPEAKER-HINT-FAILED] {result.error}: {result.details}", file=sys.stderr)
    return {"status": "FAIL", "command": "speaker-hint", **result.to_dict()}


def _build_hint_meta(role_hint: str, org_hint: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if role_hint:
        meta["role"] = role_hint
        meta["attribution_type"] = "role_hint"
    if org_hint:
        meta["org"] = org_hint
    return meta


def _elapsed_from_segments_file(target_segments_path: Optional[Path]) -> Optional[float]:
    if not (target_segments_path and target_segments_path.is_file()):
        return None
    with contextlib.suppress(Exception):
        lines = [ln.strip() for ln in target_segments_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            last_seg = json.loads(lines[-1])
            if isinstance(last_seg, dict):
                end_s = last_seg.get("end_sec", last_seg.get("start_sec", last_seg.get("seconds")))
                if end_s is not None:
                    return max(0.0, float(end_s))
    return None


def _elapsed_from_start_receipt(dirs: Tuple[Optional[Path], ...]) -> Optional[float]:
    from datetime import datetime, timezone
    for d in dirs:
        if not (d and (d / "record_start_receipt.json").is_file()):
            continue
        with contextlib.suppress(Exception):
            rc = json.loads((d / "record_start_receipt.json").read_text(encoding="utf-8"))
            ts_str = rc.get("started_at") or rc.get("timestamp")
            if ts_str:
                started_dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                return max(0.0, round((datetime.now(timezone.utc) - started_dt).total_seconds(), 2))
    return None


def _resolve_elapsed_seconds(
    args: argparse.Namespace,
    target_output_dir: Optional[Path],
    target_cache_dir: Optional[Path],
    target_segments_path: Optional[Path],
) -> Optional[float]:
    explicit = getattr(args, "elapsed_seconds", None)
    if explicit is not None:
        with contextlib.suppress(ValueError, TypeError):
            return max(0.0, float(explicit))
    return _elapsed_from_segments_file(target_segments_path) or _elapsed_from_start_receipt(
        (target_output_dir, target_cache_dir)
    )


def _safe_str(args: argparse.Namespace, attr: str) -> str:
    val = getattr(args, attr, "")
    return val.strip() if isinstance(val, str) else ""


def _execute_hint_application(
    args: argparse.Namespace,
    target_output_dir: Path,
    target_cache_dir: Optional[Path],
    target_segments_path: Optional[Path],
    target_md_path: Optional[Path],
    meeting_slug: str,
    attendees: List[Any],
    project_name: Optional[str],
    session_id: Optional[str],
    repo_root: Path,
    is_json: bool,
) -> Dict[str, Any]:
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import DurableSpeakerHintQueue

    quote, raw_speaker = _safe_str(args, "quote"), _safe_str(args, "speaker")
    role_hint, org_hint = _safe_str(args, "role"), _safe_str(args, "org")
    err_res = _validate_speaker_hint_inputs(raw_speaker, quote, role_hint, is_json)
    if err_res:
        return err_res
    speaker = _resolve_speaker_name(raw_speaker, project_name=project_name, session_attendees=attendees)
    elapsed_sec = _resolve_elapsed_seconds(args, target_output_dir, target_cache_dir, target_segments_path)
    hint_queue = DurableSpeakerHintQueue(output_dir=target_output_dir, cache_dir=target_cache_dir)
    queued_hint, is_new = hint_queue.enqueue_hint(
        speaker=speaker,
        quote=quote or f"[ROLE_HINT:{role_hint}]",
        elapsed_seconds=elapsed_sec,
        metadata=_build_hint_meta(role_hint, org_hint),
    )

    if role_hint:
        _apply_role_hint_update(attendees, speaker, role_hint, org_hint, target_output_dir, target_cache_dir, session_id, repo_root, queued_hint, quote)
        if not quote:
            if not is_json:
                print(f"✅ [ROLE-HINT-APPLIED] Bound role '{role_hint}' to '{speaker}'.")
            return {"status": "PASS", "command": "speaker-hint", "action": "ROLE_APPLIED", "hint_id": queued_hint.hint_id, "speaker_name": speaker, "role": role_hint, "org": org_hint or None}

    if not target_segments_path or not target_segments_path.is_file():
        return _handle_missing_segments_hint(target_output_dir, target_cache_dir, speaker, quote, hint_queue, queued_hint, is_new, is_json)

    manager = SpeakerHintManager(output_dir=target_output_dir, cache_dir=target_cache_dir, attendees=attendees)
    result = manager.apply_hint(
        quote=quote,
        speaker_name=speaker,
        segments_jsonl_path=target_segments_path,
        transcript_md_path=target_md_path,
        meeting_slug=meeting_slug,
        force=bool(getattr(args, "force", False)),
        elapsed_seconds=elapsed_sec,
    )
    return _dispatch_manager_result(result, hint_queue, queued_hint, attendees, role_hint, target_output_dir, target_cache_dir, session_id, repo_root, speaker, quote, is_new, is_json)


def handle_speaker_hint(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee speaker-hint` with sub-50ms resolution and live roster sync."""
    repo_root = _resolve_repo_root(args)
    meeting_dir_arg = getattr(args, "meeting_dir", None)
    remove_attendee_arg = getattr(args, "remove_attendee", None)
    resolved = _resolve_target_paths_from_dir(str(meeting_dir_arg)) if meeting_dir_arg else _resolve_target_paths_from_session(args, repo_root)
    target_output_dir, target_cache_dir, target_segments_path, target_md_path, meeting_slug, attendees, project_name, session_id = resolved
    is_json = bool(getattr(args, "json", False))

    if not target_output_dir:
        err = "No active or matching live session found in .session_registry/."
        if not is_json:
            print(f"❌ [SPEAKER-HINT-ERROR] {err}", file=sys.stderr)
        return {"status": "FAIL", "command": "speaker-hint", "error": "NO_SESSION_FOUND", "details": err}
    if remove_attendee_arg:
        return _handle_remove_attendee(remove_attendee_arg, target_output_dir, target_cache_dir, session_id, repo_root, attendees, is_json)
    return _execute_hint_application(
        args, target_output_dir, target_cache_dir, target_segments_path, target_md_path, meeting_slug, attendees, project_name, session_id, repo_root, is_json
    )


def _build_lightweight_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nee speaker-hint", add_help=False)
    p.add_argument("--quote", default="", help="Exact or approximate words spoken by the speaker")
    p.add_argument("--speaker", default="", help="Speaker name from attendee roster")
    p.add_argument("--role", default="", help="Professional title or role hint for the speaker")
    p.add_argument("--org", default="", help="Organization hint for the speaker")
    p.add_argument("--session", default=None, help="Target session ID")
    p.add_argument("--active", action="store_true", default=True, help="Target currently active session")
    p.add_argument("--meeting-dir", default=None, help="Target meeting directory")
    p.add_argument("--force", action="store_true", default=False, help="Force assignment")
    p.add_argument("--remove-attendee", default=None, help="Remove mistakenly added attendee from session")
    p.add_argument("--project", default=None, help="Project name scope")
    p.add_argument("--elapsed-seconds", dest="elapsed_seconds", type=float, default=None, help="Session elapsed seconds when hint was issued")
    p.add_argument("--runtime-root", default=None, help="Root directory")
    p.add_argument("--json", action="store_true", default=False, help="Output JSON result")
    return p


def fast_handle_speaker_hint_cli(argv: list[str]) -> int:
    """Ultra-fast CLI dispatcher bypassing heavy argument parser bootstrap."""
    parser = _build_lightweight_parser()
    parsed_args, _ = parser.parse_known_args(argv)
    res = handle_speaker_hint(parsed_args)
    if getattr(parsed_args, "json", False):
        print(json.dumps(res, indent=2))
    return 0 if res.get("status") == "PASS" else 1
