"""Rubrics, system instructions, request serialization, and output parser."""
from __future__ import annotations

from typing import Any, Mapping

from ..core import canonical_json, digest
from .models import (
    QUALITY_DEFECT_CODES,
    QualityFailureCode,
    QualityModelPolicy,
    QualityProviderError,
    QualityProviderRequest,
    QualityRole,
    QualityVerdict,
    X1QualityConfig,
)


def _available_evidence_refs(packet: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for lane in ("approved", "candidate", "external_public"):
        values = packet.get(lane, [])
        if not isinstance(values, list):
            continue
        for item in values:
            record = item.get("record") if isinstance(item, Mapping) else None
            if not isinstance(record, Mapping):
                continue
            record_id = record.get("record_id")
            if isinstance(record_id, str):
                refs.add(record_id)
            evidence_ids = record.get("evidence_ids", [])
            if isinstance(evidence_ids, list):
                refs.update(value for value in evidence_ids if isinstance(value, str))
    return refs


def _input_bindings(
    *,
    answer: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    deterministic_controls: Mapping[str, Any],
    challenger_review: Mapping[str, Any] | None,
) -> dict[str, str]:
    return {
        "answer_digest": digest(answer),
        "evidence_packet_digest": str(
            evidence_packet.get("packet_digest", digest(evidence_packet))
        ),
        "deterministic_controls_digest": digest(deterministic_controls),
        "challenger_review_digest": (
            digest(challenger_review) if challenger_review is not None else digest({})
        ),
    }


def quality_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "defect_codes", "evidence_refs", "confidence"],
        "properties": {
            "verdict": {"type": "string", "enum": [
                QualityVerdict.PASS.value,
                QualityVerdict.REPAIRABLE_FAIL.value,
                QualityVerdict.ESCALATE_HITL.value,
                QualityVerdict.ABSTAIN.value,
            ]},
            "defect_codes": {
                "type": "array",
                "maxItems": 8,
                "uniqueItems": True,
                "items": {"type": "string", "enum": sorted(QUALITY_DEFECT_CODES)},
            },
            "evidence_refs": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
    }


def quality_system_instruction(role: QualityRole) -> str:
    role_text = "critique the draft" if role is QualityRole.CHALLENGER else "make a release recommendation"
    return (
        f"You {role_text}. Return only the required structured verdict. Use only the "
        "sealed answer and evidence packet supplied here. Do not retrieve, browse, call "
        "tools, write to a graph, infer evidence that is not supplied, or rewrite the answer. "
        "Use PASS only when the answer is grounded, complete enough for its request, and "
        "clear for its stated audience. Flag undefined terms, unexplained jargon, factual "
        "gaps, missing counterevidence, and client-readiness gaps with the governed codes."
    )


def build_quality_provider_body(
    *,
    config: X1QualityConfig,
    model_policy: QualityModelPolicy,
    request: QualityProviderRequest,
) -> dict[str, Any]:
    payload = {
        "role": request.role.value,
        "question": request.question,
        "answer": dict(request.answer),
        "sealed_evidence_packet": dict(request.evidence_packet),
        "deterministic_controls": dict(request.deterministic_controls),
        "challenger_review": dict(request.challenger_review)
        if request.challenger_review is not None
        else None,
        "input_bindings": dict(request.input_bindings),
        "repair_attempt": request.repair_attempt,
    }
    body = {
        "systemInstruction": {"parts": [{"text": quality_system_instruction(request.role)}]},
        "contents": [{"role": "user", "parts": [{"text": canonical_json(payload)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": quality_response_schema(),
            "maxOutputTokens": model_policy.maximum_output_tokens,
            "thinkingConfig": {"thinkingLevel": model_policy.reasoning_effort.upper()},
        },
    }
    if len(canonical_json(body)) > config.maximum_request_chars:
        raise QualityProviderError(
            QualityFailureCode.REQUEST_BUDGET_EXHAUSTED,
            "quality request exceeds the governed request-size ceiling",
        )
    return body


def _parse_quality_output(
    output: Any,
    *,
    evidence_packet: Mapping[str, Any],
) -> tuple[QualityVerdict, tuple[str, ...], tuple[str, ...], str]:
    if not isinstance(output, Mapping):
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider output must be an object",
        )
    required = {"verdict", "defect_codes", "evidence_refs", "confidence"}
    if set(output) != required:
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider output fields do not match the governed schema",
        )
    try:
        verdict = QualityVerdict(str(output["verdict"]))
    except ValueError as exc:
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider returned an unsupported verdict",
        ) from exc
    defects = output["defect_codes"]
    evidence_refs = output["evidence_refs"]
    confidence = output["confidence"]
    if (
        not isinstance(defects, list)
        or len(defects) > 8
        or len(set(defects)) != len(defects)
        or any(value not in QUALITY_DEFECT_CODES for value in defects)
    ):
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider returned invalid defect codes",
        )
    if (
        not isinstance(evidence_refs, list)
        or len(evidence_refs) > 32
        or len(set(evidence_refs)) != len(evidence_refs)
        or any(not isinstance(value, str) or not value for value in evidence_refs)
    ):
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider returned invalid evidence references",
        )
    available_refs = _available_evidence_refs(evidence_packet)
    if any(value not in available_refs for value in evidence_refs):
        raise QualityProviderError(
            QualityFailureCode.EVIDENCE_BINDING_INVALID,
            "quality provider referenced evidence outside the sealed packet",
        )
    if verdict is QualityVerdict.PASS and (defects or evidence_refs):
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "PASS may not contain defect or evidence references",
        )
    if verdict is not QualityVerdict.PASS and not defects:
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "a non-pass verdict requires at least one governed defect code",
        )
    if confidence not in {"low", "medium", "high"}:
        raise QualityProviderError(
            QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
            "quality provider returned invalid confidence",
        )
    return verdict, tuple(defects), tuple(evidence_refs), confidence
