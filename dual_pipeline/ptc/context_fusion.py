"""
dual_pipeline/ptc/context_fusion.py

Programmatic Context Fusion Streamer (PCFS) for Pipeline D.
Enforces:
- Law 3: Untrusted Fast Path (Ambient live STT utterances are strictly flagged untrusted and ephemeral)
- Local in-process fusion between live STT Fast Path and settled Slow Path graph facts
- Sliding window buffer management (default 5 items) preventing unbounded memory growth
- Automated disk spooling via PTCOutputSpooler for payloads exceeding TokenBudget
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dual_pipeline.core import digest, utc_now
from dual_pipeline.ptc.contracts import TokenBudget
from dual_pipeline.ptc.spooler import PTCOutputSpooler

CLIENT_SAFE_NAMESPACE = "CLIENT_SAFE"
INTERNAL_ADVISORY_NAMESPACE = "INTERNAL_ADVISORY"


@dataclass
class PTCLiveFastPathEvent:
    """Ephemeral, unverified live speech-to-text event from Pipeline A Fast Path."""
    event_id: str
    speaker: str
    text: str
    timestamp: str = ""
    untrusted: bool = True   # Law 3: Fast-path events must always be marked untrusted
    ephemeral: bool = True   # Law 3: Fast-path events must always be marked ephemeral
    confidence: float = 0.85
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PTCFusionPlan:
    """Input plan declaring query, incoming fast-path stream, and settled graph facts."""
    query: str
    live_fast_path_events: Sequence[PTCLiveFastPathEvent | Dict[str, Any]]
    settled_canonical_facts: Sequence[Mapping[str, Any]] = field(default_factory=list)
    settled_advisory_facts: Sequence[Mapping[str, Any]] = field(default_factory=list)
    sliding_window_size: int = 5
    stealth_mode: bool = True
    token_budget: Optional[TokenBudget] = None


@dataclass
class PTCFusedContextPayload:
    """Structured fused intelligence payload ready for cockpit lenses and assistant context."""
    query: str
    fast_path_context: List[Dict[str, Any]]
    reconciled_canonical_facts: List[Dict[str, Any]]
    reconciled_advisory_facts: List[Dict[str, Any]]
    executive_briefing_snippet: str
    law3_verified: bool = True
    fusion_digest: str = ""
    is_spooled: bool = False
    spool_path: Optional[str] = None

    def to_compact_json(self) -> str:
        data = asdict(self)
        return json.dumps(data, separators=(",", ":"), default=str)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PTCContextFusionStreamer:
    """In-process context fusion streamer uniting live Fast Path with settled Slow Path."""

    def __init__(self, spooler: Optional[PTCOutputSpooler] = None) -> None:
        self.spooler = spooler or PTCOutputSpooler()

    def fuse(self, plan: PTCFusionPlan) -> PTCFusedContextPayload:
        # 1. Enforce Law 3: Process incoming fast-path events with explicit untrusted flags
        sanitized_events: List[Dict[str, Any]] = []
        for raw in plan.live_fast_path_events:
            ev = raw if isinstance(raw, dict) else raw.to_dict()
            sanitized = dict(ev)
            sanitized["untrusted"] = True
            sanitized["ephemeral"] = True
            if "timestamp" not in sanitized or not sanitized["timestamp"]:
                sanitized["timestamp"] = utc_now()
            sanitized_events.append(sanitized)

        # Slice to sliding window size (e.g. 5 most recent utterances)
        window = plan.sliding_window_size if plan.sliding_window_size > 0 else 5
        fast_path_bounded = sanitized_events[-window:]

        # 2. Reconcile settled Slow Path facts
        canonical_facts = [dict(f) for f in plan.settled_canonical_facts]
        advisory_facts = [] if plan.stealth_mode else [dict(f) for f in plan.settled_advisory_facts]

        # 3. Generate synthesized executive briefing snippet locally in-process
        briefing_lines: List[str] = []
        if canonical_facts:
            first_name = canonical_facts[0].get("name", canonical_facts[0].get("node_id", "Component"))
            briefing_lines.append(
                f"**Settled Architecture Baseline:** Found {len(canonical_facts)} verified enterprise facts "
                f"(e.g., '{first_name}')."
            )
        else:
            briefing_lines.append("**Settled Architecture Baseline:** No canonical decisions registered.")

        if not plan.stealth_mode and advisory_facts:
            first_adv = advisory_facts[0].get("name", advisory_facts[0].get("node_id", "Observation"))
            briefing_lines.append(
                f"**Internal Advisory Lens:** {len(advisory_facts)} strategic observations identified "
                f"(e.g., '{first_adv}')."
            )

        if fast_path_bounded:
            last_ev = fast_path_bounded[-1]
            speaker = last_ev.get("speaker", "Speaker")
            text = last_ev.get("text", "")
            briefing_lines.append(f"**Ambient Fast-Path (Unverified Live STT):** '{speaker}': \"{text}\"")
        else:
            briefing_lines.append("**Ambient Fast-Path:** Inactive / No recent utterances.")

        executive_briefing = "\n\n".join(briefing_lines)

        # 4. Merkle Digest Calculation
        digest_payload = {
            "query": plan.query,
            "fast_path_event_ids": [e.get("event_id", "") for e in fast_path_bounded],
            "canonical_ids": [f.get("node_id", "") for f in canonical_facts],
            "stealth_mode": plan.stealth_mode,
        }
        fusion_digest = f"sha256:{digest(digest_payload)}"

        payload = PTCFusedContextPayload(
            query=plan.query,
            fast_path_context=fast_path_bounded,
            reconciled_canonical_facts=canonical_facts,
            reconciled_advisory_facts=advisory_facts,
            executive_briefing_snippet=executive_briefing,
            law3_verified=True,
            fusion_digest=fusion_digest,
            is_spooled=False,
            spool_path=None,
        )

        # 5. Check TokenBudget and Spool if over budget
        budget = plan.token_budget or TokenBudget(max_payload_bytes=2048)
        serialized_bytes = len(payload.to_compact_json().encode("utf-8"))

        if serialized_bytes > budget.max_payload_bytes:
            task_id = f"pcfs_{abs(hash(plan.query)) % 1000000:06d}"
            spool_ref = self.spooler.spool(task_id, payload.to_dict(), budget)
            payload.is_spooled = True
            payload.spool_path = spool_ref.artifact_ref

        return payload
