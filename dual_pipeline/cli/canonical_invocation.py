"""Canonical CLI invocation builder for governed meeting ingestion under AGENTS.md."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Sequence, Union


def format_directive_slug(title: str) -> str:
    """Replicates bash: $(echo "${TITLE}" | tr '[:upper:] ' '[:lower:]_' | tr -cd '[:alnum:]_')."""
    lowered = title.lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", lowered)


def format_date_slug(date_str: str) -> str:
    """Replicates bash: $(echo "${DATE}" | tr '/' '-')."""
    return date_str.replace("/", "-")


def _format_attendees_arg(attendees: Optional[Union[str, Sequence[Any]]]) -> Optional[str]:
    if not attendees:
        return None
    if isinstance(attendees, (list, tuple)):
        att_names = []
        for a in attendees:
            if isinstance(a, dict):
                name = a.get("name")
                if name:
                    att_names.append(str(name))
            elif a:
                att_names.append(str(a))
        return ", ".join(att_names) or None
    att_str = str(attendees).strip()
    return att_str or None


def _dedup_worktree_rel_path(wt_p: Path) -> Path:
    parts = list(wt_p.parts)
    wt_indices = [i for i, part in enumerate(parts) if part == ".worktrees"]
    if len(wt_indices) >= 2:
        first_idx = wt_indices[0]
        second_idx = wt_indices[1]
        if first_idx + 2 == second_idx and second_idx + 1 < len(parts):
            parts = parts[:first_idx] + parts[second_idx:]
            return Path(*parts)
    return wt_p


def build_canonical_ingest_command(
    input_path: Union[str, Path],
    project: str,
    meeting: str,
    date: str,
    attendees: Optional[Union[str, Sequence[Any]]] = None,
    reviewer: Optional[str] = None,
    provider: str = "llm",
    max_judge_retries: int = 2,
    embedding_backend: str = "bge-m3-dev",
    filename_slug: Optional[str] = None,
    worktree_dir: Optional[Union[str, Path]] = None,
    time: Optional[str] = None,
    attachment_dir: Optional[Union[str, Path]] = None,
) -> str:
    """
    Constructs the canonical 'nee ingest-meeting' invocation matching AGENTS.md rule.
    """
    del reviewer
    from dual_pipeline.cli.ingest import _generate_default_directive_id
    directive_id = _generate_default_directive_id({
        "project": project,
        "meeting_name": meeting,
        "date": date,
    })

    cmd_parts = [
        "nee ingest-meeting",
        f'--input "{Path(input_path)}"',
        f'--project "{project}"',
        f'--meeting "{meeting}"',
        f'--date "{date}"',
    ]
    if time and str(time).strip():
        cmd_parts.append(f'--time "{str(time).strip()}"')

    att_str = _format_attendees_arg(attendees)
    if att_str:
        cmd_parts.append(f'--attendees "{att_str}"')

    if filename_slug:
        cmd_parts.append(f'--filename-slug "{filename_slug}"')
    if worktree_dir:
        wt_p = Path(worktree_dir)
        try:
            from dual_pipeline.core import ROOT
            if wt_p.is_absolute() and wt_p.is_relative_to(ROOT):
                wt_p = wt_p.relative_to(ROOT)
        except Exception:
            pass
        wt_p = _dedup_worktree_rel_path(wt_p)
        cmd_parts.append(f'--worktree-dir "{wt_p}"')

    if attachment_dir:
        att_p = Path(attachment_dir)
        try:
            from dual_pipeline.core import ROOT
            if att_p.is_absolute() and att_p.is_relative_to(ROOT):
                att_p = att_p.relative_to(ROOT)
        except Exception:
            pass
        cmd_parts.append(f'--attachment-dir "{att_p}"')
    elif input_path:
        try:
            from dual_pipeline.naming import resolve_meeting_container_dir
            container_p = resolve_meeting_container_dir(input_path)
            if container_p:
                ref_dir = container_p / "referenced_artifacts"
                if ref_dir.is_dir() and any(f.is_file() and not f.name.startswith(".") for f in ref_dir.iterdir()):
                    try:
                        from dual_pipeline.core import ROOT
                        if ref_dir.is_absolute() and ref_dir.is_relative_to(ROOT):
                            ref_dir = ref_dir.relative_to(ROOT)
                    except Exception:
                        pass
                    cmd_parts.append(f'--attachment-dir "{ref_dir}"')
        except Exception:
            pass

    cmd_parts.extend([
        f"--provider {provider}",
        f"--max-judge-retries {max_judge_retries}",
        f'--directive-id "{directive_id}"',
        f"--embedding-backend {embedding_backend}",
        "--allow-dev-bge-m3",
        "--enforce-worktree",
    ])

    return " ".join(cmd_parts)

