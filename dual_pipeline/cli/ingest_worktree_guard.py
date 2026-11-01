"""Enforces Code-Freeze Gate on Ingestion Worktrees (ingest/*)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("dual_pipeline.cli.ingest_worktree_guard")

_GOVERNED_FREEZE_DIRS = (
    "dual_pipeline/",
    "unified_pipeline/",
    "pipeline_a_intake/",
    "pipeline_b_extraction/",
    "storage/",
    "policy/",
    "contracts/",
    "tools/",
    "scripts/",
    "config/",
    ".agents/",
    "evals/",
    "tests/",
    "governance/",
    "automations/",
    "schemas/",
    "graph/",
    ".githooks/",
    "nee.py",
)

_FROZEN_BRANCH_PREFIXES = ("ingest/", "audio/", "transcript/", "chore/ingest", "doc/")


class IngestWorktreeCodeFreezeError(RuntimeError):
    """Raised when an ingestion worktree has dirty or committed code/tooling modifications."""


def _has_output_modifications(rt_root: Path, run_git_fn: Any) -> bool:
    try:
        out_status = run_git_fn(["status", "--porcelain", "--", "output/"], cwd=rt_root, check=False)
        out_diff = run_git_fn(["diff", "--name-only", "main...HEAD", "--", "output/"], cwd=rt_root, check=False)
        return bool((out_status and out_status.strip()) or (out_diff and out_diff.strip()))
    except Exception:
        return False


def assert_ingest_worktree_code_freeze(
    worktree_dir: Optional[Union[Path, str]] = None,
    *,
    strict_all_branches: bool = False,
) -> None:
    """
    Enforces Code-Freeze Gate on Ingestion Worktrees (ingest/*, audio/*, transcript/*)
    and blocks mixed output/ + engine code mutations on any branch.
    """
    rt_root = Path(worktree_dir).expanduser().resolve() if worktree_dir else Path.cwd().resolve()
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import get_current_git_branch, run_git

    branch = get_current_git_branch(rt_root)
    if not branch:
        return

    is_frozen_branch = strict_all_branches or branch.startswith(_FROZEN_BRANCH_PREFIXES)
    if not is_frozen_branch and not _has_output_modifications(rt_root, run_git):
        return

    try:
        status_out = run_git(["status", "--porcelain", "--", *_GOVERNED_FREEZE_DIRS], cwd=rt_root, check=True)
        dirty_lines = [line.strip() for line in status_out.splitlines() if line.strip()]
        if dirty_lines:
            logger.error("Ingest worktree code freeze violation: %d dirty engine files on %s:\n%s", len(dirty_lines), branch, "\n".join(dirty_lines[:10]))
            dp_tag = "dual_pipeline/ " if any("dual_pipeline/" in ln for ln in dirty_lines) else ""
            raise IngestWorktreeCodeFreezeError(
                f"FAIL_CLOSED: Ingestion worktree '{branch}' has dirty {dp_tag}source code modifications. "
                "Ingest worktrees are strictly code-frozen. Modifying engine code during ingestion is prohibited."
            )

        diff_out = run_git(["diff", "--name-only", "main...HEAD", "--", *_GOVERNED_FREEZE_DIRS], cwd=rt_root, check=True)
        committed_lines = [line.strip() for line in diff_out.splitlines() if line.strip()]
        if committed_lines:
            logger.error("Ingest worktree code freeze violation: %d committed engine files on %s:\n%s", len(committed_lines), branch, "\n".join(committed_lines[:10]))
            raise IngestWorktreeCodeFreezeError(
                f"FAIL_CLOSED: Ingestion worktree '{branch}' has committed source code modifications relative to main. "
                "Ingest worktrees are strictly code-frozen. Modifying engine code during ingestion is prohibited."
            )
    except IngestWorktreeCodeFreezeError:
        raise
    except RuntimeError as exc:
        raise IngestWorktreeCodeFreezeError(str(exc)) from exc
    except Exception as exc:
        raise IngestWorktreeCodeFreezeError(
            f"FAIL_CLOSED: Failed to verify code-freeze state on ingestion worktree '{branch}' at {rt_root}: {exc}"
        ) from exc

