"""Proposal queue monitoring and backpressure gating for Graph Scout."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

from .core import ROOT, read_json

logger = logging.getLogger(__name__)

DEFAULT_PROPOSAL_DIRS = [
    ROOT / "output" / "graph_scout" / "proposals",
    ROOT / ".local" / "dual_pipeline" / "proposals",
]


class ProposalQueue:
    """Monitors learning-change proposals to enforce review backpressure gates."""

    def __init__(self, proposal_dirs: Optional[Sequence[Path]] = None):
        self.proposal_dirs: List[Path] = (
            [Path(p).resolve() for p in proposal_dirs]
            if proposal_dirs is not None
            else [p.resolve() for p in DEFAULT_PROPOSAL_DIRS]
        )

    def get_open_proposals(self) -> List[Path]:
        """Collect all currently unreviewed / proposed proposal artifacts."""
        open_files: List[Path] = []
        for pdir in self.proposal_dirs:
            if not pdir.is_dir():
                continue
            for item in pdir.glob("*.json"):
                try:
                    data = read_json(item)
                    status = str(data.get("status", "")).upper()
                    # An item is open if status is PROPOSED or not yet terminal
                    if status in ("PROPOSED", "PENDING", "OPEN") or (
                        data.get("approval_required") is True and not data.get("applied")
                    ):
                        open_files.append(item)
                except Exception:
                    continue
        return open_files

    def get_open_proposals_count(self) -> int:
        """Return the total number of open proposals waiting for operator review."""
        return len(self.get_open_proposals())

    def is_queue_gated(self, max_backlog: int = 20) -> bool:
        """Evaluate whether new scheduled scans should be halted due to backlog."""
        count = self.get_open_proposals_count()
        gated = count >= max_backlog
        if gated:
            logger.warning(
                "ProposalQueue: backlog threshold exceeded (%d >= %d). Gating scheduled scout.",
                count,
                max_backlog,
            )
        return gated
