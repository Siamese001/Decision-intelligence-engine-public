"""
Wave 2 Adversarial & Edge-Case tests for intake package sealing and QA release gating.
Covers 4 mandatory edge-case categories:
1. Boundary payload: Empty/short source document rejected by semantic yield gate (`ZeroSignalError`).
2. Malformed input: Invalid locator syntax rejected by `AssertionProvenanceError`.
3. Invariant bypass attempt: `prepare_package` rejects unauthorized `final_release_decision.json` (`authoritative_write_authorized: false`).
4. Rollback & recovery: Tampered `canonical_digest` detected by `validate_artifact`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import pytest

from dual_pipeline.document_pipeline_core.executor import (
    AssertionProvenanceError,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.exceptions import ZeroSignalError
from tools.prepare_evidence_envelope_intake import prepare_package, validate_artifact


def test_boundary_empty_source_rejected_by_semantic_yield(tmp_path: Path) -> None:
    src = tmp_path / "source_document.txt"
    src.write_text("Too short", encoding="utf-8")
    draft = tmp_path / "03_draft.json"
    draft.write_text(
        json.dumps({"schema_version": "5.7", "evidence_index": {}, "graph_mutations": {"entity_upserts": [], "relationship_signals": []}}),
        encoding="utf-8",
    )
    with pytest.raises(ZeroSignalError):
        prepare_package(
            draft,
            src,
            tmp_path / "intake_package.json",
            source_location="test/short.txt",
            classification="internal",
            audiences=["internal_consulting"],
            started_at="2026-10-24T00:00:00Z",
        )


def test_malformed_locator_rejected_by_provenance_validator() -> None:
    proj = {
        "evidence_index": {
            "ev-bad": {"text": "Valid quote", "locator": "INVALID_LOCATOR_SYNTAX_###"}
        },
        "graph_mutations": {
            "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-bad"]}],
            "relationship_signals": [],
        },
    }
    with pytest.raises(AssertionProvenanceError):
        validate_attachment_assertion_provenance(proj, {})


def test_invariant_bypass_unauthorized_release_decision_blocks_prepare_package(tmp_path: Path) -> None:
    src = tmp_path / "source_document.txt"
    src.write_text(" ".join(["substantive"] * 80), encoding="utf-8")
    (tmp_path / "final_release_decision.json").write_text(
        json.dumps({"status": "FAIL", "authoritative_write_authorized": False}),
        encoding="utf-8",
    )
    draft = tmp_path / "03_draft.json"
    draft.write_text(
        json.dumps({"schema_version": "5.7", "evidence_index": {}, "graph_mutations": {"entity_upserts": [], "relationship_signals": []}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Release authorization rejected"):
        prepare_package(
            draft,
            src,
            tmp_path / "intake_package.json",
            source_location="test/doc.txt",
            classification="internal",
            audiences=["internal_consulting"],
            started_at="2026-10-24T00:00:00Z",
        )


def test_tampered_canonical_digest_rejected() -> None:
    pkg_path = Path(__file__).resolve().parents[1] / "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement/intake_package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    validate_artifact(pkg)
    tampered = copy.deepcopy(pkg)
    tampered["meeting"]["title"] = "Tampered Title After Seal"
    with pytest.raises( Exception ):
        validate_artifact(tampered)
