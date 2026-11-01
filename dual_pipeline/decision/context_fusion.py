"""Pipeline D: Copilot Context Fusion Engine.

Fuses the Ephemeral Fast Path (Live STT) with the Settled Slow Path (Knowledge Graph).
Enforces:
  - Law 3: Untrusted Fast Path (Clear visual & semantic separation between live ephemeral STT and settled graph facts)
  - Law 7: Invariant Pipeline Sequencing (Evidence -> Canonical Graph Delta -> Executive Briefing)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

logger = logging.getLogger("dual_pipeline.decision.context_fusion")

CLIENT_SAFE_NAMESPACE = "CLIENT_SAFE"
INTERNAL_ADVISORY_NAMESPACE = "INTERNAL_ADVISORY"


class FactProtocol(Protocol):
    node_id: str
    evidence_id: str
    namespace: str
    type: str
    name: str
    snippet: str
    strategic_value: float
    confidence: float
    tacticality: float
    is_quarantined: bool
    relevance_score: float
    properties: Dict[str, Any]
    connected_edges: List[Dict[str, Any]]
    traceparent: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        ...


@dataclass
class CopilotFusionPayload:
    """
    Fused intelligence payload combining:
      1. Live Fast Path Context (Ephemeral, Unverified)
      2. Reconciled Slow Path Facts (Settled, Policy-Gated)
      3. Dialectical Reconciliations (Contradictions, Corroborations, Tensions)
    """
    query: str
    fast_path_context: List[Dict[str, Any]]
    reconciled_canonical_facts: List[Any]
    reconciled_advisory_facts: List[Any]
    executive_synthesis: str
    stealth_mode: bool
    policy_check_passed: bool = True
    dialectical_reconciliations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "fast_path_context": self.fast_path_context,
            "reconciled_canonical_facts": [
                f.to_dict() if hasattr(f, "to_dict") else f for f in self.reconciled_canonical_facts
            ],
            "reconciled_advisory_facts": [
                f.to_dict() if hasattr(f, "to_dict") else f for f in self.reconciled_advisory_facts
            ],
            "executive_synthesis": self.executive_synthesis,
            "stealth_mode": self.stealth_mode,
            "policy_check_passed": self.policy_check_passed,
            "dialectical_reconciliations": self.dialectical_reconciliations,
        }


class CopilotFusionEngine:
    """
    Fuses the Ephemeral Fast Path (Live STT) with the Settled Slow Path (Graph).
    Enforces Law 3: Ambient live context is clearly flagged as unverified.
    """

    def __init__(self, retrieval_engine: Any):
        self.retrieval_engine = retrieval_engine

    def _reconcile_dialectics(
        self,
        live_fast_path_events: List[Dict[str, Any]],
        canonical_facts: List[Any],
    ) -> List[Dict[str, Any]]:
        """Reconciles live fast-path utterances against settled canonical facts for dialectical alignment."""
        reconciliations: List[Dict[str, Any]] = []
        tension_keywords = {
            "replace", "deprecate", "drop", "alternative", "issue", "defect",
            "fail", "slow", "bottleneck", "rearchitect", "reject", "oppose",
            "drift", "conflict", "contradict", "problem", "wrong"
        }

        for event in live_fast_path_events:
            speaker = event.get("speaker", "Speaker")
            text = event.get("text", "")
            text_lower = text.lower()
            event_matched = False

            for fact in canonical_facts:
                fact_name = getattr(fact, "name", "")
                fact_id = getattr(fact, "node_id", getattr(fact, "id", "canonical_node"))
                fact_tokens = [t.lower() for t in fact_name.split() if len(t) > 3]

                matches_fact = any(t in text_lower for t in fact_tokens) or (str(fact_id).lower() in text_lower)
                if matches_fact:
                    event_matched = True
                    has_tension = any(kw in text_lower for kw in tension_keywords)
                    rec_type = "DIALECTICAL_TENSION" if has_tension else "CORROBORATED"
                    reconciliations.append({
                        "event_speaker": speaker,
                        "event_text": text,
                        "canonical_node_id": fact_id,
                        "canonical_name": fact_name,
                        "reconciliation_type": rec_type,
                        "description": (
                            f"Live utterance by {speaker} suggests dialectical tension with settled fact '{fact_name}'"
                            if has_tension
                            else f"Live utterance by {speaker} corroborates settled architecture fact '{fact_name}'"
                        ),
                        "requires_hitl_arbitration": has_tension,
                    })

            if not event_matched and text.strip():
                reconciliations.append({
                    "event_speaker": speaker,
                    "event_text": text,
                    "canonical_node_id": None,
                    "canonical_name": None,
                    "reconciliation_type": "UNVERIFIED_FAST_PATH",
                    "description": f"Live utterance by {speaker} introduces unanchored claim not in canonical graph",
                    "requires_hitl_arbitration": False,
                })

        return reconciliations

    def fuse_context(
        self,
        query: str,
        live_fast_path_events: List[Dict[str, Any]],
        stealth_mode: bool = True,
    ) -> CopilotFusionPayload:
        """
        Synthesizes an executive response blending live conversation with settled facts.
        """
        # Retrieve settled facts through policy gate
        all_retrieved = self.retrieval_engine.search(query=query, stealth_mode=stealth_mode)

        canonical_facts = [f for f in all_retrieved if getattr(f, "namespace", "") == CLIENT_SAFE_NAMESPACE]
        advisory_facts = [f for f in all_retrieved if getattr(f, "namespace", "") == INTERNAL_ADVISORY_NAMESPACE]

        # Dialectical context reconciliation
        dialectical_reconciliations = self._reconcile_dialectics(live_fast_path_events, canonical_facts)
        tensions = [r for r in dialectical_reconciliations if r["reconciliation_type"] == "DIALECTICAL_TENSION"]

        # Generate Executive Synthesis
        synthesis_lines = []
        if canonical_facts:
            synthesis_lines.append(
                f"**Settled Architecture Baseline:** Found {len(canonical_facts)} verified enterprise facts "
                f"(e.g., '{canonical_facts[0].name}')."
            )
        else:
            synthesis_lines.append("**Settled Architecture Baseline:** No canonical decisions registered.")

        if not stealth_mode and advisory_facts:
            synthesis_lines.append(
                f"**Internal Advisory Lens:** {len(advisory_facts)} strategic observations identified "
                f"across {advisory_facts[0].type}."
            )

        if live_fast_path_events:
            last_event = live_fast_path_events[-1]
            synthesis_lines.append(
                f"**Ambient Fast-Path (Unverified):** '{last_event.get('speaker', 'Speaker')}': "
                f"\"{last_event.get('text', '')}\""
            )
        else:
            synthesis_lines.append("**Ambient Fast-Path:** Inactive / No recent utterances.")

        if tensions:
            synthesis_lines.append(
                f"**Dialectical Tension Detected:** Fast-path discussion indicates divergence with settled architecture: "
                f"'{tensions[0]['description']}'"
            )

        executive_synthesis = "\n\n".join(synthesis_lines)

        return CopilotFusionPayload(
            query=query,
            fast_path_context=live_fast_path_events[-5:],  # Keep 5 most recent
            reconciled_canonical_facts=canonical_facts,
            reconciled_advisory_facts=advisory_facts,
            executive_synthesis=executive_synthesis,
            stealth_mode=stealth_mode,
            policy_check_passed=True,
            dialectical_reconciliations=dialectical_reconciliations,
        )

    def stream_programmatic_fusion(
        self,
        query: str,
        live_fast_path_events: Sequence[Mapping[str, Any]],
        sliding_window_size: int = 5,
        stealth_mode: bool = True,
    ) -> Dict[str, Any]:
        """Programmatic entrypoint for streaming context fusion and Law 3 verification (PCFS)."""
        from dual_pipeline.ptc.context_fusion import (
            PTCContextFusionStreamer,
            PTCFusionPlan,
        )
        all_retrieved = self.retrieval_engine.search(query=query, stealth_mode=stealth_mode)
        canonical = [
            f.to_dict() if hasattr(f, "to_dict") else dict(f)
            for f in all_retrieved
            if getattr(f, "namespace", "") == CLIENT_SAFE_NAMESPACE
        ]
        advisory = [
            f.to_dict() if hasattr(f, "to_dict") else dict(f)
            for f in all_retrieved
            if getattr(f, "namespace", "") == INTERNAL_ADVISORY_NAMESPACE
        ]
        plan = PTCFusionPlan(
            query=query,
            live_fast_path_events=live_fast_path_events,
            settled_canonical_facts=canonical,
            settled_advisory_facts=advisory,
            sliding_window_size=sliding_window_size,
            stealth_mode=stealth_mode,
        )
        streamer = PTCContextFusionStreamer()
        payload = streamer.fuse(plan)
        return payload.to_dict()

    def synthesize_copilot_briefing(
        self,
        payload: CopilotFusionPayload,
        tier: Any = "FAST_ROUTING",
        max_bullets: int = 6,
    ) -> Dict[str, Any]:
        """
        Synthesizes a low-latency, MECE executive briefing for real-time Copilot use cases.
        Dynamically binds to ModelTier.FAST_ROUTING or ModelTier.FRONTIER_REASONING
        with parameter immunity sourced from env_di (zero hardcoded model strings).
        """
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        effective_tier = tier if isinstance(tier, ModelTier) else ModelTier(str(tier))

        # Dynamically resolve kwargs with parameter immunity from env_di
        call_kwargs = ModelRegistry.prepare_copilot_call_kwargs(effective_tier)

        bullets: List[str] = []

        # 1. Settled Baseline Fact(s)
        if payload.reconciled_canonical_facts:
            top_fact = payload.reconciled_canonical_facts[0]
            name = getattr(top_fact, "name", str(top_fact))
            snippet = getattr(top_fact, "snippet", "")
            bullets.append(f"Settled Baseline: Grounded in '{name}' ({snippet[:90]}...)" if snippet else f"Settled Baseline: Grounded in '{name}'.")
        else:
            bullets.append("Settled Baseline: No conflicting canonical decisions registered.")

        # 2. Dialectical Tensions (if any)
        tensions = [r for r in payload.dialectical_reconciliations if r.get("reconciliation_type") == "DIALECTICAL_TENSION"]
        if tensions:
            bullets.append(f"Dialectical Tension: Divergence between live discussion and settled facts ({tensions[0].get('description', '')}).")

        # 3. Ambient Fast-Path Ephemeral Signals
        if payload.fast_path_context:
            last = payload.fast_path_context[-1]
            speaker = last.get("speaker", "Speaker")
            text = last.get("text", "")
            bullets.append(f"Live Fast-Path: '{speaker}' stated \"{text[:100]}\" (unverified live STT).")

        # 4. Internal Advisory Lens (if unmasked)
        if not payload.stealth_mode and payload.reconciled_advisory_facts:
            top_adv = payload.reconciled_advisory_facts[0]
            adv_name = getattr(top_adv, "name", str(top_adv))
            bullets.append(f"Internal Advisory: Strategic leverage note '{adv_name}'.")

        # Bound to max_bullets
        formatted_bullets = bullets[:max_bullets]
        mece_summary = "\n".join(f"* {b}" for b in formatted_bullets)

        return {
            "tier": effective_tier.value,
            "resolved_model": call_kwargs.get("model"),
            "reasoning_effort": call_kwargs.get("reasoning_effort"),
            "call_kwargs": call_kwargs,
            "mece_bullets": formatted_bullets,
            "formatted_summary": mece_summary,
            "is_parameter_immune": "temperature" not in call_kwargs and "top_p" not in call_kwargs,
        }
