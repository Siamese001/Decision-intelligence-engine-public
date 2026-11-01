"""
dual_pipeline/canonical_pipeline/u0_manifest_discovery.py

Directory resolution, session manifest discovery, and container attachment hydration.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _prepend_worktree_mirror_dirs(dirs: List[Path], wt_raw: Any) -> None:
    if not wt_raw:
        return
    with contextlib.suppress(Exception):
        wt_p = Path(str(wt_raw)).expanduser().resolve()
        for base_d in list(dirs):
            if "output" not in base_d.parts:
                continue
            idx = base_d.parts.index("output")
            wt_cand = wt_p.joinpath(*base_d.parts[idx:])
            if wt_cand.is_dir() and wt_cand not in dirs:
                dirs.insert(0, wt_cand)
                wt_cache = wt_cand / ".cache"
                if wt_cache.is_dir() and wt_cache not in dirs:
                    dirs.insert(1, wt_cache)


def _resolve_live_artifact_dirs(
    metadata: Dict[str, Any], target_path: Any, container_dir: Optional[Path]
) -> List[Path]:
    dirs: List[Path] = []
    if container_dir and container_dir.is_dir():
        dirs.append(container_dir)
        cache_dir = container_dir / ".cache"
        if cache_dir.is_dir() and cache_dir not in dirs:
            dirs.append(cache_dir)
        if len(container_dir.parents) >= 3 and container_dir.parents[2].name == "output":
            tc_dir = (
                container_dir.parents[2]
                / "transcript_cache"
                / container_dir.parents[1].name
                / container_dir.parent.name
            )
            if tc_dir.is_dir() and tc_dir not in dirs:
                dirs.append(tc_dir)
                metadata.setdefault("transcript_cache_dir", str(tc_dir))
    if target_path:
        with contextlib.suppress(Exception):
            p = Path(str(target_path)).expanduser().resolve()
            p_dir = p.parent if p.is_file() else (p if p.is_dir() else None)
            if p_dir and p_dir.is_dir() and p_dir not in dirs:
                dirs.append(p_dir)
            if p_dir:
                p_cache = p_dir / ".cache"
                if p_cache.is_dir() and p_cache not in dirs:
                    dirs.append(p_cache)
            if p_dir and "transcript_cache" in p_dir.parts:
                metadata.setdefault("transcript_cache_dir", str(p_dir))
    _prepend_worktree_mirror_dirs(dirs, metadata.get("worktree_dir"))
    return dirs


def _needs_manifest_attendee_enrichment(current_att: Any, manifest_att: Any) -> bool:
    if not isinstance(manifest_att, list) or not manifest_att:
        return False
    has_rich_manifest = any(isinstance(a, dict) and (a.get("org") or a.get("role")) for a in manifest_att)
    if not current_att:
        return True
    if not has_rich_manifest:
        return False
    if isinstance(current_att, str):
        return True
    if isinstance(current_att, list):
        return not any(isinstance(a, dict) and (a.get("org") or a.get("role")) for a in current_att)
    return False


def _find_manifest_in_dirs(search_dirs: List[Path], slug: str) -> Dict[str, Any]:
    merged_data: Dict[str, Any] = {}
    for s_dir in search_dirs:
        candidates = sorted(s_dir.glob("*manifest.json"))
        for extra in ("metadata.json", "intake_package.json"):
            if (s_dir / extra).is_file() and (s_dir / extra) not in candidates:
                candidates.append(s_dir / extra)
        if slug:
            matching = [c for c in candidates if slug.lower() in c.name.lower()]
            if matching:
                candidates = matching + [c for c in candidates if c not in matching]
        for mf in candidates:
            with contextlib.suppress(Exception):
                data = json.loads(mf.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    if mf.name == "intake_package.json" and "meeting" in data:
                        m_info = data.get("meeting") or {}
                        extracted = {
                            "title": m_info.get("title"),
                            "attendees": m_info.get("attendees"),
                            "project": m_info.get("project"),
                            "classification": m_info.get("classification"),
                        }
                        for k, v in extracted.items():
                            if v and k not in merged_data:
                                merged_data[k] = v
                    else:
                        for k, v in data.items():
                            if v and k not in merged_data:
                                merged_data[k] = v
    return merged_data


def _hydrate_container_attachments(metadata: Dict[str, Any], container_dir: Optional[Path]) -> List[str]:
    if not container_dir:
        return []
    hydrated: List[str] = []
    if not metadata.get("attachment_dir") and not metadata.get("attachments"):
        for cand_name in ("referenced_artifacts", "attachments"):
            cand_p = container_dir / cand_name
            if cand_p.is_dir() and any(f.is_file() and not f.name.startswith(".") for f in cand_p.iterdir()):
                metadata["attachment_dir"] = str(cand_p)
                hydrated.append("attachment_dir")
                break
    drop_log_p = container_dir / "collateral_drop_log.json"
    if drop_log_p.is_file():
        metadata.setdefault("collateral_drop_log_path", str(drop_log_p))
        if "collateral_drop_log" not in hydrated:
            hydrated.append("collateral_drop_log")
    return hydrated


__all__ = [
    "_resolve_live_artifact_dirs",
    "_needs_manifest_attendee_enrichment",
    "_find_manifest_in_dirs",
    "_hydrate_container_attachments",
]
