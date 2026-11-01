"""Pipeline B: Direct Programmatic Universal Write Gateway (UWG) Ingestion Gateway.

Eliminates intermediate CLI subprocess execution and temporary file serialization
by exposing an in-memory, contract-governed submission interface directly to UWG.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.l0.contracts import (
    CanonicalGraphDelta,
    ValidationResult,
)
from dual_pipeline.l0.provenance import compute_content_digest
from dual_pipeline.l0.validation import validate_canonical_graph_delta

logger = logging.getLogger("pipeline_b.uwg_gateway")


@dataclass
class UwgSubmissionReceipt:
    """Programmatic submission confirmation emitted by the UWG intake gateway."""
    submission_id: str
    delta_id: str
    status: str                         # "ACCEPTED" | "QUARANTINED" | "REJECTED"
    nodes_admitted: int
    edges_admitted: int
    assertions_count: int
    digest: str
    timestamp: float = field(default_factory=time.time)
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "delta_id": self.delta_id,
            "status": self.status,
            "nodes_admitted": self.nodes_admitted,
            "edges_admitted": self.edges_admitted,
            "assertions_count": self.assertions_count,
            "digest": self.digest,
            "timestamp": self.timestamp,
            "rejection_reasons": self.rejection_reasons,
        }


class ProgrammaticUwgGateway:
    """Direct in-memory gateway submitting CanonicalGraphDelta candidates to UWG."""

    def __init__(
        self,
        target_generation_id: str = "gen_current",
        db_manager: Optional[Any] = None,
    ) -> None:
        self.target_generation_id: str = target_generation_id
        self.db_manager = db_manager

    def submit_delta(
        self,
        delta: CanonicalGraphDelta,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UwgSubmissionReceipt:
        """
        Validates delta and submits candidates directly to the Universal Write Gateway in-memory.
        Replaces external Python CLI shell spawns (tools/prepare_evidence_envelope_intake.py).
        """
        # 1. Validate delta invariants (C-01 through C-19)
        val: ValidationResult = validate_canonical_graph_delta(delta)
        if not val.is_valid:
            logger.warning(f"UWG delta rejected due to validation errors: {val.errors}")
            return UwgSubmissionReceipt(
                submission_id=f"sub_err_{int(time.time())}",
                delta_id=delta.delta_id,
                status="REJECTED",
                nodes_admitted=0,
                edges_admitted=0,
                assertions_count=0,
                digest="",
                rejection_reasons=val.errors,
            )

        # 2. Compute canonical digest of admitted delta
        delta_dict = delta.to_dict()
        delta_digest = compute_content_digest(delta_dict).sha256
        submission_id = f"uwg_sub_{delta_digest[:16]}"

        total_nodes = len(delta.nodes_created) + len(delta.nodes_updated)
        total_edges = len(delta.edges_created)
        assertions = total_nodes + total_edges

        # 3. Optional persistence to storage if db_manager is configured
        if self.db_manager is not None:
            try:
                for node in delta.nodes_created + delta.nodes_updated:
                    if hasattr(self.db_manager, "upsert_canonical_node"):
                        self.db_manager.upsert_canonical_node(node)
                for edge in delta.edges_created:
                    if hasattr(self.db_manager, "insert_canonical_edge"):
                        self.db_manager.insert_canonical_edge(edge)
            except Exception as e:
                logger.error(f"UWG delta persistence error: {e}")
                return UwgSubmissionReceipt(
                    submission_id=f"sub_err_{int(time.time())}",
                    delta_id=delta.delta_id,
                    status="REJECTED",
                    nodes_admitted=0,
                    edges_admitted=0,
                    assertions_count=0,
                    digest="",
                    rejection_reasons=[f"Database persistence failure: {e}"],
                )

        # 4. Emit receipt
        receipt = UwgSubmissionReceipt(
            submission_id=submission_id,
            delta_id=delta.delta_id,
            status="ACCEPTED",
            nodes_admitted=total_nodes,
            edges_admitted=total_edges,
            assertions_count=assertions,
            digest=delta_digest,
        )
        logger.info(
            f"Programmatic UWG Gateway accepted delta '{delta.delta_id}' with {assertions} candidate assertions."
        )
        return receipt
