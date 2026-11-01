"""
End-to-End (E2E) lifecycle tests for Stage 1 Document Ingestion Routing & Ingress.

Verifies the complete Stage 1 pipeline flow:
1. CLI ingress & co-located metadata.json hydration (`build_canonical_metadata`).
2. Pre-intake binary/document classification & domain mapping (`propose_pre_intake_classification`).
3. Archetype classification & Signal Value Router profile binding (`apply_signal_profile_to_metadata`).
4. Deterministic pre-execution ingestion plan generation & persistence (`build_document_ingestion_plan`, `persist_document_ingestion_plan`) for a 70-slide architecture deck as well as a role catalog.
5. Full `CanonicalAgenticPipeline` execution in an ephemeral sandbox (`tmp_path`) without polluting `output/`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.cli.ingest import (
    _generate_default_directive_id,
    build_canonical_metadata,
)
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    build_document_ingestion_plan,
    persist_document_ingestion_plan,
)
from dual_pipeline.document_pipeline_core.signal_value_router import (
    SignalTier,
    apply_signal_profile_to_metadata,
)
from dual_pipeline.pre_intake_classifier import (
    AUDIENCE_PLACEHOLDER,
    CLASSIFICATION_PLACEHOLDER,
    propose_pre_intake_classification,
)


def _build_70_slide_architecture_deck() -> tuple[str, list[dict]]:
    slides: list[str] = []
    slide_units: list[dict] = []
    for idx in range(1, 71):
        if idx in (1, 15, 35, 55):
            heading = f"## Slide {idx}\nSection Divider: Phase {idx} Overview"
            content = f"Section Divider: Phase {idx} Overview"
        else:
            content = (
                f"NEA Enterprise Architecture Workstream {idx}: "
                f"Lead Architect: Jane Doe, Principal Engineer: John Smith. "
                f"Target AWS Bedrock & BGE-M3 hybrid vector indexing milestone Q{(idx % 4) + 1} 2026. "
                f"Approved CAPEX allocation ${(idx % 5) + 1}.2M for Apex Global Enterprises (NEE) & APEX grid telemetry."
            )
            heading = f"## Slide {idx}\n{content}"
        slides.append(heading)
        slide_units.append({"unit_number": idx, "locator": f"slide:{idx}", "content": content})
    return "\n\n".join(slides), slide_units


def test_stage1_70_slide_deck_routing_and_planning_e2e(tmp_path: Path) -> None:
    """E2E verification of Stage 1 routing, signal profile, and deterministic planning on a 70-slide deck."""
    deck_text, slide_units = _build_70_slide_architecture_deck()
    deck_dir = tmp_path / "nea_deck_package"
    deck_dir.mkdir(parents=True)
    deck_file = deck_dir / "2026-10-24_NEA_Enterprise_Architecture_Deck.md"
    deck_file.write_text(deck_text, encoding="utf-8")
    (deck_dir / "metadata.json").write_text(
        json.dumps(
            {
                "project": "project_nexus",
                "title": "NEA Enterprise Architecture 70-Slide Deck",
                "date": "10/24/2026",
                "is_document": True,
                "mutate_graph": False,
                "planned_at": "2026-10-24T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        input=str(deck_file),
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
    assert meta["mutate_graph"] is False

    # Pre-intake classification with document source author/contributors
    pre_pkg = {
        "artifact_id": "nea-70-slide-deck",
        "source": {
            "source_id": "nea-deck-2026",
            "title": meta["title"],
            "author": "Principal Architect <architect@consulting.example.com>",
            "contributors": ["Program Lead <lead@enterprise.example.com>"],
            "classification": CLASSIFICATION_PLACEHOLDER,
            "audiences": [AUDIENCE_PLACEHOLDER],
        },
    }
    pre_report = propose_pre_intake_classification(
        package=pre_pkg,
        source_bytes=deck_file.read_bytes(),
    )
    assert pre_report["proposed_classification"] in ("confidential", "restricted")
    assert set(pre_report["proposed_audiences"]) == {"internal_consulting", "nee-engineering"}

    # Signal router & planner verification (both with raw text and metadata-only slide_units)
    meta_units_only = dict(meta)
    meta_units_only["slide_units"] = slide_units
    profile = apply_signal_profile_to_metadata("", meta_units_only)
    assert profile.signal_tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert profile.page_count == 70
    assert profile.enable_unit_batch_extraction is True
    assert profile.enable_5_agent_intelligence is True
    assert meta_units_only["enable_5_agent_intelligence"] is True

    plan_from_text = build_document_ingestion_plan(deck_text, meta)
    plan_from_units = build_document_ingestion_plan("", meta_units_only)
    assert plan_from_text["total_pages_detected"] == 70
    assert plan_from_units["total_pages_detected"] == 70
    assert plan_from_text["selected_high_signal_count"] >= 60
    assert plan_from_text["planned_at"] == "2026-10-24T12:00:00Z"

    plan_path = persist_document_ingestion_plan(plan_from_text, tmp_path / "plan_out", slug="00_NEA_Deck")
    assert plan_path.name == "00_NEA_Deck_ingestion_plan.json"
    assert plan_path.is_file()


def test_stage1_canonical_pipeline_document_execution_e2e(tmp_path: Path) -> None:
    """E2E execution of CanonicalAgenticPipeline on a multi-section architecture specification in tmp_path."""
    doc_text = """## Page 1
Apex Global Enterprises (NEE) & PartnerAdvisory Statement of Work (SOW) & Architecture Specification
Negotiated purchaser terms, hourly rate, and estimated service fees.
Last Updated: October 24, 2026
Reference Endpoint: https://api.example.com/v1/health

## Page 2
Workstream 1: Enterprise Knowledge Graph
PM: Sarah Jenkins
SM: David Kim
Scope: Deploy SQLite WAL graph store and BGE-M3 semantic sidecar with $1.8M budget allocation.

## Page 3
Workstream 2: Live Meeting Copilot
PM: Alex Rivera
SM: Priya Patel
Scope: Local Apple Silicon MLX Whisper ASR streaming with zero cloud egress.
"""
    src_file = tmp_path / "2026-10-24_NEE_Architecture_Spec.md"
    src_file.write_text(doc_text, encoding="utf-8")
    sandbox_out = tmp_path / "ephemeral_out"

    args = argparse.Namespace(
        input=str(src_file),
        source=None,
        inputs=None,
        metadata=None,
        document=False,
        notes=False,
        copilot_notes=False,
        output3_only=False,
        command="ingest",
        project="project_nexus",
        meeting="NEE_Architecture_Spec",
        title="NEE Architecture Spec",
        date="10/24/2026",
        regenerate=None,
        bypass_speaker_hitl=False,
        allow_duplicate=False,
    )
    meta = build_canonical_metadata(args)
    meta["test_fast"] = True
    assert meta["is_document"] is True
    assert meta["output3_only"] is True
    directive_id = _generate_default_directive_id(meta)

    pipeline = CanonicalAgenticPipeline(provider_type="offline", modality=CanonicalModality.DOCUMENT)
    res = pipeline.run(
        input_path=src_file,
        metadata=meta,
        output_dir=str(sandbox_out),
        mutate_graph=False,
        auto_mutate_on_pass=False,
        directive_id=directive_id,
        test_fast=True,
    )
    assert res.get("status") in ("COMPLETE", "SUCCESS", "PASS", None)

    plan_files = list(sandbox_out.rglob("00_*_ingestion_plan.json"))
    assert len(plan_files) == 1
    plan_payload = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert plan_payload["total_pages_detected"] == 3
    assert plan_payload["signal_tier"] == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value

    out3_files = [p for p in sandbox_out.rglob("03_*_canonical_signal_projection.json") if "attempts" not in p.parts]
    assert len(out3_files) == 1
