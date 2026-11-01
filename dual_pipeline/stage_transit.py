"""
dual_pipeline/stage_transit.py

Cryptographic stage transit envelopes for immutable monotonic pipeline stage chaining.
Ensures that each pipeline stage cryptographically verifies the transit token of its
immediate predecessor, preventing out-of-order execution and skipped stages.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("dual_pipeline.stage_transit")

GENESIS_PREDECESSOR_HASH = "0" * 64


class StageSequenceViolationError(ValueError):
    """Raised when stage transition chaining is violated, skipped, or out-of-order."""


@dataclass(frozen=True)
class StageTransitEnvelope:
    """Cryptographic chaining envelope proving monotonic stage progression."""
    stage: str
    sequence_number: int
    predecessor_hash: str
    stage_output_digest: str
    timestamp_utc: str
    transit_token: str

    @classmethod
    def mint(
        cls,
        stage: str,
        sequence_number: int,
        predecessor_hash: str,
        stage_output_digest: str = "",
    ) -> StageTransitEnvelope:
        ts = datetime.now(timezone.utc).isoformat()
        payload = f"{stage}:{sequence_number}:{predecessor_hash}:{stage_output_digest}:{ts}".encode("utf-8")
        token = hashlib.sha256(payload).hexdigest()
        return cls(
            stage=stage,
            sequence_number=sequence_number,
            predecessor_hash=predecessor_hash,
            stage_output_digest=stage_output_digest,
            timestamp_utc=ts,
            transit_token=token,
        )

    def verify_predecessor(self, predecessor: Optional[StageTransitEnvelope]) -> bool:
        """Verifies that this envelope's predecessor_hash matches the predecessor's transit_token."""
        if predecessor is None:
            return self.sequence_number == 0 and self.predecessor_hash == GENESIS_PREDECESSOR_HASH
        return (
            self.predecessor_hash == predecessor.transit_token
            and self.sequence_number == predecessor.sequence_number + 1
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StageTransitChain:
    """Cryptographic ledger tracking and validating monotonic stage transit envelopes."""

    def __init__(self) -> None:
        self._chain: list[StageTransitEnvelope] = []

    @property
    def current_token(self) -> Optional[StageTransitEnvelope]:
        return self._chain[-1] if self._chain else None

    @property
    def depth(self) -> int:
        return len(self._chain)

    def advance(
        self,
        stage: str,
        stage_output_digest: str = "",
        provided_token: Optional[StageTransitEnvelope] = None,
    ) -> StageTransitEnvelope:
        """
        Advances the chain to the new stage, verifying cryptographic integrity.
        """
        seq = len(self._chain)
        pred_hash = self._chain[-1].transit_token if self._chain else GENESIS_PREDECESSOR_HASH

        if provided_token is not None:
            if not provided_token.verify_predecessor(self.current_token):
                raise StageSequenceViolationError(
                    f"Cryptographic transit verification failed for stage '{stage}': "
                    f"Provided predecessor hash '{provided_token.predecessor_hash}' does not match "
                    f"expected chain token '{pred_hash}'."
                )
            self._chain.append(provided_token)
            return provided_token

        new_envelope = StageTransitEnvelope.mint(
            stage=stage,
            sequence_number=seq,
            predecessor_hash=pred_hash,
            stage_output_digest=stage_output_digest,
        )
        self._chain.append(new_envelope)
        return new_envelope

    def to_list(self) -> list[Dict[str, Any]]:
        return [env.to_dict() for env in self._chain]
