"""Adversarial and boundary test suite for Wave 2 Document Partitioning and Retrieval Clearance.

Covers:
1. Invariant bypass attempt: Attempted client audience leakage for internal_consulting fails closed.
2. Exact production regression: Real AMS 101 Florida slide deck auto-escalates to confidential.
3. Boundary payload: Varied letter-spaced confidentiality markings and commercial pricing patterns.
4. Non-regression: Client-scoped Apex Global projects retain internal shared audience clearance.
5. Invariant verification: Fail-closed denial of confidential records exposed to client audiences.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from dual_pipeline.document_pipeline_core.metadata_helper import (
    scaffold_document_metadata,
    _detect_document_classification,
    _resolve_document_audiences,
)
from dual_pipeline.document_pipeline_core.retrieval_visibility import (
    preflight_document_clearance,
    RetrievalClearancePending,
)
from dual_pipeline.retrieval_clearance import retrieval_clearance


def test_internal_consulting_client_audience_leak_fails_closed():
    """Invariant bypass attempt: internal_consulting paired with client audience fails closed."""
    meta = {
        "project": "internal_consulting",
        "classification": "confidential",
        "audiences": ["internal_consulting", "nee-engineering"],
    }
    with pytest.raises(RetrievalClearancePending) as excinfo:
        preflight_document_clearance(meta, required=True)

    assert "INTERNAL_CONSULTING_CLIENT_AUDIENCE_LEAK" in str(excinfo.value)
    report = excinfo.value.report
    assert report["status"] == "BLOCKED_CLIENT_AUDIENCE_LEAK"
    assert report["reason_code"] == "INTERNAL_CONSULTING_CLIENT_AUDIENCE_LEAK"


def test_internal_consulting_client_approved_external_leak_fails_closed():
    """Invariant bypass attempt: internal_consulting paired with client_approved_external fails closed."""
    meta = {
        "project": "internal_consulting",
        "classification": "internal",
        "audiences": ["internal_consulting", "client_approved_external"],
    }
    with pytest.raises(RetrievalClearancePending) as excinfo:
        preflight_document_clearance(meta, required=True)

    assert "INTERNAL_CONSULTING_CLIENT_AUDIENCE_LEAK" in str(excinfo.value)


def test_real_ams_florida_deck_scaffold_isolation():
    """Exact production regression: Real AMS Florida slide deck is classified confidential and restricted to internal_consulting."""
    onedrive_p = Path.home() / "Library" / "CloudStorage" / "OneDrive-PartnerAdvisory" / "Documents" / "HR IT" / "AMS Training and Solution Materials - AMS 101 - Florida.pdf"
    uploaded_p = Path.home() / ".gemini" / "antigravity" / "brain" / "6c14a140-2810-403d-a804-a36e8f16b601" / ".user_uploaded" / "media_1792319357695_61facab1.pdf"
    target_p = onedrive_p if onedrive_p.exists() else (uploaded_p if uploaded_p.exists() else None)

    if not target_p:
        pytest.skip("Authoritative AMS Florida deck not present on local filesystem.")

    meta = scaffold_document_metadata(target_p, project="internal_consulting")
    assert meta["project"] == "internal_consulting"
    assert meta["classification"] == "confidential"
    assert meta["audiences"] == ["internal_consulting"]
    assert "nee-engineering" not in meta["audiences"]

    # Preflight clearance must pass cleanly for isolated internal_consulting
    report = preflight_document_clearance(meta, required=True)
    assert report["status"] == "ELIGIBLE"
    assert report["reason_code"] == "ELIGIBLE"


def test_letter_spaced_and_explicit_confidentiality_markings():
    """Boundary payload: Letter-spaced markings and confidential banners auto-escalate classification."""
    cases = [
        ("Slide Deck", "Overview of operations\nINTERNAL USE ONLY\nNext steps", "confidential"),
        ("Slide Deck", "Overview of operations\nINTERNAL USE O N LY\nNext steps", "confidential"),
        ("Architecture", "PRICING STRUCTURE\nFixed fee milestones and margin targets", "confidential"),
        ("Strategy", "PROPRIETARY AND CONFIDENTIAL\nPartnerAdvisory internal playbook", "confidential"),
        ("Commercial Terms", "Legal SOW and Master Services Agreement review", "confidential"),
        ("Engineering Notes", "Standard architecture diagram showing Kafka to SQLite stream", "internal"),
    ]
    for title, text, expected in cases:
        detected = _detect_document_classification(text, title)
        assert detected == expected, f"Failed for title='{title}', text='{text[:30]}...'"


def test_apex_global_client_projects_retain_shared_audiences():
    """Non-regression: Client-scoped Apex Global projects (project_lens, nea, project_atlas) with internal classification retain shared audiences."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write("# Weekly Architecture Notes\nDiscussion on data synchronization between Kafka and SQLite.\n")
        tmp_path = Path(f.name)

    try:
        for proj in ("project_lens", "nea", "project_atlas"):
            meta = scaffold_document_metadata(tmp_path, project=proj)
            assert meta["project"] == proj
            assert meta["classification"] == "internal"
            assert meta["audiences"] == ["internal_consulting", "nee-engineering"]

            report = preflight_document_clearance(meta, required=True)
            assert report["status"] == "ELIGIBLE"
            assert report["reason_code"] == "ELIGIBLE"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_retrieval_clearance_denies_confidential_client_exposure():
    """Invariant verification: retrieval_clearance denies any confidential record exposed to nee-engineering."""
    allowed, reason = retrieval_clearance(
        assertion_type="confirmed_canonical_fact",
        classification="confidential",
        audiences=["internal_consulting", "nee-engineering"],
    )
    assert not allowed
    assert reason == "confidential_record_exposed_to_client_audience"


def test_wave3_three_bucket_bypass_rejected_by_per_unit_materiality_gate():
    """Wave 3: Citing 10 slides across early/middle/late of a 25-slide deck (with 23 substantive slides) fails closed."""
    from dual_pipeline.document_pipeline_core.quality import validate_slide_deck_coverage_and_yield

    slides_md = []
    for i in range(1, 26):
        if i == 1:
            slides_md.append("## Slide 1: AMS 101 Title\nWelcome")
        elif i == 15:
            slides_md.append("## Slide 15: Divider\nSection Break")
        else:
            slides_md.append(
                f"## Slide {i}: Substantive Topic {i}\n"
                f"- Enterprise capability and $1.{i}M recurring revenue metric on slide {i}.\n"
                f"- Second bullet point confirming operational model for slide {i}."
            )
    source_text = "\n\n".join(slides_md)
    meta = {"is_document": True, "page_count": 25}

    # Sparse projection citing 10 slides across early (4,5,6), middle (12,16,17), and late (18,22,23,24)
    cited_subset = [4, 5, 6, 12, 16, 17, 18, 22, 23, 24]
    sparse_proj = {
        "evidence_index": {
            f"ev-{p:02d}": {
                "exact_text": f"Enterprise capability and $1.{p}M recurring revenue metric on slide {p}.",
                "locator": {"kind": "slide", "value": f"Slide {p}"},
            }
            for p in cited_subset
        },
        "graph_mutations": {},
    }
    passed, errors, telemetry = validate_slide_deck_coverage_and_yield(
        sparse_proj, source_text=source_text, metadata=meta, mutation_count=25
    )
    assert not passed
    assert telemetry["missing_buckets"] == []  # Old 3-bucket check was fooled!
    assert telemetry["exempt_structural_units"] == [1, 15]
    assert len(telemetry["substantive_units"]) == 23
    assert len(telemetry["uncovered_substantive_units"]) == 13
    assert 8 in telemetry["uncovered_substantive_units"]
    assert 20 in telemetry["uncovered_substantive_units"]
    assert any("DOCUMENT_SLIDE_COVERAGE_GAP" in e for e in errors)

    # Full substantive projection covering 22 of 23 substantive slides (95.7% >= 90%) passes
    full_subset = [p for p in telemetry["substantive_units"] if p != 25]
    full_proj = {
        "evidence_index": {
            f"ev-{p:02d}": {
                "exact_text": f"Enterprise capability and $1.{p}M recurring revenue metric on slide {p}.",
                "locator": {"kind": "slide", "value": f"Slide {p}"},
            }
            for p in full_subset
        },
        "graph_mutations": {},
    }
    passed_full, errors_full, telemetry_full = validate_slide_deck_coverage_and_yield(
        full_proj, source_text=source_text, metadata=meta, mutation_count=25
    )
    assert passed_full, f"Expected pass, got: {errors_full}"
    assert telemetry_full["substantive_coverage_ratio"] >= 0.90


def test_retrieval_clearance_allows_internal_shared_records():
    """Invariant verification: retrieval_clearance allows internal records shared between PartnerAdvisory and client."""
    allowed, reason = retrieval_clearance(
        assertion_type="confirmed_canonical_fact",
        classification="internal",
        audiences=["internal_consulting", "nee-engineering"],
    )
    assert allowed
    assert reason is None
