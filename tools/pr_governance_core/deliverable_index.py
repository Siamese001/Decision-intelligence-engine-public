"""tools/pr_governance_core/deliverable_index.py

Canonical and sibling worktree deliverable indexing for PR governance.
Scans output directories for PACKAGE_READY/PASS release decisions, metadata,
and meeting identities to detect cross-worktree deliverable completion.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

_DATE_RE = re.compile(r"\b(\d{2})[-/](\d{2})[-/](\d{4})\b")
_STOP_TOKENS = {
    "live", "feat", "transcript", "ingest", "meeting", "native", "trans",
    "chat", "the", "and", "for", "with", "2026", "master", "output",
    "inputs", "input", "attempts", "attempt", "hitl", "resolved",
    "executive", "recap", "canonical", "signal", "projection", "json", "md",
}
_MAIN_DELIVERABLE_CACHE: Dict[Path, List[Dict[str, Any]]] = {}


def _normalize_date_str(raw: Optional[str]) -> str:
    if not raw:
        return ""
    m = _DATE_RE.search(str(raw))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _extract_meeting_tokens(text: Optional[str]) -> Set[str]:
    if not text:
        return set()
    parts = re.split(r"[^a-z0-9]+", str(text).lower())
    return {p for p in parts if len(p) >= 3 and p not in _STOP_TOKENS and not p.isdigit()}


def _parse_release_decision_entry(f: Path, root: Path) -> Optional[Dict[str, Any]]:
    if "attempts" in f.parts:
        return None
    try:
        dec = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    status = str(
        dec.get("release_decision")
        or dec.get("status")
        or dec.get("decision")
        or dec.get("terminal_outcome")
        or ""
    ).upper()
    if status not in ("PASS", "PACKAGE_READY"):
        return None
    parent = f.parent
    rel_dir = str(parent.relative_to(root))
    meta_p = parent / "metadata.json"
    meta: Dict[str, Any] = {}
    if meta_p.is_file():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            meta = {}
    date_str = _normalize_date_str(meta.get("date")) or _normalize_date_str(rel_dir)
    project = str(meta.get("project") or (parent.parts[-3] if len(parent.parts) >= 3 else "")).lower()
    m_name = str(meta.get("meeting_name") or "").strip()
    tokens = _extract_meeting_tokens(m_name) | _extract_meeting_tokens(parent.name)
    return {
        "canonical_dir": rel_dir,
        "project": project,
        "date": date_str,
        "meeting_name": m_name,
        "directive_id": str(meta.get("directive_id") or "").strip(),
        "tokens": tokens,
    }


def build_canonical_main_deliverable_index(root: Path) -> List[Dict[str, Any]]:
    """Indexes PACKAGE_READY meeting deliverables on main by date, project, and meeting identity."""
    resolved = root.resolve()
    if resolved in _MAIN_DELIVERABLE_CACHE:
        return _MAIN_DELIVERABLE_CACHE[resolved]
    out_dir = resolved / "output"
    items: List[Dict[str, Any]] = []
    if out_dir.is_dir():
        for f in out_dir.glob("**/final_release_decision.json"):
            entry = _parse_release_decision_entry(f, resolved)
            if entry:
                items.append(entry)
    _MAIN_DELIVERABLE_CACHE[resolved] = items
    return items


def _is_collapsed_or_preview_transcript(file_path: Path) -> bool:
    """Detects LIVE_PREVIEW or single-speaker collapsed live transcripts."""
    if not file_path.is_file():
        return False
    try:
        txt = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    if "LIVE_PREVIEW" in txt:
        return True
    speakers = re.findall(r"\*\*\[[\d:]+\]\*\*\s+([^:]+):", txt)
    return bool(len(speakers) >= 5 and len(set(speakers)) == 1)


def _collect_worktree_session_clues(wt_path: Path, residual_files: List[str]) -> Dict[str, Any]:
    dates: Set[str] = set()
    projects: Set[str] = set()
    meeting_names: Set[str] = set()
    directives: Set[str] = set()
    tokens: Set[str] = _extract_meeting_tokens(wt_path.name)
    has_qa_failure = False
    has_collapsed_preview = False

    checked_dirs: Set[Path] = set()
    for rel in residual_files:
        d_str = _normalize_date_str(rel)
        if d_str:
            dates.add(d_str)
        tokens |= _extract_meeting_tokens(rel)
        full_p = wt_path / rel
        if rel.endswith("_live_transcript.md") and _is_collapsed_or_preview_transcript(full_p):
            has_collapsed_preview = True
        if rel.endswith("judge_failure_diagnostics.json"):
            has_qa_failure = True
        for cand_dir in (full_p.parent, full_p.parent.parent):
            if cand_dir in checked_dirs or not cand_dir.is_dir():
                continue
            checked_dirs.add(cand_dir)
            meta_p = cand_dir / "metadata.json"
            if meta_p.is_file():
                try:
                    m = json.loads(meta_p.read_text(encoding="utf-8", errors="ignore"))
                    if m.get("date"):
                        dates.add(_normalize_date_str(m.get("date")))
                    if m.get("project"):
                        projects.add(str(m["project"]).lower())
                    if m.get("meeting_name"):
                        meeting_names.add(str(m["meeting_name"]).strip().lower())
                        tokens |= _extract_meeting_tokens(m["meeting_name"])
                    if m.get("directive_id"):
                        directives.add(str(m["directive_id"]).strip())
                    if str(m.get("status") or "").upper() in ("PARTIAL_QA_FAILURE", "FAILED", "FAIL"):
                        has_qa_failure = True
                except Exception:
                    pass
    return {
        "dates": {d for d in dates if d},
        "projects": projects,
        "meeting_names": meeting_names,
        "directives": directives,
        "tokens": tokens,
        "has_qa_failure": has_qa_failure,
        "has_collapsed_preview": has_collapsed_preview,
    }


def _matches_canonical_entry(clues: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    if not entry["date"] or entry["date"] not in clues["dates"]:
        return False
    if entry["directive_id"] and entry["directive_id"] in clues["directives"]:
        return True
    e_name = entry["meeting_name"].lower()
    if e_name and e_name in clues["meeting_names"]:
        return True
    shared = clues["tokens"] & entry["tokens"]
    if entry["project"] and entry["project"] in clues["projects"] and len(shared) >= 1:
        return True
    return len(shared) >= 2


def build_sibling_worktree_deliverable_index(
    root: Path, exclude_wt: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Indexes completed 01-03 meeting deliverables in other active worktrees."""
    resolved = root.resolve()
    wt_dir = resolved / ".worktrees"
    if not wt_dir.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    exclude_resolved = exclude_wt.resolve() if exclude_wt else None
    for child in wt_dir.iterdir():
        if not child.is_dir() or (exclude_resolved and child.resolve() == exclude_resolved):
            continue
        out_dir = child / "output"
        if not out_dir.is_dir():
            continue
        for dec_file in out_dir.glob("**/final_release_decision.json"):
            entry = _parse_release_decision_entry(dec_file, child)
            if entry:
                entry["worktree_name"] = child.name
                items.append(entry)
    return items
