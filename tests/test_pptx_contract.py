"""
tests/test_pptx_contract.py — Contract validation and normalization tests for PPTX intake.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.normalization import normalize_pptx_source
from tools.validate_dual_pipeline_contracts import (
    ValidationFailure,
    canonical_artifact_digest,
)

_FIXTURE_PATH = Path("contracts/dual_pipeline/fixtures/valid/pptx-source-intake.json")


def _resign(value: dict[str, Any]) -> dict[str, Any]:
    """Recompute and seal canonical digest for a mutated contract artifact."""
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": canonical_artifact_digest(value),
    }
    return value


@pytest.fixture
def valid_pptx_fixture() -> dict:
    """Load the canonical valid pptx-source-intake fixture."""
    assert _FIXTURE_PATH.exists(), f"Missing fixture at {_FIXTURE_PATH}"
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_valid_fixture_conformance(valid_pptx_fixture):
    """Verify that the valid fixture satisfies the pptx-source-intake schema."""
    # Should validate cleanly without raising
    validate_artifact(valid_pptx_fixture)
    assert valid_pptx_fixture["contract_id"] == "urn:nee:dual-pipeline:pptx-source-intake:1.0.0"
    assert valid_pptx_fixture["schema_version"] == "1.0.0"
    assert valid_pptx_fixture["source"]["slide_count"] > 0
    assert len(valid_pptx_fixture["chunks"]) > 0


def test_fail_closed_on_unknown_property(valid_pptx_fixture):
    """Governed artifacts must fail closed on unexpected additional properties."""
    bad_payload = copy.deepcopy(valid_pptx_fixture)
    bad_payload["unexpected_injected_field"] = "malicious_or_drifted_value"
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(_resign(bad_payload))
    assert exc_info.value.issue.code == "SCHEMA_UNKNOWN_FIELD"


def test_fail_closed_on_invalid_authority_rank(valid_pptx_fixture):
    """Authority rank must be within 1..9."""
    bad_payload = copy.deepcopy(valid_pptx_fixture)
    bad_payload["source"]["authority_rank"] = 10
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(_resign(bad_payload))
    assert exc_info.value.issue.code == "SCHEMA_MAXIMUM"

    bad_payload["source"]["authority_rank"] = 0
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(_resign(bad_payload))
    assert exc_info.value.issue.code == "SCHEMA_MINIMUM"


def test_fail_closed_on_negative_slide_count(valid_pptx_fixture):
    """Slide count must be a non-negative integer."""
    bad_payload = copy.deepcopy(valid_pptx_fixture)
    bad_payload["source"]["slide_count"] = -1
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(_resign(bad_payload))
    assert exc_info.value.issue.code == "SCHEMA_MINIMUM"


def test_fail_closed_on_missing_chunks(valid_pptx_fixture):
    """Missing chunks array must violate schema."""
    bad_payload = copy.deepcopy(valid_pptx_fixture)
    del bad_payload["chunks"]
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(_resign(bad_payload))
    assert exc_info.value.issue.code == "SCHEMA_REQUIRED"


def test_tampered_digest_rejected(valid_pptx_fixture):
    """Tampered body without resign must fail digest check."""
    bad_payload = copy.deepcopy(valid_pptx_fixture)
    bad_payload["source"]["title"] = "Tampered Title"
    with pytest.raises(ValidationFailure) as exc_info:
        validate_artifact(bad_payload)
    assert exc_info.value.issue.code == "DIGEST_MISMATCH"


def test_normalize_pptx_source_lifecycle(valid_pptx_fixture):
    """Verify normalize_pptx_source emits a contract-valid normalized candidate bundle."""
    bundle = normalize_pptx_source(valid_pptx_fixture)

    assert bundle["contract_id"] == "urn:nee:dual-pipeline:normalized-candidate-bundle:1.0.0"
    assert bundle["schema_version"] == "1.0.0"
    assert len(bundle["candidates"]) == len(valid_pptx_fixture["chunks"])
    assert len(bundle["evidence"]) == len(valid_pptx_fixture["chunks"])

    # Validate output bundle against dual-pipeline schema
    validate_artifact(bundle)

    first_cand = bundle["candidates"][0]
    assert first_cand["candidate_id"].startswith("candidate-")
    assert first_cand["candidate_type"] == "source_chunk"
    assert "normalized_text" in first_cand
    assert first_cand["materiality"] == "high"

    first_ev = bundle["evidence"][0]
    assert first_ev["source_id"] == valid_pptx_fixture["source"]["source_id"]
    assert first_ev["source_version_id"] == valid_pptx_fixture["source"]["source_version_id"]
    assert first_ev["authority_rank"] == valid_pptx_fixture["source"]["authority_rank"]
    assert first_ev["locator"]["kind"] == "section"
