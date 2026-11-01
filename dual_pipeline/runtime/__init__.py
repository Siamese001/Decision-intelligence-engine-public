"""
Local Model Runtime, Memory Governance, and Host Admission for Apple Silicon Metal.
Provides unified memory tracking, single-flight model loading, offline inference,
and priority-governed host admission.
"""
from __future__ import annotations

from .host_admission import (
    AdmissionError,
    AdmissionPriority,
    AdmissionQueueFullError,
    AdmissionRejectedError,
    AdmissionRequest,
    AdmissionTimeoutError,
    HostAdmissionController,
    get_host_admission_controller,
)
from .memory_governor import (
    AllocationRecord,
    MemoryGovernor,
    MemoryGovernorError,
    MemoryLimitExceededError,
    ReclamationBlockedError,
    get_memory_governor,
    is_worker_alive,
)
from .model_runtime import (
    LocalModelRuntime,
    ModelInferenceResult,
    get_model_runtime,
)
from .pipeline_context import (
    EvalsPayload,
    L1CognitionPayload,
    L2SynthesisPayload,
    PipelineInvocationContext,
    StageCheckpoint,
    U0IngressPayload,
    XExitGatesPayload,
)

__all__ = [
    "AdmissionError",
    "AdmissionPriority",
    "AdmissionQueueFullError",
    "AdmissionRejectedError",
    "AdmissionRequest",
    "AdmissionTimeoutError",
    "AllocationRecord",
    "EvalsPayload",
    "HostAdmissionController",
    "L1CognitionPayload",
    "L2SynthesisPayload",
    "LocalModelRuntime",
    "MemoryGovernor",
    "MemoryGovernorError",
    "MemoryLimitExceededError",
    "ModelInferenceResult",
    "PipelineInvocationContext",
    "ReclamationBlockedError",
    "StageCheckpoint",
    "U0IngressPayload",
    "XExitGatesPayload",
    "get_host_admission_controller",
    "get_memory_governor",
    "get_model_runtime",
    "is_worker_alive",
]
