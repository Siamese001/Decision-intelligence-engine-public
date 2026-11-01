"""
Adversarial and edge-case tests for Stage 1 Document Ingestion Routing & Ingress (DEF-S1-01 through DEF-S1-12).

Covers mandatory edge-case categories:
1. Boundary payloads (empty text with metadata units, extreme slide numbers > 500, undated/untitled documents).
2. Malformed & non-UTF8 inputs (binary PDF/DOCX bytes in pre-intake classifier, malformed budget strings).
3. Invariant bypass attempts (ingest/* worktree code-freeze bypass, spoof guard evasion, placeholder audience auto-admission, ungrounded role injection).
4. Exact defect regression pinning across DEF-S1-01 .. DEF-S1-12.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.cli.ingest import (
    _generate_default_directive_id,
    build_canonical_metadata,
    handle_canonical_ingest,
)
from dual_pipeline.cli.ingest_worktree_guard import assert_ingest_worktree_code_freeze
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    build_document_ingestion_plan,
    extract_role_framework_catalog_projection,
)
from dual_pipeline.document_pipeline_core.signal_value_router import (
    ExtractionMode,
    PerspectiveOrigin,
    SignalTier,
    apply_signal_profile_to_metadata,
    assess_document_signal_profile,
)
from dual_pipeline.intake_classifier import (
    _build_fusion_coordination,
    classify_packages,
)
from dual_pipeline.pre_intake_classifier import (
    AUDIENCE_PLACEHOLDER,
    CLASSIFICATION_PLACEHOLDER,
    propose_pre_intake_classification,
)


def test_def_s1_01_metadata_hydration_precedes_semantic_flags_and_infers_md_doc(tmp_path: Path) -> None:
    """DEF-S1-01: Co-located metadata.json with is_document=True and .md document auto-detection."""
    doc_dir = tmp_path / "partner_advisory_charter"
    doc_dir.mkdir(parents=True)
    md_file = doc_dir / "charter_overview.md"
    md_file.write_text(
        "# Project Nexus Architecture Charter\n\n"
        "## Executive Summary\n"
        "This document defines the target cloud architecture, governance model, and milestone schedule "
        "for the Apex Global Enterprises AI platform.\n\n"
        "## Scope & Deliverables\n"
        "- Deliverable 1: Canonical Knowledge Graph\n"
        "- Deliverable 2: BGE-M3 Hybrid Retrieval\n",
        encoding="utf-8",
    )
    (doc_dir / "metadata.json").write_text(
        json.dumps({"project": "project_nexus", "is_document": True}),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(md_file),
        source=None,
        inputs=None,
        metadata=None,
        document=False,
        notes=False,
        copilot_notes=False,
        output3_only=False,
        command="ingest",
        regenerate=None,
        bypass_speaker_hitl=False,
        allow_duplicate=False,
    )
    meta = build_canonical_metadata(args)
    assert meta["is_document"] is True
    assert meta["canonical_modality"] == CanonicalModality.DOCUMENT.value
    assert meta["output3_only"] is True
    assert meta["outputs"] == ["03"]

    # Also verify standalone .md document without metadata.json is detected via is_document_source
    standalone_md = tmp_path / "standalone_architecture_charter.md"
    standalone_md.write_text(
        "# System Design Specification\n\n## Section 1: Overview\n"
        "This document defines the architectural specification without any speaker turns or timestamps.\n"
        "## Section 2: Data Flow\nPipeline stages U0 through E3.\n",
        encoding="utf-8",
    )
    args_standalone = argparse.Namespace(
        input=str(standalone_md),
        source=None,
        inputs=None,
        metadata=None,
        document=False,
        notes=False,
        copilot_notes=False,
        output3_only=False,
        command="ingest",
        regenerate=None,
        bypass_speaker_hitl=False,
        allow_duplicate=False,
    )
    meta_standalone = build_canonical_metadata(args_standalone)
    assert meta_standalone["is_document"] is True
    assert meta_standalone["canonical_modality"] == CanonicalModality.DOCUMENT.value
    assert meta_standalone["outputs"] == ["03"]


def test_def_s1_02_worktree_code_freeze_guard_fails_closed_on_committed_and_git_errors(tmp_path: Path) -> None:
    """DEF-S1-02: assert_ingest_worktree_code_freeze checks target worktree_dir, main...HEAD commits, and fails closed on git errors."""
    target_wt = tmp_path / "ingest_wt"
    target_wt.mkdir()

    # 1. Committed modification on ingest/* branch relative to main must fail closed
    def fake_run_git_committed(cmd: list[str], cwd: Path | None = None, check: bool = True) -> str:
        assert cwd == target_wt.resolve()
        if cmd[0] == "status":
            return ""
        if cmd[0] == "diff":
            assert "storage/" in cmd
            assert "policy/" in cmd
            assert "contracts/" in cmd
            return "storage/connection.py\n"
        return ""

    with (
        patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="ingest/test_doc"),
        patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", side_effect=fake_run_git_committed),
    ):
        with pytest.raises(RuntimeError, match="committed source code modifications"):
            assert_ingest_worktree_code_freeze(target_wt)

    # 2. Subprocess / git error on ingest/* branch must fail closed (never swallowed)
    with (
        patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="ingest/test_doc"),
        patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", side_effect=OSError("git lock error")),
    ):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: Failed to verify code-freeze state"):
            assert_ingest_worktree_code_freeze(target_wt)


def test_def_s1_03_pre_intake_classifier_handles_binary_bytes_and_document_authors() -> None:
    """DEF-S1-03: Binary PDF/DOCX bytes do not crash with UnicodeDecodeError, and source.author/contributors map domains."""
    binary_pdf_bytes = (
        b"%PDF-1.7\n%\xff\xd8\xff\xe0\x00\x10\n"
        b"1 0 obj << /Title (Statement of Work and Pricing Schedule) >> endobj\n"
        b"stream\n\x89\xfe\x00\xffConfidential pricing and procurement terms for AWS architecture.\nendstream"
    )
    doc_package = {
        "artifact_id": "doc-binary-pdf-case",
        "source": {
            "source_id": "src-sow-001",
            "title": "Apex Global Statement of Work",
            "author": "Lead Architect <architect@consulting.example.com>",
            "contributors": ["Client Sponsor <sponsor@grid.example.com>"],
            "classification": CLASSIFICATION_PLACEHOLDER,
            "audiences": [AUDIENCE_PLACEHOLDER],
        },
    }
    proposal = propose_pre_intake_classification(package=doc_package, source_bytes=binary_pdf_bytes)
    assert proposal["proposed_classification"] == "confidential"
    assert "customer_partner_participant_boundary" in proposal["signal_types"]
    assert "contractual_or_commercial_material" in proposal["signal_types"]
    assert proposal["proposed_audiences"] == ["internal_consulting", "nee-engineering"]
    assert proposal["audience_basis"] == "policy_mapped_meeting_domains"


def test_def_s1_04_and_05_parallel_batch_runs_preflight_and_disambiguates_directive_id(tmp_path: Path) -> None:
    """DEF-S1-04 & DEF-S1-05: Batch dispatch runs after preflight validation and untitled docs get distinct directive IDs."""
    doc_a = tmp_path / "doc_alpha.pdf"
    doc_b = tmp_path / "doc_beta.pdf"
    doc_a.write_bytes(b"%PDF-1.4 alpha content")
    doc_b.write_bytes(b"%PDF-1.4 beta content")

    id_a = _generate_default_directive_id({"project": "nexus", "input_path": str(doc_a)})
    id_b = _generate_default_directive_id({"project": "nexus", "input_path": str(doc_b)})
    assert id_a != id_b
    assert "doc_alpha_" in id_a
    assert "doc_beta_" in id_b

    # Legacy .ppt in batch_inputs must be blocked BEFORE parallel batch dispatch
    bad_ppt = tmp_path / "legacy_deck.ppt"
    bad_ppt.write_bytes(b"legacy powerpoint")
    args_bad = argparse.Namespace(
        input=None,
        source=None,
        inputs=[str(doc_a), str(bad_ppt)],
        enforce_worktree=True,
        worktree_dir=str(tmp_path),
        provider="offline",
        dry_run=True,
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_MODALITY_GOVERNED_REFUSAL"):
        handle_canonical_ingest(args_bad)


def test_def_s1_06_and_12_deterministic_planner_and_metadata_mutate_graph() -> None:
    """DEF-S1-06 & DEF-S1-12: build_document_ingestion_plan is deterministic and respects slide_units without inflated page counts."""
    slide_units = [
        {"unit_number": i, "content": f"Slide {i}: Architecture component and workstream milestone details for pod {i}."}
        for i in range(1, 15)
    ]
    meta = {
        "filename_slug": "Architecture_Deck",
        "slide_units": slide_units,
        "planned_at": "2026-10-24T00:00:00Z",
    }
    plan1 = build_document_ingestion_plan("", meta)
    plan2 = build_document_ingestion_plan("", meta)
    assert plan1 == plan2
    assert plan1["planned_at"] == "2026-10-24T00:00:00Z"
    assert plan1["total_pages_detected"] == 14

    # Single-page prose document with no ## Page headers should report 1 page, not len(text)//2200
    prose_text = "Strategic architecture overview and system requirements. " * 200
    prose_plan = build_document_ingestion_plan(prose_text, {"planned_at": "2026-10-24"})
    assert prose_plan["total_pages_detected"] == 1


def test_def_s1_07_and_11_signal_router_spoof_guard_and_page_bounds() -> None:
    """DEF-S1-07 & DEF-S1-11: example.com does not trigger spoof guard, late spoof markers clamp archetype, and Slide 2026 is ignored."""
    legit_doc = (
        "## Page 1\n"
        "Apex Global Enterprises (NEE) & APEX Enterprise Architecture Charter\n"
        "Sample webhook endpoint: https://api.example.com/v1/telemetry\n"
        "Budget: $4.5M CAPEX. Workstreams: NEA Agentic Mesh, Grid Telemetry.\n"
        "## Page 2026\n"
        "2026 Strategic Roadmap & Target Architecture Milestones.\n"
    )
    meta: dict = {}
    profile = apply_signal_profile_to_metadata(legit_doc, meta)
    assert "spoof_guard_clamped" not in profile.signal_reasons
    assert profile.page_count < 500
    assert "enable_5_agent_intelligence" in meta

    # Spoof marker beyond char 4000 must still be caught and update metadata["document_archetype"]
    padding = "Routine operational architecture text. " * 150  # > 5500 chars
    spoof_doc = (
        padding
        + "\nWorkstream | Status | Owner | Target Date\n"
        + "synthetic-benchmark-spoof lorem ipsum\n"
    )
    spoof_meta: dict = {"document_archetype": "TABULAR_PORTFOLIO_EXPORT"}
    spoof_profile = apply_signal_profile_to_metadata(spoof_doc, spoof_meta)
    assert "spoof_guard_clamped" in spoof_profile.signal_reasons
    assert spoof_meta["document_archetype"] == DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC.value


def test_def_s1_08_archetype_classifier_sow_vs_workshop_and_beyond_3000_chars() -> None:
    """DEF-S1-08: SOW/Orals documents mentioning a breakout session are not misclassified as SPARSE_ENABLEMENT_WORKSHOP."""
    sow_doc = (
        "# Statement of Work (SOW) - Apex Global Enterprises AI Platform\n"
        "Negotiated purchaser terms, hourly rate, and estimated service fees.\n"
        "Phase 1 includes a Discussion (Document) session and Break! Be back at 2pm.\n"
    )
    assert classify_document_archetype(sow_doc, {}) == DocumentArchetype.CONTRACTUAL_SOW_PROSE

    # Role catalog cues appearing after char 3000 must still classify as ROLE_FRAMEWORK_CATALOG
    prefix = "Introductory corporate overview text.\n" * 120  # > 4000 chars
    late_catalog = (
        prefix
        + "\nme@partner_advisory: Career Framework Role Profiles\n"
        + "Understanding Role Profile Components\n"
        + "Relationship to Other Roles\n"
        + "Role Milestones\n"
    )
    assert classify_document_archetype(late_catalog, {}) == DocumentArchetype.ROLE_FRAMEWORK_CATALOG


def test_def_s1_09_intake_classifier_rejects_html_temporal_anchor_and_placeholder_audience(tmp_path: Path) -> None:
    """DEF-S1-09: HTML packages are never temporal anchors and OPERATOR_SET_AUDIENCE never grants auto-admission."""
    rows = [
        {"path": "/tmp/doc.html", "contract_id": "com.nee:html-source-intake:1.0.0"},
        {"path": "/tmp/slides.pdf", "contract_id": "com.nee:pdf-source-intake:1.0.0"},
    ]
    fusion = _build_fusion_coordination(rows)
    assert fusion["temporal_anchor"] is None
    assert set(fusion["semantic_enhancers"]) == {"/tmp/doc.html", "/tmp/slides.pdf"}

    pkg_payload = {
        "contract_id": "nee:meeting-transcript-intake:1.0.0",
        "artifact_id": "pkg-placeholder-aud-test",
        "meeting": {
            "meeting_id": "mtg-001",
            "title": "Internal Status",
            "classification": "internal",
            "audiences": [AUDIENCE_PLACEHOLDER],
        },
    }
    pkg_file = tmp_path / "pkg.json"
    pkg_bytes = json.dumps(pkg_payload).encode("utf-8")
    pkg_file.write_bytes(pkg_bytes)
    pkg_sha = hashlib.sha256(pkg_bytes).hexdigest()

    with patch("dual_pipeline.intake_classifier.validate_artifact"):
        report = classify_packages(
            directive_id="directive-test-001",
            package_set_digest=pkg_sha,
            packages=[{"path": str(pkg_file), "sha256": pkg_sha}],
            batch_admission={"admission_mode": "SIGNED_POLICY"},
        )
    assert report["shared_audiences"] == []
    assert report["auto_candidate_admission_eligible"] is False
    assert report["human_approval_required"] is True


def test_def_s1_10_role_catalog_extractor_grounds_aliases_and_respects_budget() -> None:
    """DEF-S1-10: Role catalog extractor only emits aliases/skills grounded in document text and obeys mutation budget."""
    minimal_catalog = (
        "## Page 1\n"
        "Career Framework Role Profiles\n"
        "## Page 10\n"
        "Solution Architect\n"
        "Role Description: Owns end-to-end technical architecture and system design.\n"
        "## Page 12\n"
        "Data Architect\n"
        "Also Known As: Cloud Data Architect\n"
        "Role Description: Designs cloud analytical data platforms.\n"
    )
    proj = extract_role_framework_catalog_projection(
        minimal_catalog,
        {"document_ingestion_plan": {"recommended_mutation_budget": 2}},
    )
    assert proj["max_mutation_budget"] == 2
    upserts = proj["graph_mutations"]["entity_upserts"]
    signals = proj["graph_mutations"]["relationship_signals"]
    assert len(upserts) + len(signals) <= 2

    full_proj = extract_role_framework_catalog_projection(minimal_catalog, {})
    ent_by_id = {e["id"]: e for e in full_proj["graph_mutations"]["entity_upserts"]}
    assert "role:solution-architect" in ent_by_id
    # Unmentioned aliases for Solution Architect ("Architect", "Technical Architect" not in text)
    assert "Technical Architect" not in ent_by_id["role:solution-architect"]["aliases"]
    assert "role:data-architect" in ent_by_id
    assert ent_by_id["role:data-architect"]["aliases"] == ["Cloud Data Architect"]
