"""
tests/test_modular_uwg_deployer_and_release.py

Verification suite for Wave 4: Stage Modularization (X1-X3 QA, UWG Deployer & Evals Release).
Tests the decoupled modules:
- dual_pipeline.transcript_pipeline_core.uwg_deployer
- dual_pipeline.transcript_pipeline_core.release_runner
- dual_pipeline.transcript_pipeline_core.qa_runner
- dual_pipeline.transcript_pipeline_core.preflight_coordinator
- dual_pipeline.transcript_pipeline_core.pipeline (RawTranscriptPipeline end-to-end)
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    resolve_output_directory_and_slug,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    prepare_and_verify_release_package,
)
from dual_pipeline.transcript_pipeline_core.uwg_deployer import (
    atomic_write_text,
    deploy_uwg_artifacts,
)


def test_uwg_atomic_write(tmp_path: Path):
    target = tmp_path / "test_file.txt"
    content = "Hello, atomic world!\nLine 2."
    atomic_write_text(target, content)
    assert target.exists()
    assert target.read_text(encoding="utf-8") == content


def test_deploy_uwg_artifacts(tmp_path: Path):
    output_dir = tmp_path / "project_nexus" / "09-12-2026" / "Phase1_Sync"
    artifacts = {
        "01_hitl_resolved_transcript.md": "# Forensic Transcript\n\n[00:00] Alice: Hello.",
        "02_executive_recap.md": "# Executive Recap\n\n## Context\nTest meeting.",
        "03_canonical_signal_projection.json": json.dumps({"nodes": [], "edges": []}),
    }
    metadata = {
        "meeting_name": "Phase 1 Sync",
        "standardized_slug": "Phase1_Sync",
        "date": "09/12/2026",
        "date_filename": "09-12-2026",
        "time_filename": "1000",
        "time": "10:00 AM",
        "project": "project_nexus",
    }
    qa_rep = {"passed": True, "output_01_forensic_transcript": {"pass": True}}
    judge_rep = {"status": "PASS", "composite_score": 0.95}
    decision = {"status": "PASS", "authoritative_write_authorized": True}

    result = deploy_uwg_artifacts(
        artifacts=artifacts,
        metadata=metadata,
        output_dir=str(output_dir),
        qa_report=qa_rep,
        judge_report=judge_rep,
        final_decision=decision,
    )

    assert Path(result["01_transcript"]).exists()
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()
    assert Path(result["qa_gates_report"]).exists()
    assert Path(result["llm_judge_evaluation"]).exists()
    assert Path(result["final_release_decision"]).exists()


def test_prepare_and_verify_release_package(tmp_path: Path):
    output_dir = tmp_path / "project_nexus" / "09-12-2026" / "Phase1_Sync"
    output_dir.mkdir(parents=True, exist_ok=True)
    f1 = output_dir / "01_Phase1_Sync_09-12-2026_1000_hitl_resolved_transcript.md"
    f1.write_text("# Forensic Transcript\n\nS01\nDrew Oakley: First principles rebuild.\n", encoding="utf-8")
    f2 = output_dir / "02_Phase1_Sync_09-12-2026_1000_executive_recap.md"
    f2.write_text("# Executive Recap\n\nContext\n", encoding="utf-8")
    f3 = output_dir / "03_Phase1_Sync_09-12-2026_1000_canonical_signal_projection.json"
    from dual_pipeline.core import ROOT, read_json
    fixture = read_json(
        ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"
    )
    draft_data = json.loads(json.dumps(fixture))
    f3.write_text(json.dumps(draft_data, indent=2), encoding="utf-8")

    written = {
        "01_transcript": str(f1),
        "02_recap": str(f2),
        "03_graph": str(f3),
        "source_notes": "",
    }
    metadata = {
        "meeting_name": "Phase 1 Sync",
        "classification": "confidential",
        "audiences": ["internal_consulting"],
    }
    artifacts: dict = {}

    pkg_path = prepare_and_verify_release_package(
        written=written,
        metadata=metadata,
        artifacts=artifacts,
        is_notes_mode=False,
    )
    assert pkg_path.exists()
    assert "intake_package.json" in artifacts
    pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg_data["schema_version"] == "1.0.0"


def test_preflight_coordinator_resolution(tmp_path: Path):
    metadata = {
        "meeting_name": "Project Beacon Architecture",
        "date": "09/12/2026",
        "time": "14:00",
        "project": "project_beacon",
    }
    out_dir, slug = resolve_output_directory_and_slug(
        metadata=metadata,
        transcript_text="Alice: We are reviewing the Project Beacon architecture today.",
        output_dir="auto",
    )
    assert "project_beacon" in out_dir
    assert "09-12-2026" in out_dir
    assert len(slug) <= 40
    assert metadata["standardized_slug"] == slug


def test_raw_transcript_pipeline_e2e_modular_execution(tmp_path: Path):
    pipeline = RawTranscriptPipeline(provider_type="offline")
    transcript = (
        "Alice [00:00:01]: Good morning everyone. We are here to review the Project Nexus architecture.\n"
        "Bob [00:00:15]: The BGE-M3 model latency is currently running at 150ms steady state.\n"
        "Alice [00:00:30]: Approved. We will deploy the pipeline release tomorrow."
    )
    metadata = {
        "meeting_name": "Nexus Review",
        "project": "project_nexus",
        "date": "09/12/2026",
        "time": "10:00 AM",
        "attendees": ["Alice", "Bob"],
        "archetype": "INTERNAL_TECHNICAL_SYNC",
    }
    out_dir = str(tmp_path / "project_nexus" / "09-12-2026" / "Nexus_Review")
    res = pipeline.run(
        input_path=transcript,
        metadata=metadata,
        output_dir=out_dir,
    )
    assert "01_transcript" in res
    assert Path(res["01_transcript"]).exists()
    assert Path(res["02_recap"]).exists()
    assert Path(res["03_graph"]).exists()
    assert Path(res["execution_receipt"]).exists()
