"""Live session context bridge for real-time meeting transcription caches."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def find_active_sessions(
    repo_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Scan .local/sessions/ for active or recent recording session metadata."""
    if repo_root is None:
        repo_root = Path.cwd()

    sessions_dir = repo_root / ".local" / "sessions"
    if not sessions_dir.is_dir():
        # Also check workspace parent if running inside a worktree
        parent_candidate = repo_root.parent.parent / ".local" / "sessions"
        if parent_candidate.is_dir():
            sessions_dir = parent_candidate
        else:
            return []

    active_sessions: List[Dict[str, Any]] = []
    for sdir in sorted(sessions_dir.glob("rec_*"), reverse=True):
        if not sdir.is_dir():
            continue
        req_file = sdir / "request.json"
        state_file = sdir / "session_state.json"
        req_data: Dict[str, Any] = {}
        state_data: Dict[str, Any] = {}

        if req_file.is_file():
            try:
                req_data = json.loads(req_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("Failed reading %s: %s", req_file, e)

        if state_file.is_file():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("Failed reading %s: %s", state_file, e)

        active_sessions.append(
            {
                "session_id": sdir.name,
                "session_dir": str(sdir),
                "project_name": req_data.get("project_name", ""),
                "meeting_name": req_data.get("meeting_name", ""),
                "attendees": req_data.get("attendees", ""),
                "state": state_data.get("state", "UNKNOWN"),
                "mtime": sdir.stat().st_mtime,
            }
        )

    return active_sessions


def extract_transcript_cache_turns(
    cache_path: Path,
    limit: int = 30,
) -> List[Dict[str, str]]:
    """Parse transcript cache lines into structured turns."""
    if not cache_path.is_file():
        return []

    content = cache_path.read_text(encoding="utf-8")
    turn_pattern = re.compile(
        r"\*\*([^*]+)\*\*\s*(?:\[([0-9:]+)\])?:\s*(.+)", re.DOTALL
    )
    turns: List[Dict[str, str]] = []

    for block in content.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        match = turn_pattern.match(block)
        if match:
            speaker = match.group(1).strip()
            timestamp = match.group(2) or "00:00:00"
            text = " ".join(match.group(3).split())
            turns.append(
                {
                    "speaker": speaker,
                    "timestamp": timestamp,
                    "text": text,
                }
            )
        else:
            turns.append(
                {
                    "speaker": "Unknown",
                    "timestamp": "00:00:00",
                    "text": " ".join(block.split()),
                }
            )

    return turns[-limit:] if limit else turns


def resolve_live_context_for_entities(
    entities: List[str],
    repo_root: Optional[Path] = None,
    max_turns: int = 25,
) -> List[Dict[str, Any]]:
    """Search active transcript caches for turns relevant to the queried entities."""
    if repo_root is None:
        repo_root = Path.cwd()

    cache_dir = repo_root / "output" / "transcript_cache"
    if not cache_dir.is_dir():
        parent_candidate = repo_root.parent.parent / "output" / "transcript_cache"
        if parent_candidate.is_dir():
            cache_dir = parent_candidate
        else:
            return []

    entity_tokens = {
        tok.lower()
        for ent in entities
        for tok in re.findall(r"[a-zA-Z0-9]+", ent)
        if len(tok) >= 3 and tok.lower() not in {"the", "and", "for", "view", "debrief", "pause"}
    }

    if not entity_tokens:
        return []

    cache_files = sorted(
        cache_dir.glob("*.transcript_cache.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    records: List[Dict[str, Any]] = []

    for cfile in cache_files[:5]:
        turns = extract_transcript_cache_turns(cfile, limit=max_turns)
        matching_turns = []
        filename_lower = cfile.name.lower()
        filename_matched = any(tok in filename_lower for tok in entity_tokens)

        for turn in turns:
            turn_text_lower = turn["text"].lower()
            turn_speaker_lower = turn["speaker"].lower()
            if filename_matched or any(
                tok in turn_text_lower or tok in turn_speaker_lower
                for tok in entity_tokens
            ):
                matching_turns.append(turn)

        if matching_turns:
            retrieval_text = " | ".join(
                f"{t['speaker']} [{t['timestamp']}]: {t['text']}"
                for t in matching_turns[:8]
            )
            records.append(
                {
                    "record_id": f"live-cache-{cfile.stem[:24]}",
                    "record_type": "live_transcript_turn",
                    "lane": "candidate",
                    "title": f"Live Transcript Cache: {cfile.stem}",
                    "retrieval_text": retrieval_text,
                    "evidence_ids": [f"ev-live-{cfile.stem[:16]}"],
                    "materiality": "medium",
                    "currentness_state": "provisional_live",
                    "turns": matching_turns[:8],
                }
            )

    return records
