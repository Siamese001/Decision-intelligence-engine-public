"""Intent Extractor for Stage U0 / Output 3 Scope Modulation."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .models import StrategicIntentSignal

# Scope modulation patterns in spoken or synthesized dialogue
_SCOPE_CUT_PATTERNS = [
    (re.compile(r"\bnot\s+focusing\s+on\s+([a-z0-9_\s-]+)", re.I), "DEFERRED_OUT_OF_SCOPE"),
    (re.compile(r"\bdeferred\b.*?([a-z0-9_\s-]+)", re.I), "DEFERRED_OUT_OF_SCOPE"),
    (re.compile(r"\bout\s+of\s+scope\b.*?([a-z0-9_\s-]+)", re.I), "DEFERRED_OUT_OF_SCOPE"),
    (re.compile(r"\b(?:pod\s+model|independent\s+pods?)\s+(?:is\s+)?not\s+working\s+out\b", re.I), "DISCONTINUED"),
    (re.compile(r"\b106[- ]swe\b.*?(?:provided|readout|closed|delivered)\b", re.I), "COMPLETED_IN_READOUT"),
]


class IntentExtractor:
    """Extracts and validates strategic scope modulation signals from transcript outputs."""

    @staticmethod
    def extract_from_output_3(output_3_dict: Dict[str, Any]) -> List[StrategicIntentSignal]:
        """Extract explicit strategic_intent_signals from Output 3 dict."""
        signals: List[StrategicIntentSignal] = []
        gm = output_3_dict.get("graph_mutations", {})
        if not isinstance(gm, dict):
            return signals

        raw_signals = gm.get("strategic_intent_signals", [])
        if not isinstance(raw_signals, list):
            return signals

        for item in raw_signals:
            if isinstance(item, dict) and "signal_id" in item and "target_topic_or_entity" in item:
                signals.append(StrategicIntentSignal.from_dict(item))

        return signals

    @staticmethod
    def scan_recap_text(recap_text: str, observed_at: str = "08/27/2026") -> List[StrategicIntentSignal]:
        """Heuristic fallback to extract scope modulation intent from executive recap text."""
        signals: List[StrategicIntentSignal] = []

        # Check for AI SDLC / evals deferral
        if re.search(r"not focusing on AI SDLC|SDLC redesign.*?out of scope|broader methodology work deferred", recap_text, re.I):
            signals.append(
                StrategicIntentSignal(
                    signal_id="sig-ai-sdlc-deferred",
                    target_topic_or_entity="workstream:ai_sdlc",
                    posture="DEFERRED_OUT_OF_SCOPE",
                    evidence_statement_ids=[],
                    rationale="Executive recap explicitly defers SDLC redesign and AI evals focus in favor of foundational staffing.",
                    observed_at=observed_at,
                    scope_type="WORKSTREAM",
                )
            )

        # Check for 106 SWE decomposition completion
        if re.search(r"106[- ]swe|106/66/40", recap_text, re.I) and re.search(r"provided|readout|addressed", recap_text, re.I):
            signals.append(
                StrategicIntentSignal(
                    signal_id="sig-106-swe-delivered",
                    target_topic_or_entity="initiative:106_swe",
                    posture="COMPLETED_IN_READOUT",
                    evidence_statement_ids=[],
                    rationale="106-SWE decomposition completed/provided in executive readout.",
                    observed_at=observed_at,
                    scope_type="INITIATIVE",
                )
            )

        # Check for Pod model discontinuation
        if re.search(r"pod model.*?not working out|scaling independent pods.*?deferred", recap_text, re.I):
            signals.append(
                StrategicIntentSignal(
                    signal_id="sig-pod-model-discontinued",
                    target_topic_or_entity="operating_model:pods",
                    posture="DISCONTINUED",
                    evidence_statement_ids=[],
                    rationale="Client leadership confirmed independent pod model is not working out.",
                    observed_at=observed_at,
                    scope_type="OPERATING_MODEL",
                )
            )

        return signals
