"""dual_pipeline.grounding

Deterministic Grounding and Mandatory GraphDB Claim Sourcing subsystem.
Enforces:
1. Complete structured subject/predicate/object claim contracts.
2. Transactionally consistent, read-only SQLite snapshot access.
3. Strict assertion eligibility, rejecting candidate, retracted, or superseded records.
4. Typed source locators preserving evidence provenance.
"""
from __future__ import annotations

from .contracts import (
    ClaimManifest,
    ClaimRecord,
    DeliveryReceipt,
    SourceLocator,
)
from .delivery_boundary import (
    DeliveryResult,
    GroundedDeliveryGuard,
    PostVerificationTamperingError,
    SafeFailureResponse,
    SUPPORTED_DELIVERY_SURFACES,
    UnsupportedDeliverySurfaceError,
    verify_grounded_delivery,
)
from .eligibility import (
    AssertionEligibilityAuditor,
    EligibilityDecision,
    EligibilityResult,
)
from .metrics import (
    MetricEvaluationResult,
    count_words,
    evaluate_text_metrics,
    normalize_text,
)
from .request_gaps import (
    GapClassification,
    RequestGapAuditor,
    RequestGapItem,
    RequestGapMatrix,
)
from .snapshot import (
    ReadOnlyGraphSnapshot,
    verify_snapshot_generation,
)

__all__ = [
    "ClaimManifest",
    "ClaimRecord",
    "DeliveryReceipt",
    "SourceLocator",
    "DeliveryResult",
    "GroundedDeliveryGuard",
    "PostVerificationTamperingError",
    "SafeFailureResponse",
    "SUPPORTED_DELIVERY_SURFACES",
    "UnsupportedDeliverySurfaceError",
    "verify_grounded_delivery",
    "AssertionEligibilityAuditor",
    "EligibilityDecision",
    "EligibilityResult",
    "MetricEvaluationResult",
    "count_words",
    "evaluate_text_metrics",
    "normalize_text",
    "GapClassification",
    "RequestGapAuditor",
    "RequestGapItem",
    "RequestGapMatrix",
    "ReadOnlyGraphSnapshot",
    "verify_snapshot_generation",
]
