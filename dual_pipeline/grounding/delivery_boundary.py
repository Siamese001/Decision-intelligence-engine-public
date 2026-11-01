"""dual_pipeline.grounding.delivery_boundary

Layer 2 delivery boundary guard, surface inventory, and safe failure response contract.
Enforces:
1. Complete delivery surface inventory with explicit rejection of unintegrated hosts.
2. Mandatory pre-release verification: zero payload releases on missing, failed, or tampered receipts.
3. Post-verification immutability: prevents secondary formatters or signatures from mutating content after certification.
4. Safe failure response contract: suppresses ungrounded draft text and restricted evidence while providing clear rejection causes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .contracts import (
    ClaimManifest,
    DeliveryReceipt,
)
from .eligibility import (
    AssertionEligibilityAuditor,
    EligibilityDecision,
)
from .metrics import evaluate_text_metrics  # noqa: lazy-decouple
from .snapshot import ReadOnlyGraphSnapshot

SUPPORTED_DELIVERY_SURFACES = frozenset({
    "EMAIL_BODY",
    "TEAMS_MESSAGE",
    "SLACK_MESSAGE",
    "REPORT_ARTIFACT",
    "EXPORT_FILE",
    "EXECUTIVE_AGENDA",
    "TALKING_POINTS",
    "QUERY_SYNTHESIS",
})


class UnsupportedDeliverySurfaceError(ValueError):
    """Raised when an unintegrated external host or unsupported delivery surface is requested."""
    pass


class PostVerificationTamperingError(RuntimeError):
    """Raised when payload content changes between verification and sink release."""
    pass


@dataclass(frozen=True)
class SafeFailureResponse:
    """Sanitized failure response explaining unavailable evidence without leaking drafts or secrets."""
    delivery_surface: str
    reason_code: str
    public_message: str
    rejection_reasons: List[str]
    receipt_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    draft_suppressed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivery_surface": self.delivery_surface,
            "reason_code": self.reason_code,
            "public_message": self.public_message,
            "rejection_reasons": list(self.rejection_reasons),
            "receipt_id": self.receipt_id,
            "timestamp": self.timestamp,
            "draft_suppressed": self.draft_suppressed,
        }


@dataclass
class DeliveryResult:
    """Outcome of governed delivery attempt through GroundedDeliveryGuard."""
    delivered: bool
    surface: str
    receipt: DeliveryReceipt
    safe_response: Optional[SafeFailureResponse] = None
    sink_response: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivered": self.delivered,
            "surface": self.surface,
            "receipt": self.receipt.to_dict(),
            "safe_response": self.safe_response.to_dict() if self.safe_response else None,
            "sink_response": str(self.sink_response) if self.sink_response is not None else None,
        }


def _check_payload_digest(payload_text: str, manifest: ClaimManifest) -> Tuple[str, Optional[str]]:
    payload_sha256 = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    if payload_sha256 != manifest.payload_digest:
        return payload_sha256, f"Payload SHA-256 mismatch: calculated {payload_sha256} != manifest {manifest.payload_digest}"
    return payload_sha256, None


def _check_generation_parity(snapshot: ReadOnlyGraphSnapshot, manifest: ClaimManifest) -> List[str]:
    reasons: List[str] = []
    if snapshot.generation_id != manifest.graph_generation_id:
        reasons.append(f"Generation mismatch: snapshot {snapshot.generation_id} != manifest {manifest.graph_generation_id}")
    if snapshot.recorded_sha256 != manifest.graph_sha256:
        reasons.append(f"Graph SHA mismatch: snapshot {snapshot.recorded_sha256} != manifest {manifest.graph_sha256}")
    return reasons


def _map_decision_to_reason(decision: str) -> str:
    if decision == EligibilityDecision.REJECTED_CONTRADICTION:
        return "UNRESOLVED_CONTRADICTION"
    if decision == EligibilityDecision.REJECTED_AUDIENCE:
        return "INACCESSIBLE_AUDIENCE"
    if decision == EligibilityDecision.REJECTED_STATUS:
        return "SUPERSEDED_EVIDENCE"
    return "UNSUPPORTED_CLAIM"


_REASON_SEVERITY: Dict[str, int] = {
    "INACCESSIBLE_AUDIENCE": 50,
    "UNRESOLVED_CONTRADICTION": 40,
    "SUPERSEDED_EVIDENCE": 30,
    "UNSUPPORTED_CLAIM": 20,
    "GROUNDED_SUPPORTED": 0,
}


def _is_off_topic_claim(claim: Any, prompt_tokens: set[str]) -> bool:
    import re
    if not prompt_tokens:
        return False
    subj_tokens = {t.lower() for t in re.findall(r"\b[A-Za-z0-9_]{3,}\b", claim.subject or "")}
    topic_tokens = prompt_tokens - subj_tokens
    claim_tokens = {
        t.lower()
        for t in re.findall(r"\b[A-Za-z0-9_]{3,}\b", f"{claim.predicate or ''} {claim.object or ''}")
    } - subj_tokens
    if not topic_tokens or not claim_tokens or len(topic_tokens) < 2:
        return False
    if topic_tokens & claim_tokens:
        return False
    return not any(
        (len(pt) >= 4 and len(ct) >= 4 and (pt[:4] == ct[:4] or pt in ct or ct in pt))
        for pt in topic_tokens
        for ct in claim_tokens
    )


def _audit_all_claims(
    manifest: ClaimManifest,
    snapshot: ReadOnlyGraphSnapshot,
    audience: str,
    prompt_text: Optional[str] = None,
) -> Tuple[int, int, str, List[str]]:
    import re
    auditor = AssertionEligibilityAuditor()
    supported = 0
    contradicted = 0
    code = "GROUNDED_SUPPORTED"
    reasons: List[str] = []

    prompt_tokens = {
        t.lower()
        for t in re.findall(r"\b[A-Za-z0-9_]{3,}\b", prompt_text or "")
        if t.lower() not in {"the", "and", "for", "with", "what", "who", "how", "when", "where", "why", "are", "was", "from", "that", "this"}
    }

    for claim in manifest.claims:
        res = auditor.audit_claim(claim, snapshot, audience=audience)
        if not res.is_eligible:
            reasons.extend(res.rejection_reasons)
            if res.decision == EligibilityDecision.REJECTED_CONTRADICTION:
                contradicted += 1
            new_code = _map_decision_to_reason(res.decision)
            if _REASON_SEVERITY.get(new_code, 10) >= _REASON_SEVERITY.get(code, 0):
                code = new_code
            continue

        if _is_off_topic_claim(claim, prompt_tokens):
            reasons.append(
                f"INELIGIBLE_ASSERTION_LEAK: Off-topic claim '{claim.claim_id}' shares zero non-entity topic tokens with prompt."
            )
            if _REASON_SEVERITY.get("UNSUPPORTED_CLAIM", 20) >= _REASON_SEVERITY.get(code, 0):
                code = "UNSUPPORTED_CLAIM"
            continue

        supported += 1

    return supported, contradicted, code, reasons


def _resolve_terminal_reason(
    passed_count: bool,
    has_unsupported_titles: bool,
    claim_code: str,
    has_digest_err: bool = False,
    has_gen_err: bool = False,
) -> str:
    if not passed_count:
        return "WORD_COUNT_EXCEEDED"
    if has_gen_err:
        return "GENERATION_DRIFT"
    if claim_code in (
        "ZERO_GRAPH_TRAVERSAL",
        "PROMPT_ECHO_CONTAMINATION",
        "CROSS_SPEAKER_PROMPT_ECHO",
        "UNANCHORED_PLACEHOLDER_CLAIM",
        "UNBACKED_PAYLOAD_SPAN",
    ):
        return claim_code
    if has_unsupported_titles:
        return "AMBIGUOUS_IDENTITY"
    if has_digest_err and claim_code == "GROUNDED_SUPPORTED":
        return "UNBACKED_PAYLOAD_SPAN"
    return claim_code if claim_code != "GROUNDED_SUPPORTED" else "UNSUPPORTED_CLAIM"


def _process_metric_and_drift_reasons(
    metrics_reasons: List[str],
    snapshot: ReadOnlyGraphSnapshot,
    claim_code: str,
    reasons: List[str],
) -> str:
    """Classify metric reasons and detect mid-flight snapshot generation drift."""
    for r in metrics_reasons:
        if "CROSS_SPEAKER_PROMPT_ECHO" in r:
            claim_code = "PROMPT_ECHO_CONTAMINATION"
            reasons.append(r)
        elif "UNANCHORED_PLACEHOLDER_CLAIM" in r:
            claim_code = "UNANCHORED_PLACEHOLDER_CLAIM"
            reasons.append(r)
        elif "UNBACKED_PAYLOAD_SPAN" in r:
            claim_code = "UNBACKED_PAYLOAD_SPAN"
            reasons.append(r)
    if not snapshot.is_generation_current():
        reasons.append("Mid-flight generation drift detected: graph mutated during verification.")
        claim_code = "GENERATION_DRIFT"
    return claim_code


def _resolve_effective_delivery_max_words(
    delivery_surface: str, max_words: Optional[int], prompt_text: Optional[str]
) -> Optional[int]:
    from dual_pipeline.parameters import get_grounding_delivery_max_words
    from .metrics import extract_prompt_word_ceiling
    if delivery_surface == "QUERY_SYNTHESIS" and max_words is None and not (prompt_text and extract_prompt_word_ceiling(prompt_text, None)):
        return None
    return get_grounding_delivery_max_words() if max_words is None else max_words


def _build_blocked_snapshot_receipt(
    manifest: ClaimManifest, payload_sha256: str, runtime_root: Path, delivery_surface: str, audience: str
) -> DeliveryReceipt:
    return DeliveryReceipt(
        receipt_id=f"RCP-{manifest.manifest_id}", manifest_id=manifest.manifest_id,
        manifest_digest=manifest.to_dict()["canonical_digest"]["value"], payload_digest=payload_sha256,
        runtime_root=str(runtime_root), graph_generation_id=manifest.graph_generation_id,
        graph_sha256=manifest.graph_sha256, status="BLOCKED", reason_code="UNSUPPORTED_CLAIM",
        total_claims_count=len(manifest.claims), supported_claims_count=0, contradicted_claims_count=0,
        delivery_surface=delivery_surface, audience=audience, word_count=0,
    )


def verify_grounded_delivery(
    payload_text: str, manifest: ClaimManifest, runtime_root: Path, max_words: Optional[int] = None,
    audience: str = "CLIENT_SAFE", delivery_surface: str = "EMAIL_BODY",
    prompt_text: Optional[str] = None, enforce_claim_coverage: bool = False,
) -> Tuple[bool, DeliveryReceipt, List[str]]:
    """Verify text payload and claim manifest against GraphDB snapshot, returning DeliveryReceipt."""
    if delivery_surface not in SUPPORTED_DELIVERY_SURFACES:
        raise UnsupportedDeliverySurfaceError(
            f"Delivery surface '{delivery_surface}' is not registered in governed inventory: {sorted(SUPPORTED_DELIVERY_SURFACES)}"
        )

    eff_max_words = _resolve_effective_delivery_max_words(delivery_surface, max_words, prompt_text)
    reasons: List[str] = []

    payload_sha256, digest_err = _check_payload_digest(payload_text, manifest)
    if digest_err:
        reasons.append(digest_err)

    try:
        snapshot = ReadOnlyGraphSnapshot(runtime_root, enforce_pair_integrity=True)
    except Exception as exc:
        reasons.append(f"Failed to open GraphDB snapshot at {runtime_root}: {exc}")
        receipt = _build_blocked_snapshot_receipt(manifest, payload_sha256, runtime_root, delivery_surface, audience)
        return False, receipt, reasons
        return False, receipt, reasons

    gen_reasons = _check_generation_parity(snapshot, manifest)
    reasons.extend(gen_reasons)
    metrics = evaluate_text_metrics(
        payload_text, manifest, snapshot, eff_max_words,
        prompt_text=prompt_text, enforce_claim_coverage=enforce_claim_coverage,
    )
    if not metrics.passed_count:
        reasons.extend(metrics.reasons)
    if metrics.unsupported_titles:
        reasons.extend([f"Invented persona title detected: {t}" for t in metrics.unsupported_titles])

    supported_cnt, contra_cnt, claim_code, claim_reasons = _audit_all_claims(
        manifest, snapshot, audience, prompt_text=prompt_text
    )
    reasons.extend(claim_reasons)
    if len(manifest.claims) == 0:
        reasons.append("EMPTY_CLAIM_MANIFEST: Grounded delivery requires >= 1 verified claim; empty manifest (zero graph traversal) is prohibited.")
        claim_code = "ZERO_GRAPH_TRAVERSAL"

    claim_code = _process_metric_and_drift_reasons(metrics.reasons, snapshot, claim_code, reasons)
    deduped_reasons = list(dict.fromkeys(reasons))

    is_certified = (len(deduped_reasons) == 0) and (supported_cnt == len(manifest.claims)) and (contra_cnt == 0) and (len(manifest.claims) > 0)
    status = "CERTIFIED" if is_certified else "REJECTED"
    reason_code = "GROUNDED_SUPPORTED" if is_certified else _resolve_terminal_reason(
        metrics.passed_count, bool(metrics.unsupported_titles), claim_code,
        has_digest_err=bool(digest_err), has_gen_err=bool(gen_reasons),
    )

    receipt = DeliveryReceipt(
        receipt_id=f"RCP-{manifest.manifest_id}", manifest_id=manifest.manifest_id,
        manifest_digest=manifest.to_dict()["canonical_digest"]["value"], payload_digest=payload_sha256,
        runtime_root=str(runtime_root), graph_generation_id=snapshot.generation_id,
        graph_sha256=snapshot.recorded_sha256 or manifest.graph_sha256, status=status, reason_code=reason_code,
        total_claims_count=len(manifest.claims), supported_claims_count=supported_cnt, contradicted_claims_count=contra_cnt,
        delivery_surface=delivery_surface, audience=audience, word_count=metrics.word_count,
    )
    receipt.validate()
    return is_certified, receipt, deduped_reasons


class GroundedDeliveryGuard:
    """Enforces fail-closed delivery boundary across all repository output surfaces."""

    def __init__(self, supported_surfaces: Optional[frozenset[str]] = None) -> None:
        self.supported_surfaces = supported_surfaces or SUPPORTED_DELIVERY_SURFACES

    def deliver(
        self,
        payload_text: str,
        manifest: ClaimManifest,
        runtime_root: Path,
        surface: str,
        sink: Callable[[str], Any],
        max_words: Optional[int] = None,
        audience: str = "CLIENT_SAFE",
        prompt_text: Optional[str] = None,
        enforce_claim_coverage: bool = False,
    ) -> DeliveryResult:
        """Verify content immediately before dispatching to destination sink."""
        # 1. Surface validation
        if surface not in self.supported_surfaces:
            raise UnsupportedDeliverySurfaceError(
                f"Delivery surface '{surface}' is not registered in governed inventory: {sorted(self.supported_surfaces)}"
            )

        # 2. Run verification
        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=payload_text,
            manifest=manifest,
            runtime_root=runtime_root,
            max_words=max_words,
            audience=audience,
            delivery_surface=surface,
            prompt_text=prompt_text,
            enforce_claim_coverage=enforce_claim_coverage,
        )

        # 3. If not certified, block release immediately and return safe response
        if not certified or receipt.status != "CERTIFIED":
            safe_resp = SafeFailureResponse(
                delivery_surface=surface,
                reason_code=receipt.reason_code,
                public_message=f"Delivery blocked on surface '{surface}': evidence qualification failed.",
                rejection_reasons=reasons,
                receipt_id=receipt.receipt_id,
            )
            return DeliveryResult(
                delivered=False,
                surface=surface,
                receipt=receipt,
                safe_response=safe_resp,
                sink_response=None,
            )

        # 4. Anti-tampering check: verify payload string has not mutated
        exact_sha = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        if exact_sha != receipt.payload_digest:
            raise PostVerificationTamperingError(
                f"Post-verification modification detected: sha {exact_sha} != receipt {receipt.payload_digest}"
            )

        # 5. Dispatch to verified destination sink
        try:
            sink_res = sink(payload_text)
            return DeliveryResult(
                delivered=True,
                surface=surface,
                receipt=receipt,
                safe_response=None,
                sink_response=sink_res,
            )
        except Exception as exc:
            # Sink execution failed: return safe failure response
            safe_resp = SafeFailureResponse(
                delivery_surface=surface,
                reason_code="SINK_DISPATCH_FAILURE",
                public_message=f"Destination sink on surface '{surface}' failed to dispatch.",
                rejection_reasons=[str(exc)],
                receipt_id=receipt.receipt_id,
            )
            return DeliveryResult(
                delivered=False,
                surface=surface,
                receipt=receipt,
                safe_response=safe_resp,
                sink_response=None,
            )
