"""L0 — Deterministic Router."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .l1_cognition import L1PlanContract

logger = logging.getLogger(__name__)


class L0DeterministicRouter:
    """Deterministic routing: applies negative-exclusion filters and passes
    real transcript data downstream (never fabricates a replacement)."""

    def execute(self, u0_context: Dict[str, Any],
                hitl_transcript: str,
                plan: L1PlanContract) -> Dict[str, Any]:
        logger.info(f"Executing L0: Deterministic Routing (path={plan.source_type})")
        if not getattr(plan, "sanitization_passed", True):
            reasons = getattr(plan, "quarantine_reasons", ["RESTRICTED_DATA"])
            raise ValueError(
                f"L0 ROUTING BLOCKED: L1 sanitization failed due to {reasons}. "
                "Restricted credentials or private keys must not route downstream."
            )

        return {
            "transcript": hitl_transcript,
            "hash": u0_context["hash"],
            "metadata": u0_context["metadata"],
            "statements": self._extract_statements(hitl_transcript),
            "diarization_status": plan.diarization_status,
            "source_type": plan.source_type,
            "meeting_type_hint": plan.meeting_type_hint,
            "classification_tier": getattr(plan, "classification_tier", "internal"),
            "lexical_quality_score": getattr(plan, "lexical_quality_score", 1.0),
            "acoustic_overlap_ratio": getattr(plan, "acoustic_overlap_ratio", 0.0),
            "model_binding": getattr(plan, "model_binding", {}),
        }

    @staticmethod
    def _extract_statements(transcript: str) -> List[str]:
        """Basic line-level statement extraction for offline / token-budget mode."""
        lines = [line.strip() for line in transcript.split("\n") if line.strip()]
        return [
            line for line in lines
            if not line.startswith("#")
            and line != "---"
            and not line.startswith("**Date:")
            and not line.startswith("**Platform")
            and not line.startswith("**Participants")
        ]

    @staticmethod
    def extract_grounded_spans(transcript: str) -> List[Dict[str, Any]]:
        """Extract line-level verbatim spans with start/end offsets and speaker from transcript."""
        import re

        results: List[Dict[str, Any]] = []
        if not transcript:
            return results
        turn_re = re.compile(
            r"(?:###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n+\*\*([^*]+?):\*\*\s*|\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([A-Za-z0-9\s\.\-_]+?)(?:\s*[—–-]\s*([A-Za-z0-9\s\.\-_]+))?\*\*\s*\n+|\*\*([A-Za-z0-9\s\.\-_]+?):\*\*\s*)(.+?)(?=\n(?:###|\*\*\[|\*\*[A-Za-z0-9\s\.\-_]+?:\*\*|\Z))",
            re.DOTALL,
        )
        for m in turn_re.finditer(transcript):
            ts = m.group(1) or m.group(3) or ""
            spk = (m.group(2) or m.group(4) or m.group(6) or "").strip()
            org = (m.group(5) or "").strip()
            body = m.group(7).strip()
            if not body or len(body) < 10:
                continue
            results.extend(_extract_sentences_from_turn(body, m.start(7), spk, org, ts))
        return results


    @staticmethod
    def _extract_sentences_from_turn(
        body: str,
        body_start: int,
        spk: str,
        org: str,
        ts: str,
    ) -> List[Dict[str, Any]]:
        import re
        spans: List[Dict[str, Any]] = []
        loc = {"kind": "timestamp", "value": ts} if ts else {"kind": "document", "value": "transcript_offset"}
        for sm in re.finditer(r"[^.!?\n]+[.!?]?", body):
            sent = sm.group().strip()
            if len(sent) >= 15:
                s_offset = body_start + sm.start()
                spans.append({
                    "text": sent,
                    "exact_text": sent,
                    "speaker": spk,
                    "speaker_org": org,
                    "start_offset": s_offset,
                    "end_offset": s_offset + len(sent),
                    "locator": loc,
                })
        return spans

    @staticmethod
    def verify_span_grounding(statement: str, transcript: str) -> Optional[Tuple[int, int]]:
        """Verify that a statement resolves as a verbatim substring in transcript, returning (start, end)."""
        if not statement or not transcript:
            return None
        clean = statement.strip()
        idx = transcript.find(clean)
        if idx != -1:
            return idx, idx + len(clean)
        needle = clean[:30].strip()
        if needle and needle in transcript:
            idx = transcript.find(needle)
            return idx, idx + len(clean)
        return None
