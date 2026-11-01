"""
dual_pipeline/canonical_pipeline/worktree_naming.py

Pure-function domain naming engine for Git worktrees and branches.
Provides modality-aware derivation conforming to Conventional Branches (INV-WT-04),
deterministic slug mapping (INV-WT-03), and model-neutral artifact hygiene (AGENTS.md Invariant 7).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Tuple, Union

# Prohibited LLM model naming tokens per AGENTS.md Invariant 7
PROHIBITED_MODEL_TOKENS = frozenset({
    "gpt", "astra", "luna", "sol", "terra", "claude",
    "gemini", "llama", "sonnet", "haiku", "opus", "o1", "o3",  # noqa: SSOT_EXEMPT
})

CONVENTIONAL_BRANCH_RE = re.compile(
    r"^(feat|feature|enhance|fix|ingest|audio|transcript|docs|style|refactor|perf|test|build|ci|chore|revert)/[a-zA-Z0-9_\-\.]+$"
)


MODEL_SANITIZE_RE = re.compile(
    r"\b(gpt[-_]?[0-9a-z]*|claude[-_]?[0-9a-z]*|gemini[-_]?[0-9a-z]*|llama[-_]?[0-9a-z]*|sonnet|haiku|opus|sol|terra|astra|luna|o1|o3)\b",
    re.IGNORECASE,
)


def slugify_branch_name(text: str, max_len: int = 40) -> str:
    """
    Converts arbitrary title or identifier into a clean conventional branch slug.
    - Strips whitespace
    - Lowercases
    - Eliminates prohibited model tokens and model versions
    - Converts underscores and non-alphanumeric characters to single hyphens
    - Enforces max_len budget without trailing hyphens
    """
    if not text:
        return "session"

    lower = text.strip().lower()
    # Strip prohibited model tokens before splitting into hyphens
    sanitized = MODEL_SANITIZE_RE.sub("", lower)

    # Replace underscores and non-alphanumerics with single hyphens
    clean = re.sub(r"[^a-z0-9]+", "-", sanitized)
    clean = re.sub(r"-+", "-", clean).strip("-")

    # Double check standalone prohibited model tokens
    parts = [p for p in clean.split("-") if p and p not in PROHIBITED_MODEL_TOKENS]
    clean = "-".join(parts)

    if not clean:
        clean = "session"

    if len(clean) > max_len:
        clean = clean[:max_len].rstrip("-")

    return clean or "session"


def derive_worktree_identity(
    modality: Any,
    title: str,
    project: Optional[str] = None,
    date: Optional[str] = None,
    repo_root: Optional[Path] = None,
    clean_slug: Optional[str] = None,
) -> Tuple[Path, str]:
    """
    Derives deterministic (worktree_dir, branch_name) based on modality, title, project, date, and clean_slug.

    Modalities:
    - DOCUMENT -> branch 'ingest/doc-<slug>', directory '.worktrees/ingest_doc_<slug>'
    - AUDIO / LIVE -> branch 'audio/<slug>', directory '.worktrees/audio_<slug>'
    - MEETING_TRANSCRIPT / TRANSCRIPT / RAW_TRANSCRIPT / MEETING_NOTES / MEETING -> branch 'transcript/<slug>', directory '.worktrees/transcript_<slug>'

    Returns:
        (worktree_dir: Path, branch_name: str)
    """
    root = (repo_root or Path.cwd()).resolve()

    # Normalize modality string
    mod_str = ""
    if modality is not None:
        if hasattr(modality, "value"):
            mod_str = str(modality.value).upper()
        else:
            mod_str = str(modality).upper()

    # Determine base slug token: prefer clean_slug if provided, else title
    base_text = clean_slug or title
    base_token = slugify_branch_name(base_text, max_len=60)

    # Intelligent project resolution: override generic container if title specifies a canonical workstream
    effective_project = project
    from dual_pipeline.project_aliases import infer_project_from_title
    inferred_proj = infer_project_from_title(base_text)
    if inferred_proj and (
        not effective_project
        or effective_project in ("auto", "nea", "project_nea", "project_nexus", "internal_consulting")
        or (
            effective_project != inferred_proj
            and inferred_proj in (
                "dociq", "project_jarvis", "project_lens", "conduit", "regai", "ec_scheduling", "project_beacon"
            )
        )
    ):
        effective_project = inferred_proj

    # Optional project and date components for scoping
    components = []
    if effective_project:
        proj_clean = slugify_branch_name(effective_project, max_len=25)
        short_proj = proj_clean[8:] if proj_clean.startswith("project-") else proj_clean
        if proj_clean and proj_clean != "session":
            if base_token.startswith(f"{proj_clean}-"):
                base_token = base_token[len(proj_clean) + 1:]
                components.append(proj_clean)
            elif base_token.startswith(f"{short_proj}-"):
                base_token = base_token[len(short_proj) + 1:]
                components.append(proj_clean)
            elif proj_clean not in base_token and short_proj not in base_token:
                components.append(proj_clean)

    if date:
        date_clean = slugify_branch_name(date, max_len=15)
        if date_clean and date_clean != "session" and date_clean not in base_token:
            components.append(date_clean)

    if base_token and base_token != "session":
        components.append(base_token)
    elif not components:
        components.append("session")

    full_slug = "-".join(components)
    derived_slug = slugify_branch_name(full_slug, max_len=80)
    slug_underscores = derived_slug.replace("-", "_")

    if mod_str in ("DOCUMENT", "DOC"):
        branch_name = f"ingest/doc-{derived_slug}"
        worktree_dir = root / ".worktrees" / f"ingest_doc_{slug_underscores}"
    elif mod_str in ("AUDIO", "RECORDING", "LIVE"):
        branch_name = f"audio/{derived_slug}"
        worktree_dir = root / ".worktrees" / f"audio_{slug_underscores}"
    elif mod_str in ("MEETING_TRANSCRIPT", "TRANSCRIPT", "RAW_TRANSCRIPT", "MEETING_NOTES", "MEETING"):
        branch_name = f"transcript/{derived_slug}"
        worktree_dir = root / ".worktrees" / f"transcript_{slug_underscores}"
    else:
        # Default fallback heuristic based on title contents
        lower_title = title.lower()
        if any(k in lower_title for k in ("live", "standup", "sync", "1-on-1", "1:1", "meeting", "call")):
            branch_name = f"audio/{derived_slug}"
            worktree_dir = root / ".worktrees" / f"audio_{slug_underscores}"
        else:
            branch_name = f"ingest/doc-{derived_slug}"
            worktree_dir = root / ".worktrees" / f"ingest_doc_{slug_underscores}"

    return worktree_dir, branch_name
