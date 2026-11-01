"""
dual_pipeline/graph_scout_core/pipeline_hook.py

Shared Layer L5 execution hook for Tier 1 GraphDB Health Scout (Wave 0).
Provides a single canonical entry point: execute_tier1_scout_hook().
Decouples pipeline stages from evals_coordinator implementation details.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class ScoutHookResult:
    """Structured result from a Tier 1 Scoped Scout execution."""
    ran: bool = False
    findings_count: int = 0
    proposals_emitted: int = 0
    elapsed_ms: float = 0.0
    skipped_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def execute_tier1_scout_hook(
    proposed_entities: Optional[Sequence[Dict[str, Any]]] = None,
    output_dir: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
    dir_id: Optional[str] = None,
    written: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ScoutHookResult:
    """Canonical entry point to execute the Tier 1 Scoped Scout.

    Unconditionally safe: catches all exceptions, logs warnings, and returns
    a populated ScoutHookResult without ever aborting the calling pipeline.

    Args:
        proposed_entities: In-memory entity dicts from Output 03 entity_upserts.
        output_dir: Destination directory for deliverables and proposals.
        runtime_root: The governed runtime root containing active_pair.json.
        dir_id: Ingestion directive ID for ledger audit trail.
        written: Deliverable artifact map (for fallback Output 03 discovery).
        metadata: Stage execution metadata.
    """
    if metadata and metadata.get("test_fast"):
        return ScoutHookResult(
            ran=False,
            findings_count=0,
            proposals_emitted=0,
            elapsed_ms=0.0,
            skipped_reason="TestFastMode",
        )

    if written is not None and written.get("tier1_scout_executed") and "tier1_scout_result" in written:
        res_dict = written["tier1_scout_result"]
        return ScoutHookResult(
            ran=bool(res_dict.get("ran", False)),
            findings_count=int(res_dict.get("findings_count", 0)),
            proposals_emitted=int(res_dict.get("proposals_emitted", 0)),
            elapsed_ms=float(res_dict.get("elapsed_ms", 0.0)),
            skipped_reason="AlreadyExecutedInPriorStage",
        )

    start_t = time.perf_counter()
    try:
        from dual_pipeline.transcript_pipeline_core.evals_coordinator import (
            _execute_phase3_scoped_scout,
        )

        res_dict = _execute_phase3_scoped_scout(
            written=written or {},
            output_dir=output_dir,
            canon_runtime_root=runtime_root,
            dir_id=dir_id,
            meta=metadata or {},
            proposed_entities=proposed_entities,
        )
        elapsed = (time.perf_counter() - start_t) * 1000.0
        res_dict["elapsed_ms"] = round(elapsed, 2)
        if written is not None:
            written["tier1_scout_executed"] = True
            written["tier1_scout_result"] = res_dict
        return ScoutHookResult(
            ran=bool(res_dict.get("ran", False)),
            findings_count=int(res_dict.get("findings_count", 0)),
            proposals_emitted=int(res_dict.get("proposals_emitted", 0)),
            elapsed_ms=round(elapsed, 2),
            skipped_reason=res_dict.get("skipped_reason"),
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - start_t) * 1000.0
        logger.warning("execute_tier1_scout_hook failed non-fatally: %s", exc, exc_info=True)
        return ScoutHookResult(
            ran=False,
            findings_count=0,
            proposals_emitted=0,
            elapsed_ms=round(elapsed, 2),
            skipped_reason=f"HookException: {exc}",
        )
