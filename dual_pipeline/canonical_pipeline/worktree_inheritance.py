"""
dual_pipeline/canonical_pipeline/worktree_inheritance.py

Encapsulates active git worktree detection and path inheritance during canonical ingestion,
preventing redundant worktree provisioning and ensuring slug invariance across execution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .worktree_seeding import seed_prior_artifacts_into_worktree

logger = logging.getLogger("CanonicalAgenticPipeline.worktree_inheritance")

# Backward compatibility alias
_seed_prior_artifacts_into_worktree = seed_prior_artifacts_into_worktree


def _extract_worktree_from_path(path_val: Any) -> Optional[Path]:
    """Inspects a file or directory path to see if it resides within a .worktrees container."""
    if not path_val:
        return None
    try:
        resolved = Path(str(path_val)).resolve()
        for parent in (resolved, *resolved.parents):
            if parent.parent.name == ".worktrees" and parent.is_dir():
                return parent
    except Exception:
        return None
    return None


def _normalize_worktree_path(worktree_dir: Any, root: Optional[Path] = None) -> Path:
    """Resolves a worktree directory path relative to root when not already absolute/existing."""
    wt_path = Path(str(worktree_dir)).expanduser()
    if wt_path.is_absolute() or wt_path.exists():
        return wt_path.resolve()
    base = (root or Path.cwd()).resolve()
    candidate = base / wt_path
    if candidate.exists():
        return candidate.resolve()
    return wt_path.resolve()


def _resolve_branch_for_worktree(wt_dir: Path, meta_branch: Optional[str]) -> str:
    """Resolves git branch name for worktree, falling back to slugified branch convention."""
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        get_current_git_branch,
    )
    branch = get_current_git_branch(wt_dir) or meta_branch
    if branch:
        return branch
    name = wt_dir.name
    if name.startswith("ingest_doc_"):
        b_slug = name.replace("ingest_doc_", "").replace("_", "-")
        return f"ingest/doc-{b_slug}"
    if name.startswith("ingest_meeting_"):
        b_slug = name.replace("ingest_meeting_", "").replace("_", "-")
        return f"ingest/meeting-{b_slug}"
    if name.startswith("transcript_"):
        b_slug = name.replace("transcript_", "").replace("_", "-")
        return f"transcript/{b_slug}"
    if name.startswith("audio_"):
        b_slug = name.replace("audio_", "", 1).replace("_", "-")
        return f"audio/{b_slug}"
    b_slug = name.replace("feat_live_", "").replace("live_", "").replace("_", "-")
    return f"feat/live-{b_slug}"


def rebind_worktree_metadata_paths(meta: Dict[str, Any], wt_dir: Path, target_p: Optional[Path] = None) -> None:
    """Rebinds any metadata paths that reference a foreign or stale worktree."""
    meta["worktree_dir"] = str(wt_dir)
    if target_p:
        meta["target_output_dir"] = str(target_p)
        meta["output_dir"] = str(target_p)

    for key in (
        "target_output_dir",
        "output_dir",
        "output_01_path",
        "output_02_path",
        "output_03_path",
        "advisory_path",
        "deliverable_dir",
        "staging_dir",
    ):
        val = meta.get(key)
        if not val or not isinstance(val, (str, Path)):
            continue
        try:
            p = Path(val)
            parts = p.parts
            if ".worktrees" in parts:
                idx = parts.index(".worktrees")
                if idx + 1 < len(parts):
                    old_wt_name = parts[idx + 1]
                    if old_wt_name != wt_dir.name:
                        rel_parts = parts[idx + 2:]
                        new_p = wt_dir.joinpath(*rel_parts)
                        meta[key] = str(new_p)
                        logger.info(f"Rebound stale worktree path in metadata[{key}]: {val} -> {new_p}")
        except Exception:
            pass


def reanchor_resumed_output_dir(out_p: Path, meta: Dict[str, Any]) -> Path:
    """Rebinds checkpoint output path and metadata to the active worktree."""
    inherited = inherit_worktree_from_metadata(meta)
    if inherited:
        wt_dir, _ = inherited
        parts = out_p.parts
        if ".worktrees" in parts:
            idx = parts.index(".worktrees")
            if idx + 1 < len(parts) and parts[idx + 1] != wt_dir.name:
                out_p = wt_dir.joinpath(*parts[idx + 2:])
        rebind_worktree_metadata_paths(meta, wt_dir, out_p)
    meta["target_output_dir"] = str(out_p)
    meta["output_dir"] = str(out_p)
    return out_p


def inherit_worktree_from_metadata(
    meta: Dict[str, Any], repo_root: Optional[Path] = None
) -> Optional[Tuple[Path, str]]:
    """
    Detects if input, cache, or current working directory points inside an active worktree.
    Returns (worktree_dir, branch) if an authentic worktree is found.
    Explicit input paths inside a worktree take highest precedence, followed by active CWD worktree
    (unless preserve_metadata_worktree=True), then metadata worktree_dir/worktree_branch.
    """
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import _resolve_primary_repo_root
    primary_root = _resolve_primary_repo_root(repo_root or Path.cwd())

    # 1. Explicit candidate keys from input/source paths inside an existing worktree
    candidate_keys = ("input_path", "audio_path", "source_path", "input", "transcript_cache_path")
    for key in candidate_keys:
        wt_p = _extract_worktree_from_path(meta.get(key))
        if wt_p and wt_p.is_dir():
            return wt_p, _resolve_branch_for_worktree(wt_p, meta.get("worktree_branch"))

    # 2. Explicit metadata worktree_dir or worktree_branch (takes precedence over unrelated CWD worktree)
    if meta.get("worktree_dir") or meta.get("worktree_branch"):
        from dual_pipeline.live_meeting_recorder_core.worktree_manager import _provision_git_worktree
        from tools.pr_governance_core.common import slugify_branch

        raw_wt_str = meta.get("worktree_dir")
        if raw_wt_str:
            raw_p = Path(str(raw_wt_str))
            wt_p = (primary_root / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()
        else:
            b_slug = slugify_branch(str(meta["worktree_branch"]))
            wt_p = (primary_root / ".worktrees" / b_slug).resolve()

        if not wt_p.is_dir() and wt_p.name:
            cand = (primary_root / ".worktrees" / wt_p.name).resolve()
            if cand.is_dir():
                wt_p = cand
            elif not wt_p.name.startswith("feat_"):
                prefixed = (primary_root / ".worktrees" / f"feat_{wt_p.name}").resolve()
                if prefixed.is_dir():
                    wt_p = prefixed

        resolved_branch = _resolve_branch_for_worktree(wt_p, meta.get("worktree_branch"))
        if not wt_p.is_dir() and wt_p.parent.name == ".worktrees":
            (primary_root / ".worktrees").mkdir(parents=True, exist_ok=True)
            _provision_git_worktree(primary_root, wt_p, resolved_branch)

        if wt_p.is_dir():
            return wt_p, _resolve_branch_for_worktree(wt_p, resolved_branch)
        return None

    # 3. Active CWD worktree precedence when no explicit worktree_dir/worktree_branch was supplied
    cwd_wt = _extract_worktree_from_path(Path.cwd())
    if cwd_wt and not meta.get("preserve_metadata_worktree"):
        try:
            cwd_wt.resolve().relative_to(primary_root.resolve())
            if cwd_wt.is_dir():
                return cwd_wt, _resolve_branch_for_worktree(cwd_wt, meta.get("worktree_branch"))
        except (ValueError, Exception):
            pass

    # 4. Fallback CWD worktree
    if cwd_wt and cwd_wt.is_dir():
        try:
            cwd_wt.resolve().relative_to(primary_root.resolve())
            return cwd_wt, _resolve_branch_for_worktree(cwd_wt, meta.get("worktree_branch"))
        except (ValueError, Exception):
            pass

    return None


def _find_matching_effective_slug(date_dir: Path, slug: str) -> str:
    """Matches existing subdirectories under date directory to prevent slug drift."""
    if not date_dir.is_dir():
        return slug
    subdirs = [d for d in date_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    for d in subdirs:
        if d.name.lower() in slug.lower() or slug.lower() in d.name.lower() or any(d.glob("*_live_transcript.md")):
            return d.name
    return slug


def resolve_inherited_target_path(
    meta: Dict[str, Any],
    proj: str,
    date_fn: str,
    slug: str,
    no_subfolder: bool = False,
    repo_root: Optional[Path] = None,
) -> Optional[Path]:
    """
    Resolves target path within an inherited worktree without provisioning new branches.
    Mutates meta with inherited worktree_dir, worktree_branch, and filename_slug.
    """
    inherited = inherit_worktree_from_metadata(meta, repo_root)
    if not inherited:
        return None

    wt_dir, wt_branch = inherited
    meta["worktree_dir"] = str(wt_dir)
    meta["worktree_branch"] = wt_branch

    # Resolve effective project directory in inherited worktree (check existing casing/alias folders)
    eff_proj = proj
    out_root = wt_dir / "output"
    if out_root.is_dir():
        for p_cand in (proj, proj.lower(), proj.upper()):
            if (out_root / p_cand / date_fn).is_dir():
                eff_proj = p_cand
                break
        else:
            for child in out_root.iterdir():
                if child.is_dir() and (child.name.lower() in proj.lower() or proj.lower() in child.name.lower()):
                    if (child / date_fn).is_dir():
                        eff_proj = child.name
                        break

    date_dir = out_root / eff_proj / date_fn
    effective_slug = meta.get("filename_slug") or _find_matching_effective_slug(date_dir, slug)
    meta["filename_slug"] = effective_slug

    sub_path = Path(eff_proj) / date_fn if no_subfolder else Path(eff_proj) / date_fn / effective_slug
    target_p = wt_dir / "output" / sub_path
    if repo_root is not None:
        from dual_pipeline.live_meeting_recorder_core.worktree_manager import _resolve_primary_repo_root
        prim_root = _resolve_primary_repo_root(repo_root)
        seed_prior_artifacts_into_worktree(prim_root / "output" / sub_path, target_p, meta=meta)
    rebind_worktree_metadata_paths(meta, wt_dir, target_p)
    logger.info(f"Canonical Lifecycle: Inherited existing worktree -> {target_p} (branch: {wt_branch})")
    return target_p


def hydrate_inherited_metadata_slug(meta: Dict[str, Any], repo_root: Optional[Path] = None) -> None:
    """Pre-populates filename_slug from input filename if inside inherited worktree."""
    inherited = inherit_worktree_from_metadata(meta, repo_root)
    if not inherited:
        return
    wt_dir, wt_branch = inherited
    meta["worktree_dir"] = str(wt_dir)
    meta["worktree_branch"] = wt_branch
    if "filename_slug" in meta:
        return

    from dual_pipeline.meeting_paths import extract_slug_from_meeting_filename
    for cand_key in ("input_path", "audio_path", "source_path", "input"):
        cand_p = meta.get(cand_key)
        if cand_p:
            extracted = extract_slug_from_meeting_filename(str(cand_p))
            if extracted:
                meta["filename_slug"] = extracted
                break


def _handle_existing_worktree_containment(
    base_out: Path, meta_dict: Dict[str, Any], repo_root: Path
) -> Path:
    from .worktree_seeding import handle_existing_worktree_containment
    return handle_existing_worktree_containment(base_out, meta_dict, repo_root)


def _provision_new_worktree_target(
    base_out: Path,
    meta_dict: Dict[str, Any],
    proj: str,
    date_fn: str,
    slug: str,
    no_subfolder: bool,
    in_repo: bool,
    repo_root: Path,
    modality: Optional[Any] = None,
) -> Path:
    """Provisions a new isolated worktree and returns target path."""
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        ensure_ingest_worktree,
    )
    effective_modality = modality or meta_dict.get("modality") or meta_dict.get("canonical_modality")
    raw_title = meta_dict.get("meeting_name") or meta_dict.get("title") or slug
    wt_dir, wt_branch = ensure_ingest_worktree(
        modality=effective_modality,
        project_name=proj,
        title=raw_title,
        date_dirname=date_fn,
        clean_slug=slug,
        repo_root=repo_root,
    )
    sub_path = (
        base_out.relative_to((repo_root / "output").resolve())
        if in_repo
        else (Path(proj) / date_fn if no_subfolder else Path(proj) / date_fn / slug)
    )
    meta_dict["worktree_dir"] = str(wt_dir)
    meta_dict["worktree_branch"] = wt_branch
    target_p = wt_dir / "output" / sub_path
    from .worktree_seeding import seed_prior_artifacts_into_worktree
    seed_prior_artifacts_into_worktree(base_out, target_p, meta=meta_dict)
    rebind_worktree_metadata_paths(meta_dict, wt_dir, target_p)
    return target_p


# Backward compatibility alias
_provision_new_live_worktree_target = _provision_new_worktree_target


def resolve_target_worktree_path(
    base_out: Path,
    meta_dict: Dict[str, Any],
    proj: str,
    date_fn: str,
    slug: str,
    no_subfolder: bool,
    output_dir: str,
    repo_root: Path,
    modality: Optional[Any] = None,
) -> Path:
    """Dispatches worktree path resolution, isolation, and inheritance."""
    in_repo = False
    try:
        in_repo = base_out.is_relative_to((repo_root / "output").resolve())
    except Exception:
        pass

    if not (in_repo or output_dir == "auto"):
        return base_out

    # Check if existing worktree can be inherited from metadata or candidate paths first
    inherited_target = resolve_inherited_target_path(
        meta=meta_dict,
        proj=proj,
        date_fn=date_fn,
        slug=slug,
        no_subfolder=no_subfolder,
        repo_root=repo_root,
    )
    if inherited_target is not None:
        return inherited_target

    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        is_inside_isolated_worktree,
    )
    if is_inside_isolated_worktree(base_out):
        return _handle_existing_worktree_containment(base_out, meta_dict, repo_root)

    return _provision_new_worktree_target(
        base_out=base_out,
        meta_dict=meta_dict,
        proj=proj,
        date_fn=date_fn,
        slug=slug,
        no_subfolder=no_subfolder,
        in_repo=in_repo,
        repo_root=repo_root,
        modality=modality,
    )
