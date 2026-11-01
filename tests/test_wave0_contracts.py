"""
tests/test_wave0_contracts.py

Unit test suite for Wave 0 contracts and baseline invariants:
- ReleaseThresholdPolicy immutability and digest computation
- TemporalSSOT 3-tier roundtrip transformations and validation
- RuntimeReceipt creation, digest computation, and verification
- SSOT Inventory schema validation
"""

import json
from datetime import date
from pathlib import Path

import pytest

from dual_pipeline.receipt_schema import ReceiptFactory
from dual_pipeline.release_thresholds import (
    CANONICAL_RELEASE_POLICY,
    ReleaseThresholdPolicy,
    ReleaseVerdict,
)
from dual_pipeline.temporal_ssot import TemporalSSOT

ROOT = Path(__file__).resolve().parent.parent


def test_release_threshold_policy_immutability():
    """Verify policy is frozen and cannot be mutated."""
    policy = ReleaseThresholdPolicy()
    with pytest.raises(Exception):
        policy.output3_pass_threshold = 90  # type: ignore

    digest = policy.compute_policy_digest()
    assert len(digest) == 64
    assert digest == CANONICAL_RELEASE_POLICY.compute_policy_digest()


def test_release_threshold_boundary_evaluations():
    """Verify individual and composite threshold evaluations."""
    policy = CANONICAL_RELEASE_POLICY

    # Output 1: pass >= 95
    assert policy.evaluate_output_verdict(1, 95) == ReleaseVerdict.PASS
    assert policy.evaluate_output_verdict(1, 94.9) == ReleaseVerdict.PASS_ADVISORY
    assert policy.evaluate_output_verdict(1, 80) == ReleaseVerdict.PASS_ADVISORY
    assert policy.evaluate_output_verdict(1, 79.9) == ReleaseVerdict.FAIL

    # Output 2: pass >= 95 (no advisory permitted below 95)
    assert policy.evaluate_output_verdict(2, 95) == ReleaseVerdict.PASS
    assert policy.evaluate_output_verdict(2, 94.9) == ReleaseVerdict.FAIL
    assert policy.evaluate_output_verdict(2, 80) == ReleaseVerdict.FAIL
    assert policy.evaluate_output_verdict(2, 79.9) == ReleaseVerdict.FAIL

    # Output 3: pass >= 95 (no advisory permitted below 95)
    assert policy.evaluate_output_verdict(3, 95) == ReleaseVerdict.PASS
    assert policy.evaluate_output_verdict(3, 94.9) == ReleaseVerdict.FAIL
    assert policy.evaluate_output_verdict(3, 80) == ReleaseVerdict.FAIL
    assert policy.evaluate_output_verdict(3, 79.9) == ReleaseVerdict.FAIL

    # Composite clean pass
    verdict, write_auth, meta = policy.evaluate_composite_release(
        s1=95, s2=95, s3=95, v1="PASS", v2="PASS", v3="PASS"
    )
    assert verdict == ReleaseVerdict.PASS
    assert write_auth is True

    # Composite advisory pass (write NOT authorized)
    verdict, write_auth, meta = policy.evaluate_composite_release(
        s1=85, s2=95, s3=95, v1="PASS_ADVISORY", v2="PASS", v3="PASS"
    )
    assert verdict == ReleaseVerdict.PASS_ADVISORY
    assert write_auth is False


def test_temporal_ssot_conversions():
    """Verify bidirectional conversions across all 3 tiers."""
    raw_date = "09/11/2026"
    parsed = TemporalSSOT.parse_to_date(raw_date)
    assert parsed == date(2026, 9, 11)

    iso = TemporalSSOT.to_iso8601(parsed)
    assert iso == "2026-09-11"
    assert TemporalSSOT.validate_iso8601(iso) is True

    iso_utc = TemporalSSOT.to_iso8601(parsed, include_time=True)
    assert iso_utc == "2026-09-11T00:00:00Z"
    assert TemporalSSOT.validate_iso8601(iso_utc) is True

    human = TemporalSSOT.to_human_date(parsed)
    assert human == "09/11/2026"
    assert TemporalSSOT.validate_human_date(human) is True

    path_date = TemporalSSOT.to_path_date(parsed)
    assert path_date == "09-11-2026"
    assert TemporalSSOT.validate_path_date(path_date) is True

    # Roundtrip from path date
    parsed_from_path = TemporalSSOT.parse_to_date(path_date)
    assert parsed_from_path == parsed
    assert TemporalSSOT.to_human_date(parsed_from_path) == "09/11/2026"


def test_runtime_receipt_creation_and_digest():
    """Verify runtime receipt hashing and serialization."""
    policy = CANONICAL_RELEASE_POLICY
    receipt = ReceiptFactory.create_receipt(
        wave_id="wave0",
        plan_version="1.0.0",
        command=".venv/bin/pytest tests/test_wave0_contracts.py -v --timeout=60",
        exit_code=0,
        input_paths=[ROOT / "dual_pipeline" / "release_thresholds.py"],
        output_paths=[ROOT / "reports" / "ssot_inventory.json"],
        policy_digest=policy.compute_policy_digest(),
        verdict="PASS",
    )

    d = receipt.to_dict()
    assert d["wave_id"] == "wave0"
    assert d["verdict"] == "PASS"
    assert len(d["receipt_digest"]) == 64
    assert len(d["commit_sha"]) == 40


def test_ssot_inventory_exists():
    """Verify reports/ssot_inventory.json exists and is structured."""
    inv_path = ROOT / "reports" / "ssot_inventory.json"
    assert inv_path.exists()
    data = json.loads(inv_path.read_text(encoding="utf-8"))
    assert data["summary"]["total_ingestion_scripts"] == 18
    assert len(data["ingestion_scripts"]) == 18
