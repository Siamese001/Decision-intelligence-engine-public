"""
tools/audit_worktree_hygiene_core

Core package for git worktree and filesystem hygiene auditing.
"""
from __future__ import annotations

from .pruner import (
    _git_env,
    cleanup_abandoned_zero_commit_worktrees,
    cleanup_orphaned_worktree_directories,
    preserve_worktree_outputs,
    purge_quarantine,
)

__all__ = [
    "_git_env",
    "cleanup_abandoned_zero_commit_worktrees",
    "cleanup_orphaned_worktree_directories",
    "preserve_worktree_outputs",
    "purge_quarantine",
]
