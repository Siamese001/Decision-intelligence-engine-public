"""L1 — Forensic Cognition & Attribution (Plan + Execute)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.model_registry import ModelTier, get_binding

from .constants import _GENERIC_SPEAKER_PATTERN
from .models import HITLRequiredError, L2SynthesisProvider  # noqa: F401

logger = logging.getLogger(__name__)


class L1PlanContract:
    """Immutable plan produced by L1 planning pass driving routing and C0 decisions."""
    __slots__ = (
        "source_type", "diarization_status", "roster_gate_needed", "meeting_type_hint",
        "fusion_dag", "semantic_enhancers", "model_binding", "lexical_quality_score",
        "acoustic_overlap_ratio", "classification_tier", "sanitization_passed", "quarantine_reasons",
    )

    def __init__(
        self,
        source_type: str,
        diarization_status: str,
        roster_gate_needed: bool,
        meeting_type_hint: Optional[str] = None,
        fusion_dag: Optional[str] = None,
        semantic_enhancers: Optional[List[str]] = None,
        model_binding: Optional[Dict[str, Any]] = None,
        lexical_quality_score: float = 1.0,
        acoustic_overlap_ratio: float = 0.0,
        classification_tier: str = "internal",
        sanitization_passed: bool = True,
        quarantine_reasons: Optional[List[str]] = None,
    ):
        self.source_type = source_type
        self.diarization_status = diarization_status
        self.roster_gate_needed = roster_gate_needed
        self.meeting_type_hint = meeting_type_hint
        self.fusion_dag = fusion_dag
        self.semantic_enhancers = semantic_enhancers or []
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.model_binding = model_binding or {
            "model_id": binding.model_id,
            "reasoning_effort": binding.reasoning_effort or "high",
            "provider": "openai",
        }
        self.lexical_quality_score = float(lexical_quality_score)
        self.acoustic_overlap_ratio = float(acoustic_overlap_ratio)
        self.classification_tier = classification_tier
        self.sanitization_passed = sanitization_passed
        self.quarantine_reasons = quarantine_reasons or []

    def model_dump(self) -> Dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}

    def __repr__(self) -> str:
        return (
            f"L1PlanContract(source_type={self.source_type!r}, "
            f"diarization_status={self.diarization_status!r}, "
            f"roster_gate_needed={self.roster_gate_needed!r}, "
            f"meeting_type_hint={self.meeting_type_hint!r})"
        )


class L1ForensicCognition:
    """
    L1 has two phases:
      plan()    — emit L1PlanContract (pure; no side effects)
      execute() — apply speaker mapping, produce HITL-resolved transcript (Artifact 01)

    The pipeline calls plan() first (to drive routing), then execute().
    For UNDIARIZED sources, execute() is called AFTER the C0 roster gate.
    Standardized on gpt-5.6-luna (high reasoning effort).
    """

    _RESTRICTED_PATTERNS = [
        re.compile(r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----"),
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
        re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36})\b"),
    ]
    _CONFIDENTIAL_PATTERNS = [
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN pattern
    ]

    def __init__(self, default_model: Optional[str] = None, reasoning_effort: Optional[str] = None) -> None:
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.default_model = default_model or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "xhigh"
        self.model_binding = {
            "model_id": self.default_model,
            "provider": "openai",
            "reasoning_effort": self.reasoning_effort,
        }

    @staticmethod
    def _resolve_source_type(metadata: Dict[str, Any], diarization_status: str, transcript: str) -> str:
        override = metadata.get("source_type", "").upper()
        if override in ("AUDIO_ONLY_UNDIARIZED", "AUDIO_TRANSCRIBED", "DIARIZED_TEXT", "UNDIARIZED_TEXT", "LIVE_AUDIO", "AUDIO_DIARIZED"):
            return override
        if metadata.get("_from_audio_transcription"):
            generic_found = bool(_GENERIC_SPEAKER_PATTERN.findall(transcript))
            return "AUDIO_ONLY_UNDIARIZED" if (diarization_status == "UNDIARIZED" or generic_found) else "AUDIO_TRANSCRIBED"
        if diarization_status in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION", "DIARIZED_ACOUSTIC"):
            return "DIARIZED_TEXT"
        return "UNDIARIZED_TEXT"

    @staticmethod
    def _check_roster_gate(metadata: Dict[str, Any], diarization_status: str, source_type: str) -> bool:
        roster = metadata.get("roster") or metadata.get("attendees") or {}
        is_undiarized = (diarization_status == "UNDIARIZED" or source_type == "AUDIO_ONLY_UNDIARIZED")
        return bool(is_undiarized and not roster)

    @staticmethod
    def _select_anchored_dag(anchor: str, metadata: Dict[str, Any], enhancers: List[str]) -> Optional[str]:
        anchor_lower = anchor.lower()
        is_live = (
            metadata.get("source_type") == "LIVE_AUDIO"
            or any(ext in anchor_lower for ext in (".m3u8", "rtmp://", "rtsp://"))
            or "live_stream" in anchor_lower
            or metadata.get("is_live", False)
        )
        if is_live:
            return "FUSION_D"
        if any(anchor_lower.endswith(ext) for ext in (".mp4", ".m4a", ".mp3", ".wav")):
            has_docs = any(e.lower().endswith((".pdf", ".pptx", ".docx")) for e in enhancers)
            return "FUSION_C" if (enhancers and has_docs) else "FUSION_A"
        if "vtt" in anchor_lower and enhancers:
            return "FUSION_B"
        return None

    @staticmethod
    def _select_unanchored_dag(metadata: Dict[str, Any], source_type: str, enhancers: List[str]) -> Tuple[Optional[str], List[str]]:
        if metadata.get("source_type") == "LIVE_AUDIO" or metadata.get("is_live", False):
            return "FUSION_D", enhancers
        if enhancers:
            vtt_list = [e for e in enhancers if e.lower().endswith(".vtt")]
            if vtt_list:
                rem = [e for e in enhancers if e != vtt_list[0]]
                return "FUSION_B", rem
            if any(e.lower().endswith((".pdf", ".pptx", ".docx")) for e in enhancers):
                return "FUSION_C", enhancers
        if source_type in ("AUDIO_TRANSCRIBED", "UNDIARIZED_TEXT", "DIARIZED_TEXT"):
            return "FUSION_A", enhancers
        return None, enhancers

    @classmethod
    def _select_fusion_dag(cls, metadata: Dict[str, Any], source_type: str) -> Tuple[Optional[str], List[str]]:
        fusion_coord = metadata.get("extensions", {}).get("com.nee.fusion_coordinator", {})
        enhancers = list(fusion_coord.get("semantic_enhancers", []))
        anchor = fusion_coord.get("temporal_anchor")
        if anchor:
            return cls._select_anchored_dag(anchor, metadata, enhancers), enhancers
        return cls._select_unanchored_dag(metadata, source_type, enhancers)

    @staticmethod
    def _score_lexical_and_acoustic(metadata: Dict[str, Any], transcript: str) -> Tuple[float, float]:
        overlap_ratio = float(metadata.get("speech_overlap_fraction", 0.0))
        stripped = transcript.strip()
        if not stripped:
            return 0.1, overlap_ratio
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        lexical = 0.5 if (len(lines) < 3 and len(stripped) < 80) else 1.0
        matches = len(re.findall(r"\[overlap|interruption|simultaneous\]", stripped, re.IGNORECASE))
        if matches > 0:
            overlap_ratio = min(1.0, overlap_ratio + (matches * 0.05))
        return lexical, overlap_ratio

    def _classify_and_sanitize(self, metadata: Dict[str, Any], transcript: str) -> Tuple[str, bool, List[str]]:
        tier = metadata.get("classification", "internal")
        passed = True
        reasons: List[str] = []
        clean_meta = {k: v for k, v in metadata.items() if not callable(v)}
        payload = f"{transcript}\n{json.dumps(clean_meta, default=str)}"

        for pat in self._RESTRICTED_PATTERNS:
            if pat.search(payload) and not metadata.get("allow_restricted_override", False):
                return "restricted", False, ["RESTRICTED_CREDENTIAL_OR_KEY_DETECTED"]

        if tier != "restricted":
            for pat in self._CONFIDENTIAL_PATTERNS:
                if pat.search(payload):
                    tier = "confidential"
                    break
        return tier, passed, reasons

    @staticmethod
    def _resolve_model_binding(metadata: Dict[str, Any]) -> Dict[str, Any]:
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        return {
            "model_id": metadata.get("model_id") or binding.model_id,
            "reasoning_effort": metadata.get("reasoning_effort") or binding.reasoning_effort or "high",
            "provider": "openai",
        }

    def plan(self, u0_context: Dict[str, Any]) -> L1PlanContract:
        """Emit routing plan without side effects."""
        metadata = u0_context.get("metadata", {})
        diarization_status = u0_context.get("diarization_status", "UNDIARIZED")
        transcript = u0_context.get("clean_transcript", "")

        source_type = self._resolve_source_type(metadata, diarization_status, transcript)
        roster_gate_needed = self._check_roster_gate(metadata, diarization_status, source_type)
        meeting_type_hint = metadata.get("meeting_type")
        fusion_dag, semantic_enhancers = self._select_fusion_dag(metadata, source_type)
        lexical_quality, overlap_ratio = self._score_lexical_and_acoustic(metadata, transcript)
        tier, passed, reasons = self._classify_and_sanitize(metadata, transcript)
        model_binding = self._resolve_model_binding(metadata)

        plan = L1PlanContract(
            source_type=source_type,
            diarization_status=diarization_status,
            roster_gate_needed=roster_gate_needed,
            meeting_type_hint=meeting_type_hint,
            fusion_dag=fusion_dag,
            semantic_enhancers=semantic_enhancers,
            model_binding=model_binding,
            lexical_quality_score=lexical_quality,
            acoustic_overlap_ratio=overlap_ratio,
            classification_tier=tier,
            sanitization_passed=passed,
            quarantine_reasons=reasons,
        )
        logger.info(f"L1 plan: {plan}")
        return plan

    def execute(
        self,
        u0_context: Dict[str, Any],
        plan: L1PlanContract,
        provider: L2SynthesisProvider,
        remediation_directive: Optional[str] = None,
    ) -> str:
        """
        Stage 01 execution: delegates to decoupled ForensicNormalizer agent.
        """
        from .forensic_normalizer import ForensicNormalizer

        return ForensicNormalizer().execute(
            u0_context=u0_context,
            plan=plan,
            provider=provider,
            remediation_directive=remediation_directive,
        )
