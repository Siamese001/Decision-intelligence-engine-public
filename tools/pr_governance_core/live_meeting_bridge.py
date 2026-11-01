"""
tools/pr_governance_core/live_meeting_bridge.py

Governed Live Meeting Intake Bridge and Lifecycle Status Classifier.
Bridges uncommitted live meeting worktrees (.worktrees/live_* or orphaned meeting directories)
into the PR governance triage pipeline, classifying their lifecycle status without violating
pre-commit invariants or performing unauthorized repository mutations (Decision D5).

States:
- RECORDING_IN_PROGRESS: .recording_active marker or active live transcript stream present.
- SYNTHESIS_PENDING: Audio recorded / session completed, but Artifact 01 not yet synthesized.
- STAGE_U0_READY: Artifacts 01-03 synthesized and complete, ready for intake packaging/commit.
- ORPHANED_AUDIO: Unregistered directory containing raw audio chunks only.
- ABANDONED_STUB: Empty session directory stub with no audio or deliverables.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class LiveMeetingStatus(str, Enum):
    RECORDING_IN_PROGRESS = "RECORDING_IN_PROGRESS"
    SYNTHESIS_PENDING = "SYNTHESIS_PENDING"
    STAGE_U0_READY = "STAGE_U0_READY"
    ORPHANED_AUDIO = "ORPHANED_AUDIO"
    ABANDONED_STUB = "ABANDONED_STUB"


LIVE_STATUS_BADGES: Dict[LiveMeetingStatus, str] = {
    LiveMeetingStatus.RECORDING_IN_PROGRESS: "🎙️ Recording In Progress",
    LiveMeetingStatus.SYNTHESIS_PENDING: "⏳ Synthesis Pending",
    LiveMeetingStatus.STAGE_U0_READY: "📦 Stage U0 Ready",
    LiveMeetingStatus.ORPHANED_AUDIO: "🎵 Orphaned Audio Only",
    LiveMeetingStatus.ABANDONED_STUB: "📁 Abandoned Stub",
}


def is_live_meeting_container(path: Path) -> bool:
    """Determines whether a worktree path represents a live meeting session."""
    name = path.name.lower()
    if name.startswith(("ingest_doc_", "doc_", "ingest/doc-", "feat/doc-")):
        return False
    if name.startswith((
        "audio_",
        "audio/",
        "live_",
        "feat_live_",
        "feat/live-",
        "meeting_",
        "transcript_",
        "transcript/",
        "ingest_meeting_",
        "ingest/meeting-",
    )):
        return True
    try:
        if (path / ".recording_active").exists():
            return True
        return bool(any(path.glob("*_live_transcript.md")) or any(path.glob("output/**/*_live_transcript.md")))
    except (PermissionError, OSError):
        return False


def scan_live_meeting_markers(wt_dir: Path) -> Dict[str, Any]:
    """Scans a worktree directory for live meeting artifacts, audio chunks, and state markers."""
    audio_chunks: List[Path] = []
    live_transcripts: List[Path] = []
    art01_files: List[Path] = []
    art02_files: List[Path] = []
    art03_files: List[Path] = []
    has_active_marker = False

    try:
        has_active_marker = (
            (wt_dir / ".recording_active").exists()
            or (wt_dir / "output" / ".recording_active").exists()
            or any(wt_dir.glob("output/**/.recording_active"))
        )
    except (PermissionError, OSError):
        has_active_marker = False

    try:
        if wt_dir.is_dir():
            for root, dirs, files in os.walk(wt_dir):
                dirs[:] = [
                    d for d in dirs
                    if d not in (
                        ".git", ".venv", "node_modules", ".local",
                        ".pytest_cache", ".ruff_cache", "__pycache__", ".cache"
                    )
                ]
                for f in files:
                    p = Path(root) / f
                    name = f.lower()
                    if name.endswith(".wav"):
                        audio_chunks.append(p)
                    elif name.endswith("_live_transcript.md"):
                        live_transcripts.append(p)
                    elif name.startswith("01_") and name.endswith(".md"):
                        art01_files.append(p)
                    elif name.startswith("02_") and name.endswith(".md"):
                        art02_files.append(p)
                    elif name.startswith("03_") and name.endswith(".json"):
                        art03_files.append(p)
    except (PermissionError, OSError):
        pass

    active_in_transcript = False
    for lt in live_transcripts:
        try:
            txt = lt.read_text(encoding="utf-8", errors="ignore")
            if any(k in txt for k in ("RECORDING_IN_PROGRESS", "Session paused")):
                active_in_transcript = True
                break
        except Exception:
            pass

    return {
        "has_active_marker": has_active_marker or active_in_transcript,
        "audio_chunks": [str(p) for p in audio_chunks],
        "audio_chunks_count": len(audio_chunks),
        "live_transcripts": [str(p) for p in live_transcripts],
        "art01_files": [str(p) for p in art01_files],
        "art02_files": [str(p) for p in art02_files],
        "art03_files": [str(p) for p in art03_files],
        "has_deliverables": bool(art01_files),
        "triad_complete": bool(art01_files and art02_files and art03_files),
    }


def classify_live_worktree(
    wt_dir: Path,
    is_registered: bool = True,
) -> Tuple[LiveMeetingStatus, Dict[str, Any]]:
    """
    Evaluates a live meeting directory and returns its lifecycle status and diagnostics.
    Enforces Decision D5: PR governance only classifies and routes, never deletes or mutates.
    """
    if not wt_dir.is_dir():
        return LiveMeetingStatus.ABANDONED_STUB, {"exists": False}

    markers = scan_live_meeting_markers(wt_dir)

    # 1. Active recording in progress
    if markers["has_active_marker"] and not markers["has_deliverables"]:
        return LiveMeetingStatus.RECORDING_IN_PROGRESS, markers

    # 2. Stage U0 deliverables generated
    if markers["has_deliverables"]:
        return LiveMeetingStatus.STAGE_U0_READY, markers

    # 3. Audio or session transcripts present, synthesis pending
    if markers["audio_chunks_count"] > 0 or markers["live_transcripts"]:
        if is_registered or wt_dir.name.startswith(("audio_", "live_", "feat_live_", "transcript_", "ingest_meeting_")):
            return LiveMeetingStatus.SYNTHESIS_PENDING, markers
        return LiveMeetingStatus.ORPHANED_AUDIO, markers

    # 4. Empty or abandoned stub
    return LiveMeetingStatus.ABANDONED_STUB, markers


def explain_live_meeting_action(status: LiveMeetingStatus, markers: Optional[Dict[str, Any]] = None) -> str:
    """Returns concrete actionable guidance for a live meeting status."""
    markers_dict = markers or {}
    audio_n = markers_dict.get("audio_chunks_count", 0)

    if status == LiveMeetingStatus.RECORDING_IN_PROGRESS:
        return "[WAIT_RECORDING] Live recording in progress; do not commit or prune"
    if status == LiveMeetingStatus.SYNTHESIS_PENDING:
        return (
            f"[RUN_SYNTHESIS] Audio recorded ({audio_n} chunk(s)); "
            f"execute Stage U0 synthesis via `nee ingest-meeting` or prompt synthesis"
        )
    if status == LiveMeetingStatus.STAGE_U0_READY:
        triad = "triad complete (01, 02, 03)" if markers_dict.get("triad_complete") else "Artifact 01 present"
        return f"[COMMIT_DELIVERABLES] {triad}; commit deliverables before branch merge"
    if status == LiveMeetingStatus.ORPHANED_AUDIO:
        return f"[QUARANTINE_AUDIO] Audio chunks only ({audio_n} file(s)); safe to quarantine or ingest"
    return "[QUARANTINE_STUB] Empty session stub (0 audio/deliverables); safe to quarantine"


from .deliverable_index import (
    build_canonical_main_deliverable_index,
    build_sibling_worktree_deliverable_index,
    _normalize_date_str,
    _collect_worktree_session_clues,
    _matches_canonical_entry,
)


def evaluate_worktree_meeting_supersession(
    wt_path: Path,
    residual_files: List[str],
    root: Path,
) -> Optional[Dict[str, Any]]:
    """Checks whether dirty/untracked files in a worktree belong to a meeting already PACKAGE_READY on main or superseded by a sibling worktree."""
    if not residual_files or not wt_path.is_dir():
        return None
    from .diff_semantics import is_code_file, is_deliverable_file, is_doc_file
    from .worktree import is_ephemeral_worktree_path

    # Filter out ephemeral files first
    meaningful_residuals = [f for f in residual_files if not is_ephemeral_worktree_path(f)]
    if not meaningful_residuals:
        return {
            "is_superseded": True,
            "canonical_dir": "",
            "meeting_name": wt_path.name,
            "date": "",
            "project": "",
            "worktree_state": "only ephemeral attempt checkpoints remaining",
        }

    clues = _collect_worktree_session_clues(wt_path, meaningful_residuals)
    if not clues["dates"]:
        d_str = _normalize_date_str(wt_path.name)
        if d_str:
            clues["dates"].add(d_str)

    deliverable_residuals = [f for f in meaningful_residuals if is_deliverable_file(f)]
    code_or_doc_residuals = [f for f in meaningful_residuals if is_code_file(f) or is_doc_file(f) or f.startswith("documents/")]

    # Check against canonical main deliverable index
    canonical_match = None
    if clues["dates"]:
        for entry in build_canonical_main_deliverable_index(root):
            if _matches_canonical_entry(clues, entry):
                canonical_match = entry
                break

    # Also check against sibling worktrees
    sibling_match = None
    if not canonical_match and clues["dates"]:
        for sib in build_sibling_worktree_deliverable_index(root, exclude_wt=wt_path):
            if _matches_canonical_entry(clues, sib):
                sibling_match = sib
                break

    matched = canonical_match or sibling_match
    if matched:
        c_dir = matched["canonical_dir"]
        reasons = []
        if canonical_match:
            reasons.append(f"PACKAGE_READY deliverable on main under {c_dir}")
        else:
            reasons.append(f"completed triad in sibling worktree '{sibling_match.get('worktree_name', '')}'")
        if clues["has_collapsed_preview"]:
            reasons.append("collapsed live-mic preview")
        if clues["has_qa_failure"]:
            reasons.append("PARTIAL_QA_FAILURE attempt")
        if code_or_doc_residuals:
            reasons.append(f"{len(code_or_doc_residuals)} stale in-flight scratch edit(s) superseded")

        state_desc = " & ".join(reasons)
        return {
            "is_superseded": True,
            "canonical_dir": c_dir,
            "meeting_name": matched["meeting_name"] or c_dir,
            "date": matched["date"],
            "project": matched["project"],
            "worktree_state": state_desc,
            "matched_on_main": bool(canonical_match),
            "matched_sibling": sibling_match.get("worktree_name") if sibling_match else None,
        }

    # If deliverables are present but NOT matched on main or sibling:
    # Check if this worktree has uncommitted deliverables and stale code edits
    if deliverable_residuals and code_or_doc_residuals:
        return {
            "is_superseded": False,
            "is_salvageable": True,
            "deliverable_count": len(deliverable_residuals),
            "code_count": len(code_or_doc_residuals),
            "worktree_state": f"{len(deliverable_residuals)} uncommitted deliverable(s) with {len(code_or_doc_residuals)} stale code edit(s)",
        }

    return None

