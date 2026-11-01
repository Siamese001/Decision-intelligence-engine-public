"""Core package for transcript stages in the dual pipeline."""

from __future__ import annotations

from .anti_conflation import (
    ERR_COMPOUND_WORKSTREAM_CONFLATION,
    CompoundWorkstreamConflationError,
    decompose_multi_workstream_utterance,
    is_compound_workstream_reference,
    validate_anti_conflation,
)
from .attendee_gate import (
    extract_candidate_attendees,
    is_audio_or_raw_source,
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from .c0_gates import C0ConformanceGate, C0ContextEnvelopeGate, C0RosterContextGate
from .c05_title_gate import C05TitleCompletenessGate, apply_hitl_role_resolution
from .c06_graphdb_role_gate import C06GraphDBRoleVerificationGate
from .constants import (
    _BASE_DIR,
    _GENERIC_SPEAKER_PATTERN,
    _NATIVE_DIARIZATION_PATTERN,
    _PASSIVE_VERBS,
    _STAGE_PATHS,
    _STAGES_DIR,
    AGENTIC_EXTRACTION_PROMPT,
    AGENTIC_LEVERAGE_NAMESPACE,
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    _load_stage_prompt,
)
from .hitl_guard import assert_no_u0_hitl_bypass
from .l0_routing import L0DeterministicRouter
from .l1_cognition import L1ForensicCognition, L1PlanContract
from .l2_executors import (
    L2ExecutiveRecapExecutor,
    L2ForensicNormalizerExecutor,
    L2GraphExtractorExecutor,
    L2SynthesisExecutor,
)
from .models import (
    AgenticLeverageNode,
    BipartiteSignalProjection,
    HITLRequiredError,
    L2SynthesisProvider,
    RestrictedAdvisoryProjection,
)
from .pa_packet import PAPacketAssembler
from .synthesizers import LLMTranscriptSynthesizer, OfflineTranscriptSynthesizer
from .u0_ingestion import U0IngestionBoundary
from .x_gates import XExitGatesEvaluator

__all__ = [
    "extract_candidate_attendees",
    "is_audio_or_raw_source",
    "record_attendee_confirmation",
    "verify_mandatory_attendee_confirmation",
    "assert_no_u0_hitl_bypass",
    "AGENTIC_EXTRACTION_PROMPT",
    "AGENTIC_LEVERAGE_NAMESPACE",
    "AgenticLeverageNode",
    "apply_hitl_role_resolution",
    "BipartiteSignalProjection",
    "C05TitleCompletenessGate",
    "C06GraphDBRoleVerificationGate",
    "C0ConformanceGate",
    "C0ContextEnvelopeGate",
    "C0RosterContextGate",
    "CLIENT_SAFE_NAMESPACE",
    "CompoundWorkstreamConflationError",
    "decompose_multi_workstream_utterance",
    "ERR_COMPOUND_WORKSTREAM_CONFLATION",
    "is_compound_workstream_reference",
    "validate_anti_conflation",
    "HITLRequiredError",
    "INTERNAL_ADVISORY_NAMESPACE",
    "L0DeterministicRouter",
    "L1ForensicCognition",
    "L1PlanContract",
    "L2ExecutiveRecapExecutor",
    "L2ForensicNormalizerExecutor",
    "L2GraphExtractorExecutor",
    "L2SynthesisExecutor",
    "L2SynthesisProvider",
    "LLMTranscriptSynthesizer",
    "OfflineTranscriptSynthesizer",
    "PAPacketAssembler",
    "RestrictedAdvisoryProjection",
    "U0IngestionBoundary",
    "XExitGatesEvaluator",
    "_BASE_DIR",
    "_GENERIC_SPEAKER_PATTERN",
    "_NATIVE_DIARIZATION_PATTERN",
    "_PASSIVE_VERBS",
    "_STAGES_DIR",
    "_STAGE_PATHS",
    "_load_stage_prompt",
]
