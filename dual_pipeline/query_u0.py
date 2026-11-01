"""Query U0 validation stage — standalone, testable request validation.

Extracted from ``QueryEngine._stage_validate_and_normalize`` so that query
request validation can be independently tested, audited, and referenced by
contract.  This module validates the incoming question, user identity,
classification, audiences, materiality, delivery profile, and authorization
context before any retrieval or reasoning occurs.
"""
from __future__ import annotations

from dataclasses import dataclass

from .authorization import AuthorizationContext
from .core import normalize_space, sha256_bytes
from .query_router import (
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    MAX_QUESTION_LENGTH,
)


@dataclass(slots=True)
class ValidatedQueryRequest:
    """Immutable result of U0 query validation.

    All fields have been validated, normalized, and are safe for downstream
    consumption by L1, L0, C0, and PA.
    """

    question: str
    question_digest: str
    user_id: str
    classification: str
    audiences: list[str]
    materiality: str | None
    delivery_profile: str | None
    artifact_type: str | None
    card_id: str | None
    card_view_version: int | None
    card_candidate_review: bool
    provider_egress_allowed: bool
    authorization_context: AuthorizationContext | None


class QueryU0Validator:
    """Stateless query request validator (U0 stage).

    Validates and normalizes all incoming query parameters.  Raises
    ``ValueError`` for invalid input and ``PermissionError`` for
    authorization failures.

    Parameters
    ----------
    production_qualified : bool
        Whether the active BGE-M3 backend is production-qualified.
    model_manifest_digest : str | None
        The backend's model manifest digest, for production binding.
    """

    def __init__(
        self,
        *,
        production_qualified: bool = False,
        model_manifest_digest: str | None = None,
    ) -> None:
        self.production_qualified = production_qualified
        self.model_manifest_digest = model_manifest_digest

    def validate(
        self,
        question: str,
        *,
        user_id: str = "local-authorized-operator",
        classification: str = "internal",
        audience: str = "nee-engineering",
        materiality: str | None = None,
        delivery_profile: str | None = None,
        artifact_type: str | None = None,
        card_id: str | None = None,
        card_view_version: int | None = None,
        card_candidate_review: bool = False,
        provider_egress_allowed: bool = True,
        authorization_context: AuthorizationContext | None = None,
    ) -> ValidatedQueryRequest:
        """Validate and normalize a query request.

        Returns a ``ValidatedQueryRequest`` on success.  Raises
        ``ValueError`` or ``PermissionError`` on validation failure.
        """

        # --- Authorization gate ---
        if self.production_qualified and authorization_context is None:
            raise PermissionError(
                "production query requires an authenticated authorization context"
            )
        if authorization_context is not None:
            authorization_context.require("knowledge:query")
            user_id = authorization_context.subject
            classification = authorization_context.classification_clearance
            audience = ",".join(authorization_context.audiences)

        # --- Question validation ---
        if not isinstance(question, str):
            raise ValueError("question must be a string")
        if len(question) > MAX_QUESTION_LENGTH:
            raise ValueError(
                f"question must not exceed {MAX_QUESTION_LENGTH} characters"
            )
        question = normalize_space(question)
        if not question:
            raise ValueError("question is required")

        # --- Production model binding ---
        if self.production_qualified:
            assert authorization_context is not None
            if (
                authorization_context.approved_model_manifest_digest
                != self.model_manifest_digest
            ):
                raise PermissionError(
                    "production query authorization is not bound to the "
                    "BGE-M3 model manifest"
                )
            authorization_context.require_resources(
                {"pdlc_question": sha256_bytes(question.encode("utf-8"))}
            )

        # --- Classification validation ---
        classification = {"external_approved": "public"}.get(classification, classification)
        if classification not in {"public", "internal", "confidential", "restricted"}:
            raise ValueError("unsupported query classification")

        # --- Materiality validation ---
        if materiality is not None and materiality not in {"low", "medium", "high"}:
            raise ValueError("materiality must be low, medium, or high")

        # --- Delivery profile validation ---
        if delivery_profile is not None and delivery_profile not in DELIVERY_PROFILES:
            raise ValueError("unsupported delivery profile")

        # --- Artifact type validation ---
        if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
            raise ValueError("unsupported artifact type")

        # --- Card option validation ---
        if card_id is None and (
            card_view_version is not None or card_candidate_review
        ):
            raise ValueError("card view options require a card_id")
        if card_view_version is not None and card_view_version < 1:
            raise ValueError("card_view_version must be positive")

        # --- Audience normalization ---
        audiences = list(dict.fromkeys(
            normalize_space(v) for v in audience.split(",") if normalize_space(v)
        ))
        if not audiences:
            raise ValueError("at least one audience is required")

        return ValidatedQueryRequest(
            question=question,
            question_digest=sha256_bytes(question.encode("utf-8")),
            user_id=user_id,
            classification=classification,
            audiences=audiences,
            materiality=materiality,
            delivery_profile=delivery_profile,
            artifact_type=artifact_type,
            card_id=card_id,
            card_view_version=card_view_version,
            card_candidate_review=card_candidate_review,
            provider_egress_allowed=provider_egress_allowed,
            authorization_context=authorization_context,
        )
