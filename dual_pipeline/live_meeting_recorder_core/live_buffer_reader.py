from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from .session_registry import SessionManifest, SessionRegistry


def _collect_ancestor_paths(start_path: Path) -> List[Path]:
    paths: List[Path] = [start_path]
    curr = start_path
    while curr != curr.parent:
        if (curr / ".session_registry").is_dir():
            paths.append(curr)
        if (curr / ".git").exists() or (curr / "pyproject.toml").is_file():
            paths.append(curr)
            if curr.parent.name == ".worktrees":
                parent_repo = curr.parent.parent
                if (parent_repo / ".session_registry").is_dir():
                    paths.append(parent_repo)
            break
        curr = curr.parent
    return paths


def _collect_worktree_registries(candidate_paths: List[Path]) -> List[Path]:
    extra_wts: List[Path] = []
    for p in candidate_paths:
        wt_dir = p / ".worktrees"
        if not wt_dir.is_dir():
            continue
        for child in wt_dir.iterdir():
            if child.is_dir() and (child / ".session_registry").is_dir():
                extra_wts.append(child)
    return extra_wts


def _deduplicate_registries(candidate_paths: List[Path], fallback: SessionRegistry) -> List[SessionRegistry]:
    seen_dirs: set[Path] = set()
    registries: List[SessionRegistry] = []
    for p in candidate_paths:
        try:
            resolved_p = p.resolve()
            reg_dir = (resolved_p / ".session_registry").resolve()
            if reg_dir not in seen_dirs and reg_dir.is_dir():
                seen_dirs.add(reg_dir)
                registries.append(SessionRegistry(resolved_p))
        except Exception:
            continue
    return registries if registries else [fallback]


class LiveBufferReader:
    """The only sanctioned reader of live transcript buffers and copilot responses."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self.registry = SessionRegistry(self.repo_root)
        self._turn_cache: Dict[str, Dict[str, Any]] = {}

    def _candidate_registries(self) -> List[SessionRegistry]:
        """Collects candidate registries, resolving canonical physical paths, worktrees, and ancestors."""
        import os
        import sys
        try:
            from dual_pipeline.core import ROOT
            canon: Optional[Path] = ROOT.resolve()
        except Exception:
            canon = None

        if os.environ.get("PYTEST_CURRENT_TEST") or "unittest" in sys.modules:
            if canon is not None and self.repo_root == canon:
                return [self.registry]
            candidate_paths = _collect_ancestor_paths(self.repo_root)
            return _deduplicate_registries(candidate_paths, self.registry)

        candidate_paths = _collect_ancestor_paths(self.repo_root)
        if canon is not None and (canon / ".session_registry").is_dir():
            candidate_paths.append(canon)
        candidate_paths.extend(_collect_worktree_registries(list(candidate_paths)))
        return _deduplicate_registries(candidate_paths, self.registry)

    @staticmethod
    def _read_manifest_file(f: Path) -> Optional[SessionManifest]:
        if f.name.startswith(".tmp_"):
            return None
        try:
            return SessionManifest.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_active_sessions(self) -> List[SessionManifest]:
        """Returns all non-stale active sessions across candidate registries."""
        active: List[SessionManifest] = []
        seen_ids: set[str] = set()
        for reg in self._candidate_registries():
            qualified = reg.qualify_stale_sessions(stale_seconds=120.0)
            stale_ids = {m.session_id for _, m, _ in qualified}
            for f in reg.registry_dir.glob("*.json"):
                manifest = self._read_manifest_file(f)
                if not manifest or manifest.session_id in stale_ids or manifest.session_id in seen_ids:
                    continue
                seen_ids.add(manifest.session_id)
                active.append(manifest)
        return active

    def get_active_session(
        self, engagement_id: Optional[str] = None, allow_conflict: bool = False,
    ) -> Optional[SessionManifest]:
        """Finds active session across candidate registries with engagement identity binding and conflict detection."""
        from .session_identity import detect_engagement_conflicts, match_engagement, normalize_engagement_id

        active = self.list_active_sessions()
        if not active:
            return None
        norm_target = normalize_engagement_id(engagement_id)
        if norm_target:
            matched = [m for m in active if match_engagement(m.engagement_id, norm_target) or match_engagement(m.engagement_name, norm_target)]
            if not matched:
                return None
            matched.sort(key=lambda m: m.last_heartbeat, reverse=True)
            return matched[0]
        conflicts = detect_engagement_conflicts(active)
        if conflicts and not allow_conflict:
            import logging
            logging.getLogger("dual_pipeline.live_meeting_recorder_core.live_buffer_reader").warning(
                "Ambiguous active sessions across distinct engagements: %s. Explicit engagement required.", conflicts,
            )
            return None
        active.sort(key=lambda m: m.last_heartbeat, reverse=True)
        return active[0]

    def get_session(self, session_id: str) -> Optional[SessionManifest]:
        """Resolves explicit session by ID across candidate registries deterministically with collision checks."""
        matches: List[SessionManifest] = []
        for reg in self._candidate_registries():
            manifest = reg.inspect(session_id)
            if manifest:
                matches.append(manifest)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        # Multiple candidate registries matched session_id: ensure they point to same physical meeting
        distinct_outputs = set()
        for m in matches:
            if m.output_dir:
                try:
                    out = (Path(m.repo_root) / m.output_dir).resolve()
                    distinct_outputs.add(str(out))
                except Exception:
                    pass
        if len(distinct_outputs) > 1:
            import logging
            logging.getLogger("dual_pipeline.live_meeting_recorder_core.live_buffer_reader").warning(
                "Ambiguous session '%s' resolves to %d distinct physical meeting output directories: %s. Failing closed.",
                session_id, len(distinct_outputs), distinct_outputs
            )
            return None

        # Return latest heartbeat
        matches.sort(key=lambda m: m.last_heartbeat, reverse=True)
        return matches[0]

    def _resolve_manifest(self, session_or_manifest: SessionManifest | str) -> Optional[SessionManifest]:
        if isinstance(session_or_manifest, SessionManifest):
            return session_or_manifest
        if isinstance(session_or_manifest, str):
            return self.get_session(session_or_manifest)
        return None

    def read_transcript(self, session_or_manifest: SessionManifest | str) -> str:
        """Reads the live transcript buffer."""
        manifest = self._resolve_manifest(session_or_manifest)
        if not manifest:
            return ""
        if manifest.transcript_md:
            path = Path(manifest.repo_root) / manifest.transcript_md
            if path.exists():
                return path.read_text(encoding="utf-8")
        return _read_fallback_live_transcript_md(manifest) if manifest.output_dir else ""

    def _resolve_manifest_output_dir(self, manifest: SessionManifest) -> Path:
        if manifest.output_dir:
            return (Path(manifest.repo_root) / manifest.output_dir).resolve()
        if getattr(manifest, "cache_dir", None):
            return (Path(manifest.repo_root) / manifest.cache_dir).resolve()
        return Path(manifest.repo_root)

    def _load_raw_turns_for_manifest(self, manifest: SessionManifest, output_dir: Path, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        cached_path: Optional[Path] = entry.get("jsonl_path")
        candidates = [cached_path] if (cached_path and cached_path.is_file()) else _find_segments_candidates(output_dir, manifest=manifest)
        for candidate in candidates:
            if candidate and candidate.is_file():
                turns = _parse_jsonl_turns_incremental(candidate, entry)
                if turns:
                    entry["jsonl_path"] = candidate
                    return _prepend_same_day_sibling_turns(output_dir, turns, candidate)
        transcript_text = self.read_transcript(manifest)
        md_turns = _parse_markdown_turns(transcript_text) if transcript_text else []
        return _prepend_same_day_sibling_turns(output_dir, md_turns, None) if md_turns else []

    def read_turns(self, session_or_manifest: SessionManifest | str) -> List[Dict[str, Any]]:
        """Read timestamped turns from the session cache or transcript buffer with incremental tail cursor."""
        manifest = self._resolve_manifest(session_or_manifest)
        if not manifest:
            return []
        output_dir = self._resolve_manifest_output_dir(manifest)
        entry = self._turn_cache.setdefault(getattr(manifest, "session_id", None) or str(output_dir), {})
        turns = self._load_raw_turns_for_manifest(manifest, output_dir, entry)
        from .speaker_hint_matcher import reconcile_turns_with_hint_queue

        cache_dir = (Path(manifest.repo_root) / manifest.cache_dir).resolve() if getattr(manifest, "cache_dir", None) else None
        hint_sig = _compute_hint_signature([output_dir, cache_dir])
        if entry.get("hint_sig") == hint_sig and entry.get("reconciled_len") == len(turns) and "reconciled_turns" in entry:
            return [dict(t) for t in entry["reconciled_turns"]]
        reconciled = reconcile_turns_with_hint_queue([dict(t) for t in turns], [output_dir, cache_dir], attendees=getattr(manifest, "attendees", None))
        entry["hint_sig"], entry["reconciled_len"], entry["reconciled_turns"] = hint_sig, len(turns), [dict(t) for t in reconciled]
        return reconciled


def _join_chunk_texts(prev_txt: str, next_txt: str) -> str:
    p_clean = re.sub(r"\.{2,}$", "", prev_txt.strip()).strip()
    n_clean = re.sub(r"^\.{2,}\s*", "", next_txt.strip()).strip()
    if not p_clean:
        return n_clean
    if not n_clean:
        return p_clean
    return f"{p_clean} {n_clean}".strip()


def _turn_bounds_sec(turn: Dict[str, Any], default_start: float = 0.0) -> Tuple[float, float]:
    s_raw = turn.get("turn_sec")
    start_sec = float(s_raw) if s_raw is not None else default_start
    e_raw = turn.get("end_sec")
    end_sec = float(e_raw) if e_raw is not None else start_sec + 5.0
    return start_sec, end_sec


_TURN_HANDOFF_RE = re.compile(
    r"^(?:yeah|yep|yes|no|nope|sure|right|totally|definitely|absolutely|good\s+question|great\s+question|thanks|thank\s+you|actually|well|i\s+think|for\s+that|to\s+answer)\b",
    re.IGNORECASE,
)


def _has_speaker_or_turn_boundary(prev: Dict[str, Any], curr: Dict[str, Any], prev_txt: str, txt: str) -> bool:
    from .copilot_roster_bridge import are_same_speaker_identity

    if bool(prev.get("is_sibling_session")) != bool(curr.get("is_sibling_session")):
        return True
    prev_spk = str(prev.get("speaker") or "").strip()
    curr_spk = str(curr.get("speaker") or "").strip()
    if prev_spk.lower() != curr_spk.lower() and not are_same_speaker_identity(prev_spk, curr_spk):
        return True
    p_vid, c_vid = str(prev.get("voice_id") or "").strip(), str(curr.get("voice_id") or "").strip()
    if p_vid and c_vid and p_vid.lower() != c_vid.lower():
        return True
    return prev_txt.endswith("?") and bool(_TURN_HANDOFF_RE.match(txt))


def _try_merge_into_prev(
    prev: Dict[str, Any], curr: Dict[str, Any], txt: str, max_pause_sec: float, max_words: int
) -> bool:
    prev_txt = str(prev.get("text") or "").strip()
    if _has_speaker_or_turn_boundary(prev, curr, prev_txt, txt):
        return False
    _, prev_end = _turn_bounds_sec(prev, 0.0)
    curr_start, curr_end = _turn_bounds_sec(curr, prev_end)
    gap = curr_start - prev_end
    prev_words = len(prev_txt.split())
    comb_words = prev_words + len(txt.split())
    if not (-2.0 <= gap <= max_pause_sec and comb_words <= max_words):
        return False
    if prev_words >= 28 and prev_txt.endswith((".", "!", "?")) and not prev_txt.endswith("...") and txt[:1].isupper():
        return False
    prev["text"] = _join_chunk_texts(prev_txt, txt)
    prev["end_sec"] = curr_end
    start_ts = str(prev.get("start_timestamp") or prev.get("timestamp") or "").split("–")[0].strip("[] ")
    end_ts = str(curr.get("timestamp") or "").split("–")[-1].strip("[] ")
    if start_ts and end_ts and start_ts != end_ts:
        prev["start_timestamp"] = start_ts
        prev["timestamp"] = f"{start_ts}–{end_ts}"
    return True


def coalesce_contiguous_turns(
    turns: List[Dict[str, Any]], max_pause_sec: float = 6.0, max_words: int = 45
) -> List[Dict[str, Any]]:
    """Coalesces adjacent rolling 5s ASR chunks from the same speaker into coherent multi-sentence passages."""
    merged: List[Dict[str, Any]] = []
    for raw_t in turns or []:
        if not isinstance(raw_t, dict):
            continue
        t = dict(raw_t)
        txt = str(t.get("text") or "").strip()
        if not txt:
            continue
        if merged and _try_merge_into_prev(merged[-1], t, txt, max_pause_sec, max_words):
            continue
        merged.append(t)
    return merged


def _compute_hint_signature(dirs: List[Optional[Path]]) -> Tuple[Any, ...]:
    sig: List[Any] = []
    for d in dirs:
        if not d:
            continue
        for fname in ("speaker_hints_queue.json", "speaker_hints.json", "voice_registry.json"):
            for p in (d / fname, d / ".cache" / fname):
                if p.is_file():
                    st = p.stat()
                    sig.append((str(p), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _parse_segment_dict(d: Dict[str, Any], default_sec: float) -> Optional[Dict[str, Any]]:
    from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn

    txt = str(d.get("normalized") or d.get("text") or d.get("verbatim") or "")
    if not txt or is_hallucinated_or_prompt_echo_turn(txt, d.get("no_speech_prob")):
        return None
    spk = str(d.get("speaker") or "Unknown")
    ts = str(d.get("timestamp") or "00:00:00")
    sec = float(d.get("start_sec") if d.get("start_sec") is not None else d.get("seconds", default_sec))
    item: Dict[str, Any] = {"speaker": spk, "text": txt, "timestamp": ts, "turn_sec": sec}
    for extra_k in ("seq", "chunk_idx", "end_sec", "asr_conf", "speaker_conf", "no_speech_prob", "voice_id", "hint_applied", "hint_quote", "source"):
        if d.get(extra_k) is not None:
            item[extra_k] = d[extra_k]
    return item


def _append_complete_jsonl_lines(complete_bytes: bytes, turns: List[Dict[str, Any]]) -> None:
    for line in complete_bytes.decode("utf-8", errors="replace").splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        try:
            data = json.loads(line_str)
        except Exception:
            continue
        item = _parse_segment_dict(data, default_sec=len(turns) * 3.0)
        if item:
            turns.append(item)


def _parse_jsonl_turns_incremental(path: Path, cache_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        st = path.stat()
        inode, size = st.st_ino, st.st_size
    except Exception:
        return []
    last_offset: int = cache_entry.get("last_offset", 0)
    turns: List[Dict[str, Any]] = list(cache_entry.get("raw_turns", []))
    if cache_entry.get("inode") != inode or size < last_offset:
        last_offset, turns = 0, []
    if size > last_offset:
        try:
            with open(path, "rb") as f:
                f.seek(last_offset)
                chunk = f.read(size - last_offset)
            last_nl = chunk.rfind(b"\n")
            if last_nl != -1:
                tail = chunk[last_nl + 1 :].strip()
                complete_bytes = chunk if tail.endswith(b"}") else chunk[: last_nl + 1]
                last_offset += len(complete_bytes)
                _append_complete_jsonl_lines(complete_bytes, turns)
            elif chunk.strip().endswith(b"}"):
                last_offset += len(chunk)
                _append_complete_jsonl_lines(chunk, turns)
        except Exception:
            return turns
    cache_entry["inode"] = inode
    cache_entry["last_offset"] = last_offset
    cache_entry["raw_turns"] = turns
    return turns


def _parse_jsonl_turns(path: Path) -> List[Dict[str, Any]]:
    return _parse_jsonl_turns_incremental(path, {})


def _parse_time_str_to_seconds(ts_str: str, default_sec: float) -> float:
    parts = ts_str.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return default_sec


def _parse_markdown_turns(text: str) -> List[Dict[str, Any]]:
    import re
    from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn

    turns: List[Dict[str, Any]] = []
    patterns = (
        re.compile(r"^(?:-\s+)?\*\*\[?([0-9:.]+)\]?\*\*\s+\*\*([^:*]+?):?\*\*:?\s+(.*)$"),
        re.compile(r"^(?:-\s+)?\*\*\[?([0-9:.]+)\]?\*\*\s+([^:]+):\s+(.*)$"),
        re.compile(r"^(?:-\s+)?\[([0-9:.]+)\]\s+\*\*([^:*]+?):?\*\*:?\s+(.*)$"),
        re.compile(r"^(?:-\s+)?\[([0-9:.]+)\]\s+([^:]+):\s+(.*)$"),
    )
    for line in text.splitlines():
        stripped = line.strip()
        m = next((pat.match(stripped) for pat in patterns if stripped and pat.match(stripped)), None)
        if not m:
            continue
        ts_str, spk, txt = m.groups()
        if txt.strip() and not is_hallucinated_or_prompt_echo_turn(txt.strip()):
            sec = _parse_time_str_to_seconds(ts_str, len(turns) * 3.0)
            turns.append({"speaker": spk.strip(), "text": txt.strip(), "timestamp": ts_str, "turn_sec": sec})
    return turns


def _manifest_segment_paths(manifest: SessionManifest) -> List[Path]:
    cands: List[Path] = []
    if getattr(manifest, "segments_jsonl", None):
        p = Path(manifest.segments_jsonl)
        cands.append(p if p.is_absolute() else (Path(manifest.repo_root) / p))
    if getattr(manifest, "cache_dir", None):
        cd = Path(manifest.cache_dir)
        cache_path = cd if cd.is_absolute() else (Path(manifest.repo_root) / cd)
        if cache_path.is_dir():
            cands.extend(sorted(cache_path.glob("*segments*.jsonl")))
    return cands


def _read_fallback_live_transcript_md(manifest: SessionManifest) -> str:
    out_dir = (Path(manifest.repo_root) / manifest.output_dir).resolve()
    search_dirs = [out_dir, out_dir.parent] if out_dir.name.startswith(".staging_") else [out_dir]
    for d in search_dirs:
        if not d.is_dir():
            continue
        for md_file in sorted(d.glob("*_live_transcript.md")):
            if md_file.is_file():
                return md_file.read_text(encoding="utf-8")
    return ""


def _scan_transcript_cache_dir(tc_dir: Path, slug: str) -> List[Path]:
    if not tc_dir.is_dir():
        return []
    cands = sorted(tc_dir.glob(f"*{slug}*segments*.jsonl")) + sorted(tc_dir.glob("*segments*.jsonl"))
    for stg in sorted(tc_dir.glob(".staging_*")):
        if stg.is_dir():
            cands.extend(sorted(stg.glob("*segments*.jsonl")))
    return cands


def _sibling_transcript_cache_paths(output_dir: Path) -> List[Path]:
    try:
        resolved = output_dir.resolve()
        cands = sorted(resolved.parent.glob("*segments*.jsonl")) if resolved.name.startswith(".staging_") and resolved.parent.is_dir() else []
        parts = resolved.parts
        if "output" in parts:
            idx = parts.index("output")
            if len(parts) >= idx + 4 and parts[idx + 1] != "transcript_cache":
                project, date_str, slug = parts[idx + 1], parts[idx + 2], parts[idx + 3]
                tc_dir = Path(*parts[:idx + 1]) / "transcript_cache" / project / date_str
                cands.extend(_scan_transcript_cache_dir(tc_dir, slug))
        return cands
    except Exception:
        return []


def _find_segments_candidates(output_dir: Path, manifest: Optional[SessionManifest] = None) -> List[Path]:
    cands: List[Path] = _manifest_segment_paths(manifest) if manifest else []
    cands.extend(_sibling_transcript_cache_paths(output_dir))
    cands.extend([
        output_dir / "cache" / "segments.jsonl",
        output_dir / ".cache" / "segments.jsonl",
        output_dir / "segments.jsonl",
        *(sorted(output_dir.glob(".cache/*segments*.jsonl"))),
        *(sorted(output_dir.glob("cache/*segments*.jsonl"))),
        *(sorted(output_dir.glob("*segments*.jsonl"))),
    ])
    seen: set[str] = set()
    unique: List[Path] = []
    for c in cands:
        resolved = str(c.resolve()) if c.exists() else str(c)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(c)
    return unique


def _extract_project_date_slug(output_dir: Path) -> Optional[Tuple[Path, str, str, str]]:
    parts = output_dir.resolve().parts
    if "output" not in parts:
        return None
    idx = parts.index("output")
    base_root = Path(*parts[:idx]) if idx > 0 else Path("/")
    if len(parts) >= idx + 4 and parts[idx + 1] == "transcript_cache":
        slug = parts[idx + 4] if len(parts) > idx + 4 else ""
        return base_root, parts[idx + 2], parts[idx + 3], slug.replace(".staging_", "")
    if len(parts) >= idx + 4:
        return base_root, parts[idx + 1], parts[idx + 2], parts[idx + 3].replace(".staging_", "")
    return None


def _collect_sibling_roots(base_root: Path) -> List[Path]:
    roots = [base_root]
    if base_root.parent.name == ".worktrees":
        main_root = base_root.parent.parent
        roots.extend([main_root, *sorted((main_root / ".worktrees").glob("audio_*"))])
    elif (base_root / ".worktrees").is_dir():
        roots.extend(sorted((base_root / ".worktrees").glob("audio_*")))
    return roots


def _gather_root_sibling_candidates(r: Path, project: str, date_str: str, active_slug: str) -> List[Path]:
    tc_dir = r / "output" / "transcript_cache" / project / date_str
    out_date_dir = r / "output" / project / date_str
    cand_files: List[Path] = sorted(tc_dir.glob("*segments*.jsonl")) if tc_dir.is_dir() else []
    if out_date_dir.is_dir():
        for sub in sorted(out_date_dir.iterdir()):
            if sub.is_dir() and sub.name != active_slug:
                cand_files.extend(sorted(sub.glob(".cache/*segments*.jsonl")) + sorted(sub.glob("*segments*.jsonl")))
    return cand_files


def _is_eligible_sibling_file(
    f: Path, prim_res: Optional[Path], active_slug: str, seen_stems: set[str]
) -> bool:
    if not f.is_file() or (prim_res and f.resolve() == prim_res):
        return False
    if active_slug and active_slug.lower() in f.name.lower():
        return False
    return f.stem not in seen_stems


def _discover_same_day_sibling_files(output_dir: Path, primary_path: Optional[Path]) -> List[Path]:
    parsed = _extract_project_date_slug(output_dir)
    if not parsed:
        return []
    base_root, project, date_str, active_slug = parsed
    prim_res = primary_path.resolve() if (primary_path and primary_path.exists()) else None
    found: List[Path] = []
    seen_stems: set[str] = set()
    for r in _collect_sibling_roots(base_root):
        for f in _gather_root_sibling_candidates(r, project, date_str, active_slug):
            if _is_eligible_sibling_file(f, prim_res, active_slug, seen_stems):
                seen_stems.add(f.stem)
                found.append(f)
    return found


def _prepend_same_day_sibling_turns(
    output_dir: Path, primary_turns: List[Dict[str, Any]], primary_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    sib_files = _discover_same_day_sibling_files(output_dir, primary_path)
    if not sib_files:
        return primary_turns
    sib_turns: List[Dict[str, Any]] = []
    for sf in sib_files:
        for t in _parse_jsonl_turns(sf):
            t_copy = dict(t)
            t_copy["is_sibling_session"] = True
            sib_turns.append(t_copy)
    if not sib_turns:
        return primary_turns
    if primary_turns:
        min_prim = min(float(t.get("turn_sec", 0.0)) for t in primary_turns)
        max_sib = max(float(t.get("turn_sec", 0.0)) for t in sib_turns)
        if max_sib >= min_prim:
            shift = max_sib - min_prim + 3.0
            for t in sib_turns:
                t["turn_sec"] = round(float(t.get("turn_sec", 0.0)) - shift, 3)
    return sib_turns + primary_turns
