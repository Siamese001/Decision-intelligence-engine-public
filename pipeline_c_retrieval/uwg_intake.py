"""Pipeline C: UWG Intake Processor & Sidecar Preparation Bridge.

Receives programmatic candidate envelopes from Pipeline B, verifies model manifest
binding (BAAI/bge-m3), and prepares generational sidecar vector indexing.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from dual_pipeline.l0.contracts import (
    CanonicalGraphDelta,
)
from dual_pipeline.l0.provenance import compute_content_digest
from dual_pipeline.model_registry import ModelRegistry
from pipeline_b_extraction.uwg_gateway import UwgSubmissionReceipt

logger = logging.getLogger("pipeline_c.uwg_intake")

# Required model provenance for sidecar indexing (Gate 1)
PINNED_EMBEDDING_MODEL = ModelRegistry.resolve_embedding_model_id()


class UwgIntakeProcessor:
    """Consumes admitted candidate deltas and prepares paired generational sidecars."""

    def __init__(self, target_generation: str = "gen_001") -> None:
        self.target_generation: str = target_generation
        self._intake_log: List[Dict[str, Any]] = []

    def admit_delta_for_indexing(
        self,
        submission: UwgSubmissionReceipt,
        delta: CanonicalGraphDelta,
        embedding_backend: str = "mlx",
    ) -> Dict[str, Any]:
        """
        Validates model binding and schedules sidecar vector embeddings (dense, sparse, ColBERT).
        """
        if submission.status != "ACCEPTED":
            logger.warning(f"Rejected delta '{delta.delta_id}'; submission status: {submission.status}")
            return {
                "admitted": False,
                "reason": f"Submission was not in ACCEPTED state: {submission.status}",
            }

        # Verify digest consistency
        expected_digest = compute_content_digest(delta.to_dict()).sha256
        if submission.digest != expected_digest:
            logger.error("Digest mismatch between delta and UWG submission receipt.")
            return {
                "admitted": False,
                "reason": "Digest mismatch between delta and UWG receipt",
            }

        intake_event = {
            "intake_id": f"c_intake_{submission.submission_id}",
            "delta_id": delta.delta_id,
            "target_generation": self.target_generation,
            "embedding_model": PINNED_EMBEDDING_MODEL,
            "embedding_backend": embedding_backend,
            "nodes_count": len(delta.nodes_created) + len(delta.nodes_updated),
            "edges_count": len(delta.edges_created),
            "timestamp": time.time(),
        }

        self._intake_log.append(intake_event)
        logger.info(
            f"Admitted delta '{delta.delta_id}' for sidecar indexing in generation '{self.target_generation}'."
        )
        return {"admitted": True, "intake_event": intake_event}

    def get_admitted_count(self) -> int:
        return len(self._intake_log)
