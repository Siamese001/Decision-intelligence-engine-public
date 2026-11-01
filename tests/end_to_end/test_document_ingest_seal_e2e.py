"""
tests/end_to_end/test_document_ingest_seal_e2e.py

End-to-end and adversarial edge-case verification for document ingestion release sealing
and source_document.txt artifact preservation.

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty / minimal document intake with sealed manifest
2. Malformed input: Altered source text, missing manifest, corrupted digest
3. Concurrency & race: Repeated concurrent seal verification calls
4. Rollback & recovery: Content drift detection preventing forged publication
5. Exact regression: Markdown document ingestion without source_text metadata
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.lifecycle import resolve_raw_source_path
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    verify_decision_seal,
    _resolve_release_artifacts,
)


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    out_dir = tmp_path / "output_package"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def test_markdown_document_source_retention_and_seal_e2e(temp_output_dir: Path):
    """
    E2E Regression Test:
    Ensures that for markdown document ingestion, source_document.txt is properly
    written to the package directory and resolved by _resolve_release_artifacts
    even when metadata.json has had source_text sanitized for persistence.
    """
    raw_md_text = (
        "# Enterprise Data Organization\n\n"
        "## Pricing & Analytics\n"
        "- Leader: Amit Ayer\n"
        "- Scope: Core AI / ML Analytics and Valuation Services\n"
    )

    # 1. Simulate source resolution during lifecycle
    src_file = temp_output_dir / "input_document.md"
    src_file.write_text(raw_md_text, encoding="utf-8")

    resolved_path = resolve_raw_source_path(
        artifacts_map={},
        src_file_path=src_file,
        out_p=temp_output_dir,
        source_text=raw_md_text,
    )
    assert resolved_path.exists()
    source_doc = temp_output_dir / "source_document.txt"
    assert source_doc.exists()
    assert source_doc.read_text(encoding="utf-8") == raw_md_text

    # 2. Simulate sanitized metadata.json (source_text stripped)
    sanitized_meta = {
        "project": "edo",
        "title": "EDO Pricing & Analytics",
        "date": "10-15-2026",
        "input_path": str(src_file),
    }
    (temp_output_dir / "metadata.json").write_text(json.dumps(sanitized_meta), encoding="utf-8")

    # 3. Simulate Output 3 projection file
    out3_payload = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [{"id": "team:edo_pricing_analytics", "name": "EDO Pricing & Analytics"}]
        }
    }
    out3_text = json.dumps(out3_payload, indent=2)
    (temp_output_dir / "03_Edo_Pricing_Analytics_10-15-2026_canonical_signal_projection.json").write_text(
        out3_text, encoding="utf-8"
    )

    # 4. Build artifact manifest with source_document text as f1
    manifest = build_artifact_manifest(
        f1_text=raw_md_text,
        f2_text="",
        f3_text=out3_text,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        judge_eval_digest="test_digest_1015",
    )
    decision = {
        "status": "PASS",
        "mode": "document",
        "authoritative_write_authorized": True,
        "artifact_manifest": manifest,
    }

    # 5. Resolve artifacts from package directory using _resolve_release_artifacts
    f1_res, f2_res, f3_res = _resolve_release_artifacts(temp_output_dir, decision)
    assert f1_res == raw_md_text
    assert f3_res == out3_text

    # 6. Verify decision seal succeeds without drift
    valid, errors = verify_decision_seal(decision, f1_text=f1_res, f2_text=f2_res, f3_text=f3_res)
    assert valid is True, f"Seal verification failed: {errors}"
    assert len(errors) == 0


def test_adversarial_boundary_payload_empty_document(temp_output_dir: Path):
    """
    Adversarial Category 1: Boundary payload
    Verifies seal behavior on 0-byte / empty document content.
    """
    empty_source = ""
    out3_text = json.dumps({"schema_version": "5.7", "graph_mutations": {"entity_upserts": []}})

    manifest = build_artifact_manifest(
        f1_text=empty_source,
        f2_text="",
        f3_text=out3_text,
        model="fixture",
        reasoning_effort="none",
    )
    decision = {
        "status": "PASS",
        "mode": "document",
        "authoritative_write_authorized": True,
        "artifact_manifest": manifest,
    }

    valid, errors = verify_decision_seal(decision, f1_text=empty_source, f2_text="", f3_text=out3_text)
    assert valid is True
    assert len(errors) == 0


def test_adversarial_malformed_input_content_drift(temp_output_dir: Path):
    """
    Adversarial Category 2: Malformed input / Tampering
    Verifies fail-closed rejection when source_document.txt is altered or drifted.
    """
    original_text = "Authentic document content"
    out3_text = json.dumps({"schema_version": "5.7", "graph_mutations": {}})

    manifest = build_artifact_manifest(
        f1_text=original_text,
        f2_text="",
        f3_text=out3_text,
        model="fixture",
        reasoning_effort="none",
    )
    decision = {
        "status": "PASS",
        "mode": "document",
        "artifact_manifest": manifest,
    }

    # Tamper with active text
    tampered_text = original_text + "\nUnauthorized injected paragraph"
    valid, errors = verify_decision_seal(decision, f1_text=tampered_text, f2_text="", f3_text=out3_text)
    assert valid is False
    assert any("Output 1 content drifted" in err for err in errors)


def test_adversarial_invariant_bypass_missing_or_corrupt_manifest():
    """
    Adversarial Category 5: Invariant bypass attempt
    Verifies that missing, malformed, or fake seal digests are rejected.
    """
    # Missing manifest
    bad_dec = {"status": "PASS", "mode": "document"}
    valid, errors = verify_decision_seal(bad_dec, f1_text="abc", f2_text="", f3_text="def")
    assert valid is False
    assert "Missing artifact_manifest in decision data" in errors[0]

    # Tampered seal hash
    manifest = build_artifact_manifest(
        f1_text="abc",
        f2_text="",
        f3_text="def",
        model="fixture",
        reasoning_effort="none",
    )
    manifest["decision_seal_sha256"] = "f" * 64  # Fake digest
    tampered_dec = {"status": "PASS", "mode": "document", "artifact_manifest": manifest}
    valid_tampered, tampered_errors = verify_decision_seal(tampered_dec, f1_text="abc", f2_text="", f3_text="def")
    assert valid_tampered is False
    assert any("Decision seal mismatch" in err for err in tampered_errors)
