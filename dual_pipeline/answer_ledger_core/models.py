"""
dual_pipeline/answer_ledger_core/models.py

Data models and canonical serialization contracts for the Governed Answer Ledger.
Enforces schema urn:nee:answer-ledger:answer-record:1.0.0 and urn:nee:answer-ledger:answer-feedback:1.0.0.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import has_c19_marker

ANSWER_RECORD_CONTRACT_ID = "urn:nee:answer-ledger:answer-record:1.0.0"
ANSWER_FEEDBACK_CONTRACT_ID = "urn:nee:answer-ledger:answer-feedback:1.0.0"
GENESIS_PREV_DIGEST = "GENESIS"


def compute_canonical_digest(data: Dict[str, Any]) -> str:
    """Computes RFC 8785 canonical sha256 digest of dictionary data."""
    canonical_json = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def sanitize_context_snapshot(snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitizes context snapshot turns, quarantining Rule C-19 career leverage content."""
    clean: List[Dict[str, Any]] = []
    for turn in snapshot:
        text = str(turn.get("text", ""))
        speaker = str(turn.get("speaker", "Unknown"))
        ts = turn.get("timestamp")
        sec = turn.get("turn_sec")
        if has_c19_marker(text):
            # Quarantine text per Rule C-19
            text = "[QUARANTINED PER RULE C-19: CAREER LEVERAGE REDACTED]"
        clean.append({
            "speaker": speaker,
            "text": text,
            "timestamp": ts,
            "turn_sec": float(sec) if sec is not None else None,
        })
    return clean


@dataclass
class AnswerRecord:
    """Represents an immutable, chain-linked answer record emitted by copilot or query."""
    pipeline: str  # "copilot" or "query"
    intent_or_type: str  # copilot intent or query type
    context_digest: str
    retrieval_seeds: List[str]
    context_snapshot: List[Dict[str, Any]]
    cited_assertion_ids: List[str]
    rigor: str
    response_text: str
    graph_sha256: Optional[str] = None
    generation: Optional[Any] = None
    parameter_snapshot_digest: Optional[str] = None
    latency_ms: Optional[float] = None
    retrieve_latency_ms: Optional[float] = None
    verify_latency_ms: Optional[float] = None
    render_latency_ms: Optional[float] = None
    stage_latencies_ms: Optional[Dict[str, float]] = None
    model_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    answer_id: str = field(default_factory=lambda: f"ans-{uuid.uuid4().hex[:12]}")
    contract_id: str = ANSWER_RECORD_CONTRACT_ID
    schema_version: str = "1.0.0"
    prev_record_digest: str = GENESIS_PREV_DIGEST
    record_digest: str = ""

    def __post_init__(self) -> None:
        if not self.context_digest:
            seeds_str = ",".join(sorted(self.retrieval_seeds))
            self.context_digest = hashlib.sha256(seeds_str.encode("utf-8")).hexdigest()
        self.context_snapshot = sanitize_context_snapshot(self.context_snapshot)

    def to_digestable_dict(self) -> Dict[str, Any]:
        """Returns dict payload excluding record_digest for hash calculation."""
        d = asdict(self)
        d.pop("record_digest", None)
        return d

    def compute_digest(self) -> str:
        """Computes canonical digest of the record bound to prev_record_digest."""
        return compute_canonical_digest(self.to_digestable_dict())

    def to_dict(self) -> Dict[str, Any]:
        """Returns complete dict representation."""
        return asdict(self)


@dataclass
class AnswerFeedback:
    """Represents operator or system feedback bound cryptographically to an AnswerRecord."""
    answer_id: str
    answer_digest: str
    feedback_type: str  # "explicit" or "implicit"
    rating: str  # "useful", "not_useful", "wrong"
    origin: str  # "USER_EXPLICIT" or "SYSTEM_INGESTION"
    note: Optional[str] = None
    weight: Optional[float] = 1.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    feedback_id: str = field(default_factory=lambda: f"fb-{uuid.uuid4().hex[:12]}")
    contract_id: str = ANSWER_FEEDBACK_CONTRACT_ID
    schema_version: str = "1.0.0"
    signature: Optional[str] = None
    proposal_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.signature:
            d = asdict(self)
            d.pop("signature", None)
            self.signature = compute_canonical_digest(d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
