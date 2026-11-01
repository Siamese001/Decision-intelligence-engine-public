"""
dual_pipeline/strategic_reasoning_core/profiling.py

Profile derivation, validation, and serialization for strategic reasoning.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import stable_id

from .constants import (
    APPLICABILITY_SCOPES,
    AUTHORITY_SCOPES,
    KNOWLEDGE_ROLES,
    MODALITIES,
    PROFILE_FIELDS,
    REALIZATION_STATES,
    STRATEGIC_PROFILE_QUALIFIER,
)
from .quantitative_ontology import (
    classify_constraint_category,
    extract_quantitative_constraints,
    hydrate_quantitative_metadata,
    infer_object_literal_type,
    is_constraint_claim,
)


def _profile(
    knowledge_role: str,
    modality: str,
    authority_scope: str,
    applicability_scope: str,
    realization_state: str,
) -> dict[str, str]:
    value = {
        "knowledge_role": knowledge_role,
        "modality": modality,
        "authority_scope": authority_scope,
        "applicability_scope": applicability_scope,
        "realization_state": realization_state,
    }
    validate_strategic_profile(value)
    return value


def _validate_field_types_and_domains(value: dict[str, Any]) -> None:
    for field, field_value in value.items():
        if not isinstance(field_value, str):
            raise ValueError(f"strategic profile field {field} must be a string")
    if value["knowledge_role"] not in KNOWLEDGE_ROLES:
        raise ValueError(f"unknown knowledge role: {value['knowledge_role']}")
    if value["modality"] not in MODALITIES:
        raise ValueError(f"unknown modality: {value['modality']}")
    if value["authority_scope"] not in AUTHORITY_SCOPES:
        raise ValueError(f"unknown authority scope: {value['authority_scope']}")
    if value["applicability_scope"] not in APPLICABILITY_SCOPES:
        raise ValueError(f"unknown applicability scope: {value['applicability_scope']}")
    if value["realization_state"] not in REALIZATION_STATES:
        raise ValueError(f"unknown realization state: {value['realization_state']}")


def _validate_role_invariants(role: str, modality: str, authority: str, applicability: str, realization: str) -> None:
    if role in {"official_intent", "target_outcome"}:
        if (
            authority != "official_project_intent"
            or modality not in {"intended", "normative"}
            or realization not in {"intended", "planned"}
        ):
            raise ValueError(
                "official intent and target outcomes require official-intent scope "
                "without realized-outcome posture"
            )
    if role == "external_pattern":
        if (
            authority != "external_reference"
            or applicability not in {"generalizable", "analogous_only"}
            or realization != "not_applicable"
        ):
            raise ValueError(
                "external patterns require external-reference authority and "
                "non-realization posture"
            )
    if role == "observed_outcome" and (
        modality != "observed" or realization not in {"observed", "measured"}
    ):
        raise ValueError("observed outcomes require observed or measured realization")
    if realization in {"observed", "measured"} and modality in {"intended", "proposed"}:
        raise ValueError("intended or proposed material cannot claim realized outcomes")


def validate_strategic_profile(profile: Mapping[str, Any]) -> dict[str, str]:
    """Validate one closed strategic profile and its inference boundaries."""

    if set(profile) != set(PROFILE_FIELDS):
        raise ValueError("strategic profile has an invalid shape")
    value = {field: profile[field] for field in PROFILE_FIELDS}
    _validate_field_types_and_domains(value)
    _validate_role_invariants(
        value["knowledge_role"],
        value["modality"],
        value["authority_scope"],
        value["applicability_scope"],
        value["realization_state"],
    )
    return {field: str(value[field]) for field in PROFILE_FIELDS}


def _explicit_profile(qualifiers: Any) -> dict[str, str] | None:
    if isinstance(qualifiers, Mapping):
        candidate = qualifiers.get(STRATEGIC_PROFILE_QUALIFIER)
        if isinstance(candidate, Mapping):
            return validate_strategic_profile(candidate)
        if isinstance(candidate, str):
            try:
                decoded = json.loads(candidate)
            except Exception:
                return None
            if isinstance(decoded, Mapping):
                return validate_strategic_profile(decoded)
    return None


def explicit_strategic_profile(qualifiers: Any) -> dict[str, str] | None:
    """Return the validated strategic profile from qualifier payloads, if present."""

    return _explicit_profile(qualifiers)


def _is_constraint_record_eligible(qualifiers: Any, text: str | None) -> bool:
    if isinstance(qualifiers, Mapping) and qualifiers.get("legacy_claim_id"):
        return False
    return is_constraint_claim(text)


def strategic_profile_for_record(
    *,
    record_type: str,
    lane: str,
    classification: str | None = None,
    qualifiers: Any = None,
    source_posture: str | None = None,
    text: str | None = None,
) -> dict[str, str]:
    """Return an explicit profile or a conservative legacy projection."""

    explicit = _explicit_profile(qualifiers)
    if explicit is not None:
        return explicit

    posture = (source_posture or "").casefold()
    external = classification in {"public", "external_approved"} or any(
        token in posture for token in ("external", "public", "vendor")
    )
    if record_type == "source_chunk":
        if external:
            return _profile("external_pattern", "descriptive", "external_reference", "generalizable", "not_applicable")
        return _profile("source_position", "descriptive", "source_position", "client_specific", "not_applicable")
    if record_type == "action_item":
        return _profile("option", "proposed", "source_position", "nee_specific", "planned")
    if external:
        return _profile("external_pattern", "descriptive", "external_reference", "generalizable", "not_applicable")
    if lane == "candidate":
        return _profile("assumption", "proposed", "source_position", "nee_specific", "not_applicable")
    if _is_constraint_record_eligible(qualifiers, text):
        return _profile("constraint", "normative", "nee_current_state", "nee_specific", "not_applicable")
    return _profile("current_state_fact", "descriptive", "nee_current_state", "nee_specific", "not_applicable")


def answer_use_for_profile(
    *,
    base_answer_use: str,
    profile: Mapping[str, Any],
    lane: str,
) -> str:
    """Project strategic use without weakening lane or record-type controls."""

    value = validate_strategic_profile(profile)
    if base_answer_use in {"navigation_only", "guidance"}:
        return base_answer_use
    if base_answer_use == "citation_only":
        return "candidate_hypothesis" if lane == "candidate" else base_answer_use
    if lane == "external_public" and value["knowledge_role"] == "external_pattern":
        return "external_analogy"
    if lane != "approved":
        return base_answer_use
    role = value["knowledge_role"]
    if role == "external_pattern":
        return "external_analogy"
    if role in {"design_principle", "constraint", "evaluation_criterion"}:
        return "decision_constraint"
    if role in {
        "official_intent",
        "business_problem",
        "target_outcome",
        "option",
        "source_position",
        "assumption",
        "open_question",
    }:
        return "strategic_context"
    return base_answer_use


def create_strategic_profile_artifact(
    *,
    assertion_id: str,
    source_id: str,
    evidence_ids: Sequence[str],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    value = validate_strategic_profile(profile)
    artifact_id = stable_id("strategic-assertion-profile", assertion_id, value, length=28)
    return create_artifact(
        "strategic-assertion-profile",
        artifact_id,
        profile_id=artifact_id,
        assertion_id=assertion_id,
        source_id=source_id,
        evidence_ids=sorted(set(evidence_ids)),
        **value,
        extensions={},
    )
