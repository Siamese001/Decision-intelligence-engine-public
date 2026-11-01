"""Delivery surface adapter and output reduction telemetry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .counter import count_characters, count_words, normalize_text
from .models import Observation, ValidationResult
from .verifier import validate_engineering_response

# RCA frozen measurement baseline (from docs/plans/docs_concise_runtime_output_plan_20261007.md)
BASELINE_AUTHORED_WORDS = 1278
BASELINE_AUTHORED_CHARS = 14373


class DeliverySurface(str, Enum):
    """Classification of delivery surfaces across repo and host."""

    SAVED_SUMMARY = "saved_summary"
    REPOSITORY_RESPONSE = "repository_response"
    IDE_ANSWER = "ide_answer"
    HOST_ACTIVITY_CARDS = "host_activity_cards"


class EnforcementAuthority(str, Enum):
    """Enforcement capability tier for a surface."""

    MACHINE_ENFORCED = "machine_enforced"
    INSTRUCTION_ONLY = "instruction_only"


@dataclass
class DeliveryResult:
    """Outcome of attempting to deliver an engineering response."""

    surface: DeliverySurface
    enforcement_authority: EnforcementAuthority
    is_delivered: bool
    delivered_text: Optional[str] = None
    content_digest: Optional[str] = None
    validation_result: Optional[ValidationResult] = None
    errors: list[str] = field(default_factory=list)
    rejection_reason: Optional[str] = None
    host_interception_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert delivery result to dictionary."""
        return {
            "surface": self.surface.value,
            "enforcement_authority": self.enforcement_authority.value,
            "is_delivered": self.is_delivered,
            "delivered_text": self.delivered_text,
            "content_digest": self.content_digest,
            "validation_result": self.validation_result.to_dict() if self.validation_result else None,
            "errors": list(self.errors),
            "rejection_reason": self.rejection_reason,
            "host_interception_supported": self.host_interception_supported,
        }


def compute_content_digest(text: str) -> str:
    """Compute sha256 hex digest of normalized text."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deliver_output(
    surface: DeliverySurface,
    text: str,
    observation: Optional[Observation] = None,
    plan_only: bool = False,
    verified_digest: Optional[str] = None,
) -> DeliveryResult:
    """Validate and deliver an engineering response to the requested surface.

    Fails closed if text changed after validation, if checks are missing,
    or if text violates concise engineering constraints.
    """
    current_digest = compute_content_digest(text)

    # 1. Post-validation mutation check
    if verified_digest is not None and verified_digest != current_digest:
        reason = (
            "Post-validation mutation detected: Checked digest does not match delivered text digest. "
            "Unchecked post-validation additions are barred."
        )
        return DeliveryResult(
            surface=surface,
            enforcement_authority=(
                EnforcementAuthority.MACHINE_ENFORCED
                if surface in (DeliverySurface.SAVED_SUMMARY, DeliverySurface.REPOSITORY_RESPONSE)
                else EnforcementAuthority.INSTRUCTION_ONLY
            ),
            is_delivered=False,
            delivered_text=None,
            content_digest=current_digest,
            errors=[reason],
            rejection_reason=reason,
            host_interception_supported=(
                surface in (DeliverySurface.SAVED_SUMMARY, DeliverySurface.REPOSITORY_RESPONSE)
            ),
        )

    # 2. Validate content against engineering output rules
    val_res = validate_engineering_response(
        response_text=text,
        observation=observation,
        plan_only=plan_only,
    )

    if not val_res.is_valid:
        reason = f"Delivery rejected by policy: {'; '.join(val_res.errors)}"
        return DeliveryResult(
            surface=surface,
            enforcement_authority=(
                EnforcementAuthority.MACHINE_ENFORCED
                if surface in (DeliverySurface.SAVED_SUMMARY, DeliverySurface.REPOSITORY_RESPONSE)
                else EnforcementAuthority.INSTRUCTION_ONLY
            ),
            is_delivered=False,
            delivered_text=None,
            content_digest=current_digest,
            validation_result=val_res,
            errors=val_res.errors,
            rejection_reason=reason,
            host_interception_supported=(
                surface in (DeliverySurface.SAVED_SUMMARY, DeliverySurface.REPOSITORY_RESPONSE)
            ),
        )

    # 3. Determine authority and host interception support based on surface
    if surface in (DeliverySurface.SAVED_SUMMARY, DeliverySurface.REPOSITORY_RESPONSE):
        authority = EnforcementAuthority.MACHINE_ENFORCED
        interception_supported = True
    else:
        # IDE_ANSWER and HOST_ACTIVITY_CARDS have no physical interception hook in Antigravity
        authority = EnforcementAuthority.INSTRUCTION_ONLY
        interception_supported = False

    return DeliveryResult(
        surface=surface,
        enforcement_authority=authority,
        is_delivered=True,
        delivered_text=text,
        content_digest=current_digest,
        validation_result=val_res,
        errors=[],
        rejection_reason=None,
        host_interception_supported=interception_supported,
    )


def measure_output_reduction(
    authored_text: str,
    baseline_words: int = BASELINE_AUTHORED_WORDS,
    baseline_chars: int = BASELINE_AUTHORED_CHARS,
) -> dict[str, Any]:
    """Measure word and character reduction of authored text against frozen baseline."""
    w_count = count_words(authored_text)
    c_count = count_characters(authored_text)

    word_ratio = round((w_count / baseline_words) * 100, 2) if baseline_words else 0.0
    char_ratio = round((c_count / baseline_chars) * 100, 2) if baseline_chars else 0.0

    word_reduction = round(100.0 - word_ratio, 2)
    char_reduction = round(100.0 - char_ratio, 2)

    return {
        "authored_words": w_count,
        "authored_chars": c_count,
        "baseline_words": baseline_words,
        "baseline_chars": baseline_chars,
        "word_ratio_pct": word_ratio,
        "char_ratio_pct": char_ratio,
        "word_reduction_pct": word_reduction,
        "char_reduction_pct": char_reduction,
        "within_120_word_ceiling": w_count <= 120,
        "within_1400_char_ceiling": c_count <= 1400,
    }
