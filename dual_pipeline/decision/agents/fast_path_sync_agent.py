"""Agent-D3: Untrusted Fast-Path Synchronizer.

Enforces Law 3 (Untrusted Fast Path):
- Live, ephemeral STT context / in-flight transcript turns are held in read-only memory.
- Strictly forbidden from directly writing or committing to canonical graph or indexes.
- Every fast-path item is stamped with [UNTRUSTED_FAST_PATH] badge and is_untrusted_fast_path=True.
- Guarantees ephemeral fusion with canonical graph results while preventing contamination.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)

logger = logging.getLogger("dual_pipeline.agents.fast_path_sync")


FAST_PATH_BADGE = "[UNTRUSTED_FAST_PATH]"


class Law3ViolationError(Exception):
    """Raised when an illegal attempt is made to commit untrusted fast-path data to canonical graph."""
    pass


def get_fast_path_sync_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:fast-path-sync:1.0.0",
        name="FastPathSyncAgent",
        version="1.0.0",
        role="Untrusted Fast-Path Synchronizer",
        domain_focus="Law 3 Untrusted Fast Path ingestion, ephemeral live STT context synchronization, read-only boundary",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id="deterministic",
            provider="local",
            temperature=0.0,
        ),
        skills=["SynchronizeFastPathContext", "TagUntrustedFastPath", "EnforceLaw3ReadOnlyBoundary"],
        allowed_tools=["memory_scratchpad"],
    )


class FastPathSyncAgent:
    """Agent managing ephemeral live meeting context under Law 3."""

    def __init__(self, conviction_threshold: float = 0.85) -> None:
        self.card = get_fast_path_sync_card()
        self.conviction_threshold = float(conviction_threshold)
        self._ephemeral_turns: List[Dict[str, Any]] = []

    def tighten_conviction_threshold(self, delta: float = 0.05, max_ceiling: float = 0.98) -> float:
        """Dynamically tightens conviction threshold based on operator override cohorts."""
        self.conviction_threshold = min(max_ceiling, round(self.conviction_threshold + delta, 3))
        logger.info(f"FastPathSyncAgent tightened conviction threshold to {self.conviction_threshold}")
        return self.conviction_threshold

    def is_fast_path_admissible(self, confidence: float) -> bool:
        """Evaluates whether live confidence clears the current calibrated threshold."""
        try:
            return float(confidence or 0.0) >= self.conviction_threshold
        except (ValueError, TypeError):
            return False

    def ingest_live_turn(
        self,
        speaker: str,
        text: str,
        timestamp: Optional[str] = None,
        confidence: float = 0.85,
    ) -> Dict[str, Any]:
        """Ingests an ephemeral live turn into isolated in-memory storage."""
        turn_id = f"fastpath-turn-{len(self._ephemeral_turns) + 1}"
        badged_text = f"{FAST_PATH_BADGE} {text.strip()}"
        admissible = self.is_fast_path_admissible(confidence)

        entry = {
            "turn_id": turn_id,
            "speaker": speaker,
            "raw_text": text,
            "badged_text": badged_text,
            "badge": FAST_PATH_BADGE,
            "timestamp": timestamp or "00:00:00",
            "confidence": confidence,
            "conviction_threshold": self.conviction_threshold,
            "fast_path_admissible": admissible,
            "is_untrusted_fast_path": True,
            "verification_status": "UNTRUSTED_FAST_PATH",
            "canonical_committed": False,
        }
        self._ephemeral_turns.append(entry)
        logger.debug(f"FastPathSyncAgent ingested live turn {turn_id} (speaker: {speaker}, admissible: {admissible})")
        return entry

    def get_ephemeral_context(self) -> List[Dict[str, Any]]:
        """Returns read-only snapshot of current ephemeral context."""
        return [dict(t) for t in self._ephemeral_turns]

    def clear_ephemeral_context(self) -> None:
        """Clears ephemeral live memory."""
        self._ephemeral_turns.clear()

    def fuse_with_canonical_evidence(
        self,
        canonical_facts: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Fuses canonical facts with ephemeral live turns, preserving strict attribution."""
        fused: List[Dict[str, Any]] = []

        # Add verified canonical facts
        for f in canonical_facts:
            item = dict(f)
            item["is_untrusted_fast_path"] = False
            item["badge"] = "[CANONICAL_VERIFIED]"
            fused.append(item)

        # Append stamped ephemeral fast-path items
        for t in self._ephemeral_turns:
            fused.append({
                "node_id": t["turn_id"],
                "content": t["badged_text"],
                "speaker": t["speaker"],
                "badge": FAST_PATH_BADGE,
                "is_untrusted_fast_path": True,
                "verification_status": "UNTRUSTED_FAST_PATH",
            })

        return fused

    def validate_no_canonical_mutation(self, target_operation: str) -> None:
        """Enforces Law 3: Fast-path context cannot trigger canonical graph mutations."""
        forbidden_mutations = {"INSERT_NODE", "UPDATE_NODE", "COMMIT_GRAPH", "ACTIVATE_RELEASE", "WRITE_VECTOR"}
        if target_operation.upper() in forbidden_mutations:
            raise Law3ViolationError(
                f"Law 3 Violation: Untrusted fast-path context attempted forbidden canonical mutation '{target_operation}'. "
                "Fast-path is strictly read-only and ephemeral."
            )

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation."""
        live_turns = task.input_payload.get("live_turns", [])
        canonical_facts = task.input_payload.get("canonical_facts", [])
        attempted_mutation = task.input_payload.get("attempted_mutation")

        if attempted_mutation:
            try:
                self.validate_no_canonical_mutation(attempted_mutation)
            except Law3ViolationError as exc:
                return {
                    "task_id": task.task_id,
                    "status": "FAILED",
                    "error": str(exc),
                    "law_violation": "LAW_3_UNTRUSTED_FAST_PATH",
                }

        ingested = []
        for t in live_turns:
            ingested.append(self.ingest_live_turn(
                speaker=t.get("speaker", "Unknown"),
                text=t.get("text", ""),
                timestamp=t.get("timestamp"),
                confidence=float(t.get("confidence", 0.85)),
            ))

        fused = self.fuse_with_canonical_evidence(canonical_facts)

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "ingested_turns_count": len(ingested),
            "total_ephemeral_turns": len(self._ephemeral_turns),
            "fused_evidence_count": len(fused),
            "fused_evidence": fused,
        }
