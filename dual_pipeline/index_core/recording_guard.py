"""Active live meeting recording detection for GPU isolation."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dual_pipeline.core import ROOT


def is_live_recording_active(repo_root: Path | None = None) -> bool:
    """Checks whether an active live meeting recording session is running in repo or parent worktree."""
    root = Path(repo_root) if repo_root else ROOT
    dirs = [root / ".session_registry"]
    if root.parent.name == ".worktrees":
        dirs.append(root.parent.parent / ".session_registry")
    now = time.time()
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            for f in d.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    last_hb = float(data.get("last_heartbeat", 0.0))
                    if now - last_hb < 15.0:
                        ppid = int(data.get("parent_pid", 0))
                        if ppid > 0:
                            try:
                                os.kill(ppid, 0)
                                return True
                            except OSError:
                                pass
                except Exception:
                    continue
        except Exception:
            continue
    return False
