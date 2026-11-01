"""Pipeline B: Direct In-Memory Intake Gateway & Compute Fork Dispatcher.

Receives HandoffEnvelopes from Pipeline A without disk polling or intermediate JSON files.
Enforces Law 1 (Compute Fork BEFORE Extraction) and WAL append-before-ack.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Tuple

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    EvidenceRecord,
    HandoffEnvelope,
)
from dual_pipeline.l0.provenance import verify_digest_integrity
from dual_pipeline.l0.validation import validate_handoff_envelope
from dual_pipeline.l0.wal_evidence import append_evidence_record

logger = logging.getLogger("pipeline_b.intake_gateway")


class ComputeForkRoutingError(RuntimeError):
    """Raised when an envelope cannot be deterministically routed to a legal compute enclave."""
    pass


class ExtractionIntakeGateway:
    """
    Programmatic entry point for Pipeline B.
    Validates inter-pipeline envelopes, persists evidence to WAL, and forks execution.
    """

    def __init__(self, wal_db_path: str | Path = "data/evidence.db") -> None:
        self.wal_db_path: Path = Path(wal_db_path)

    def route_compute_fork(self, envelope: HandoffEnvelope) -> str:
        """
        Enforces Law 1: Physical compute fork BEFORE extraction prompt assembly.
        Returns target compute posture: 'CLIENT_SAFE_CLOUD' or 'INTERNAL_ADVISORY_METAL'.
        """
        ns = envelope.claim_check.namespace

        if ns == CLIENT_SAFE_NAMESPACE:
            return "CLIENT_SAFE_CLOUD"
        elif ns == INTERNAL_ADVISORY_NAMESPACE:
            return "INTERNAL_ADVISORY_METAL"
        else:
            raise ComputeForkRoutingError(
                f"Law 1 Violation: Unknown or unclassified namespace '{ns}'."
            )

    def process_envelope(
        self,
        envelope: HandoffEnvelope,
        raw_payload_bytes: Optional[bytes] = None,
    ) -> Tuple[bool, str, Optional[EvidenceRecord]]:
        """
        Validates envelope, commits to WAL, determines compute fork route,
        and returns (success, route_destination, evidence_record).
        """
        # 1. Validate envelope schema
        validation = validate_handoff_envelope(envelope)
        if not validation.is_valid:
            logger.error(f"Envelope validation failed: {validation.errors}")
            return False, "VALIDATION_FAILED", None

        # 2. Verify payload integrity if raw bytes provided
        if raw_payload_bytes is not None:
            if not verify_digest_integrity(raw_payload_bytes, envelope.claim_check.digest):
                logger.error("ClaimCheck digest mismatch on incoming envelope.")
                return False, "DIGEST_MISMATCH", None

        # 3. Determine compute fork route BEFORE extraction (Law 1)
        try:
            route = self.route_compute_fork(envelope)
        except ComputeForkRoutingError as e:
            logger.error(f"Compute fork routing failed: {e}")
            return False, "ROUTING_FAILED", None

        # 4. Construct and append EvidenceRecord to WAL before ack (Law 4)
        text_content = envelope.payload.get("text", "")
        speaker = envelope.payload.get("speaker", "Speaker")
        session_id = envelope.payload.get("session_id", "default_session")
        source_id = envelope.payload.get("source_id", "live_stream")

        record = EvidenceRecord(
            evidence_id=f"ev_{envelope.claim_check.digest[:12]}",
            session_id=session_id,
            source_id=source_id,
            speaker=speaker,
            text=text_content,
            timestamp=time.time(),
            namespace=envelope.claim_check.namespace,
            tenant_id=envelope.claim_check.tenant_id,
            digest=envelope.claim_check.digest,
            properties=envelope.payload.get("properties", {}),
        )

        append_evidence_record(self.wal_db_path, record)
        return True, route, record
