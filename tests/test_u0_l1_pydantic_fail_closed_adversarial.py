"""Adversarial and edge-case tests for Wave 4: U0/L1/Gate F Circuit-Breaker & Strict Pydantic Contracts.

Covers mandatory edge-case categories:
1. Invariant bypass attempt:
   - Uncertified audio (`cert=None`) rejected by `AudioCompletenessCircuitBreaker` with `gate_f_certified=False`.
   - `u0_ingress.batch_validate_preflight_checks` refuses to fabricate `urn:nee:live-container-preflight-proof`.
   - `ROSTER_PRIOR` speaker role resolution stays `is_human_confirmed=False` and `provenance="ROSTER_PRIOR"`, never spoofing `HUMAN_OPERATOR` or `res_hitl_*`.
2. Malformed input:
   - `RiskUpdate` missing `evidence_ids` raises `ValidationError` instead of fabricating `['ev-01']`.
   - `EntityUpsert`, `EvidenceStatement`, and `RiskUpdate` reject unknown extra fields (`ConfigDict(extra='forbid')`).
   - Dangling key colons in `validate_with_pydantic` fail closed with `PydanticValidationError` instead of injecting `null`.
3. Exact production regression:
   - `normalize_lexical_and_phonetics` preserves English phrases `"with manny"`, `"stigma"`, and `"before the band's"` without mutating them into staff names or unrelated words.
"""
from __future__ import annotations

from pathlib import Path
import pytest
from pydantic import ValidationError

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
from dual_pipeline.transcript_stages_core.lexical_normalizer import normalize_lexical_and_phonetics
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import reconcile_speaker_role_resolutions
from dual_pipeline.validation.pydantic_gate import PydanticValidationError, validate_with_pydantic
from dual_pipeline.validation.transcript_models import (
    EntityUpsert,
    EvidenceStatement,
    RiskUpdate,
)


def test_circuit_breaker_rejects_uncertified_audio_by_default() -> None:
    """Uncertified audio (cert=None) must fail closed and set gate_f_certified=False."""
    metadata = {
        "is_audio": True,
        "declared_duration_seconds": 1800.0,
        "expected_duration_seconds": 1800.0,
    }
    with pytest.raises(AudioIncompleteFailFastError, match="cert is None"):
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata=metadata)
    assert metadata.get("gate_f_certified") is False


def test_u0_ingress_never_fabricates_live_container_preflight_proof(tmp_path: Path) -> None:
    """Live container without signed operator receipt must not fabricate urn:nee:live-container-preflight-proof."""
    (tmp_path / "record_start_receipt.json").write_text('{"started": true}', encoding="utf-8")
    meta = {
        "is_audio": True,
        "time": "10:00 AM - 10:30 AM ET",
        "attendees": ["Alex Rivera"],
        "container_dir": str(tmp_path),
    }
    report = batch_validate_preflight_checks(meta, raw_transcript="**Alex Rivera [00:00:01]:** Hello.")
    assert not report.is_valid
    assert "attendee_confirmation" in report.missing_fields
    assert meta.get("attendee_confirmation_receipt") is None


def test_lexical_normalizer_preserves_english_words() -> None:
    """English words like 'with manny', 'stigma', and 'before the band's' must not be corrupted."""
    raw = (
        "[00:01:00] **Alex Rivera:** I spoke with manny about the stigma around legacy systems "
        "before the band's performance."
    )
    cleaned = normalize_lexical_and_phonetics(raw, {"diarization_status": "NATIVE"})
    assert "with manny" in cleaned.lower()
    assert "harper Hoffman" not in cleaned
    assert "stigma" in cleaned
    assert "Figma" not in cleaned
    assert "before the band's" in cleaned


def test_roster_prior_never_escalates_to_human_operator() -> None:
    """ROSTER_PRIOR provenance must remain ROSTER_PRIOR and is_human_confirmed=False."""
    proj = {
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        "evidence_index": {},
    }
    metadata = {
        "attendees_confirmed_by_operator": True,
        "speaker_role_resolutions": {
            "Alex Rivera": {
                "person_name": "Alex Rivera",
                "resolved_role": "Principal Architect",
                "organization": "Acme Corp",
                "provenance": "ROSTER_PRIOR",
                "is_human_confirmed": False,
            }
        },
    }
    reconcile_speaker_role_resolutions(proj, metadata)
    entities = proj["graph_mutations"]["entity_upserts"]
    person_ent = next(e for e in entities if e["id"].startswith("person:"))
    assert person_ent["provenance"] == "ROSTER_PRIOR"
    assert person_ent["is_human_confirmed"] is False
    assert not str(person_ent.get("resolution_id") or "").startswith("res_hitl_")


def test_strict_pydantic_models_forbid_extra_fields_and_missing_risk_evidence() -> None:
    """Pydantic models must reject unknown extra fields and missing RiskUpdate.evidence_ids."""
    with pytest.raises(ValidationError):
        RiskUpdate(risk_id="risk:01", status="OPEN", description="Unbound risk without evidence_ids")

    with pytest.raises(ValidationError):
        EntityUpsert(
            id="system:core",
            type="system",
            name="Core Engine",
            unexpected_extra_field="forbidden",
        )

    with pytest.raises(ValidationError):
        EvidenceStatement(
            speaker="Alex Rivera",
            kind="technical",
            text="Valid text",
            unknown_field=123,
        )


def test_pydantic_gate_rejects_dangling_colon_null_injection() -> None:
    """validate_with_pydantic must reject dangling colon syntax instead of coercing to null."""
    with pytest.raises(PydanticValidationError):
        validate_with_pydantic(
            EntityUpsert,
            '{"id": "system:core", "type": "system", "name": "Core", "description": ',
            auto_heal=True,
        )
