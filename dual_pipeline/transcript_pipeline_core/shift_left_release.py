"""Shift-left pre-publish coordination and exception parking."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Union

logger = logging.getLogger("nee.shift_left.release")


def handle_shift_left_prepublish(
    metadata: Dict[str, Any],
    written: Dict[str, Any],
    package_path: Union[Path, str],
    canon_runtime_root: Path,
) -> None:
    """Execute non-mutating preview dry-run, park fast-promote exceptions, and persist decisions."""
    target_graph = Path(metadata["graph_path"]).resolve() if metadata.get("graph_path") else (canon_runtime_root / "graph" / "nee_graph.sqlite")
    if target_graph.exists() and Path(package_path).is_file():
        try:
            from dual_pipeline.intake_engine import IntakeEngine
            preview_engine = IntakeEngine(graph_path=target_graph, runtime_root=canon_runtime_root)
            digest = preview_engine.preview_c0_exceptions(Path(package_path))
            written["shift_left_exception_digest"] = digest
            has_exc = digest.get("has_exceptions", False)
            exc_count = digest.get("exception_count", 0)
            t0_count = digest.get("tier_0_auto_promoted_count", 0)
            if has_exc:
                logger.info("Shift-left dry run: %d exceptions (%d Tier-0 auto-promoted).", exc_count, t0_count)
                if metadata.get("fast_promote"):
                    from dual_pipeline.hitl_governance_core.inbox_runner import park_shift_left_exceptions
                    out_p = Path(written.get("03_graph", package_path)).parent
                    park_shift_left_exceptions(
                        runtime_root=canon_runtime_root,
                        output_dir=out_p,
                        digest=digest,
                        package_path=package_path,
                        graph_path=target_graph,
                        source_title=metadata.get("meeting") or metadata.get("title") or "Meeting Ingestion",
                    )
            else:
                logger.info("Shift-left dry run: 0 exceptions. Auto-promoting %d clean claims.", t0_count)
        except Exception as prev_err:
            logger.debug("Non-fatal C0 preview dry-run skipped: %s", prev_err)

    if metadata.get("hitl_decisions"):
        from dual_pipeline.hitl_governance_core.persistence import persist_shift_left_hitl_decisions
        out_p = Path(written.get("03_graph", package_path)).parent
        persist_shift_left_hitl_decisions(
            decisions=metadata["hitl_decisions"],
            output_dir=out_p,
            runtime_root=canon_runtime_root,
            graph_path=target_graph if target_graph.exists() else None,
        )
