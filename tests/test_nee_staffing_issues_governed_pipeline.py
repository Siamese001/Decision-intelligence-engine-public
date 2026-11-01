"""
Regression test suite for NEE Staffing Issues Dan Stephan (09/23/2026).

Validates complete governed pipeline compliance across Outputs 01, 02, and 03,
certifying elimination of the shadow MCP bypass, enforcement of C-21 Gold Standard
Speaker Perspectives Appendix, and authentic cryptographic decision sealing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_qa import evaluate_all_qa_gates, evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from tools.mcp_speech_server import generate_synthesis_artifacts

MEETING_DIR = Path("output/enterprise_account_planning/09-23-2026/NEE_Staffing_Issues_Dan_Stephan")
F1_PATH = MEETING_DIR / "01_NEE_Staffing_Issues_Dan_Stephan_09-23-2026_0145PM_0201PM_ET_hitl_resolved_transcript.md"
F2_PATH = MEETING_DIR / "02_NEE_Staffing_Issues_Dan_Stephan_09-23-2026_0145PM_0201PM_ET_executive_recap.md"
F3_PATH = MEETING_DIR / "03_NEE_Staffing_Issues_Dan_Stephan_09-23-2026_0145PM_0201PM_ET_canonical_signal_projection.json"


@pytest.fixture(scope="module")
def f1_text() -> str:
    assert F1_PATH.exists(), f"Missing deliverable 01: {F1_PATH}"
    return F1_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def f2_text() -> str:
    assert F2_PATH.exists(), f"Missing deliverable 02: {F2_PATH}"
    return F2_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def f3_text() -> str:
    assert F3_PATH.exists(), f"Missing deliverable 03: {F3_PATH}"
    return F3_PATH.read_text(encoding="utf-8")


def test_shadow_mcp_synthesizer_fails_closed():
    """Verify that unmanaged shadow MCP tool is decommissioned and fails closed."""
    turns = [{"speaker": "Amit", "start_time": "00:00:01", "text": "Testing bypass."}]
    res = generate_synthesis_artifacts("nee_staffing", "09-23-2026", "Amit Ayer", json.dumps(turns))
    data = json.loads(res)
    assert data["status"] == "FAIL_CLOSED_DEPRECATED"
    assert "GOVERNANCE_BYPASS_BLOCKED" in data["error"]


def test_output_01_forensic_transcript_passes_gate_1(f1_text: str):
    """Verify that Output 01 passes deterministic Gate 1 with 0 defects."""
    res = run_qa_gate_1(f1_text, metadata={"duration_minutes": 16})
    assert res["passed"] is True, f"Gate 1 failed: {res.get('failures')}"
    assert len(res["failures"]) == 0


def test_output_02_executive_recap_passes_gate_2(f2_text: str):
    """Verify that Output 02 passes deterministic Gate 2 across all subgates."""
    res = run_qa_gate_2(f2_text, metadata={"duration_minutes": 16})
    assert res["passed"] is True, f"Gate 2 failed: {res.get('failures')}"
    assert res["status"] == "PASS"
    assert len(res["failures"]) == 0

    # Explicit subgate assertions
    gates = res["gates"]
    assert gates["Gate_2_1_Parsed_Structural_Completeness"]["passed"] is True
    assert gates["Gate_2_2_Typed_Action_Register"]["passed"] is True
    assert gates["Gate_2_8_Appendix_Completeness"]["passed"] is True


def test_output_02_c21_appendix_gold_standard(f2_text: str):
    """Verify that Output 02 satisfies C-21 Gold Standard Speaker Perspectives Appendix."""
    res = audit_appendix_gold_standard(
        f2_text,
        duration_minutes=16,
        primary_speakers=["Amit Ayer"],
        speaker_shares={"Drew Oakley": 0.25, "Cameron Mercer": 0.15},
    )
    assert res.passed is True, f"Appendix audit failed: {res.failures}"
    assert res.has_appendix is True
    assert res.speaker_sections_count == 3
    assert res.thematic_perspectives_count >= 10

    # Ensure all 3 speakers exceed C-21 word and theme requirements
    details = res.speaker_details
    amit_key = [k for k in details if "Amit Ayer" in k][0]
    dan_key = [k for k in details if "Drew Oakley" in k][0]
    stephan_key = [k for k in details if "Cameron Mercer" in k][0]

    assert details[amit_key]["body_words"] >= 500
    assert details[amit_key]["theme_count"] >= 5

    assert details[dan_key]["body_words"] >= 200
    assert details[dan_key]["theme_count"] >= 3

    assert details[stephan_key]["body_words"] >= 200
    assert details[stephan_key]["theme_count"] >= 3


def test_output_03_canonical_signal_projection_passes_gate_3(f1_text: str, f3_text: str):
    """Verify that Output 03 strictly validates against CanonicalSignalProjectionV57 and Gate 3."""
    data = json.loads(f3_text)
    assert data["schema_version"] == "5.7"

    # Pydantic model validation
    validated = CanonicalSignalProjectionV57.model_validate(data)
    assert "NEE_Staffing_Issues_Dan_Stephan" in validated.meeting_id

    # Deterministic Gate 3 evaluation
    res = run_qa_gate_3(f3_text, f1_text=f1_text)
    assert res["passed"] is True, f"Gate 3 failed: {res.get('failures')}"
    assert res["status"] == "PASS"
    assert len(res["failures"]) == 0

    # Explicit subgate assertions
    gates = res["gates"]
    assert gates["Gate_3_1_Strict_Schema_Validation"]["passed"] is True
    assert gates["Gate_3_2_Semantic_Spine_and_Ontology_Conformance"]["passed"] is True
    assert gates["Gate_3_3_Temporal_Hygiene_and_Lifecycle_Consistency"]["passed"] is True
    assert gates["Gate_3_4_Evidence_Index_Fidelity_and_Grounding"]["passed"] is True
    assert gates["Gate_3_5_Signal_Amplification_and_Anti_Sprawl"]["passed"] is True
    assert gates["Gate_3_9_Predicate_Domain_Linter"]["passed"] is True
    assert gates["Gate_3_10_Mutation_Budget_Governor"]["passed"] is True


def test_evaluate_all_qa_gates_composite(f1_text: str, f2_text: str, f3_text: str):
    """Verify composite execution of all deterministic QA gates."""
    qa_report = evaluate_all_qa_gates(
        f1_text, f2_text, f3_text, metadata={"duration_minutes": 16}
    )
    assert qa_report["passed"] is True
    assert qa_report["status"] == "PASS"


def test_evaluate_judge_and_release_e2e_under_gpt_5_6_sol_high(f1_text: str, f2_text: str, f3_text: str):
    """Verify evaluate_judge_and_release executes under gpt-5.6-sol high and authorizes release."""
    qa_rep, judge_rep, decision = evaluate_judge_and_release(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        source_text=f1_text,
        mock=True,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        metadata={"date": "09/23/2026", "meeting_name": "NEE_Staffing_Issues_Dan_Stephan", "duration_minutes": 16},
    )
    assert qa_rep["passed"] is True
    assert judge_rep["status"] == "PASS"
    assert judge_rep["evaluator_model"] == "gpt-5.6-sol"
    assert judge_rep["reasoning_effort"] == "high"
    assert decision["status"] == "PASS"
    assert decision["authoritative_write_authorized"] is True


def test_authentic_receipts_on_disk(f1_text: str, f2_text: str, f3_text: str):
    """Verify all 4 evaluation receipts on disk are authentic, matching artifact sha256 hashes."""
    qa_path = MEETING_DIR / "qa_gates_report.json"
    judge_path = MEETING_DIR / "llm_judge_evaluation.json"
    decision_path = MEETING_DIR / "final_release_decision.json"
    intake_path = MEETING_DIR / "intake_package.json"

    assert qa_path.exists()
    assert judge_path.exists()
    assert decision_path.exists()
    assert intake_path.exists()

    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["status"] == "PASS"
    assert decision["authoritative_write_authorized"] is True

    manifest = decision["artifact_manifest"]
    assert manifest["evaluator_model"] == "gpt-5.6-sol"
    assert manifest["reasoning_effort"] in ("high", "max")

    # Compute expected sha256 checksums
    h1 = hashlib.sha256(F1_PATH.read_bytes()).hexdigest()
    h2 = hashlib.sha256(F2_PATH.read_bytes()).hexdigest()
    h3 = hashlib.sha256(F3_PATH.read_bytes()).hexdigest()

    assert manifest["output_1_sha256"] == h1
    assert manifest["output_2_sha256"] == h2
    assert manifest["output_3_sha256"] == h3

    # Check intake package
    intake_pkg = json.loads(intake_path.read_text(encoding="utf-8"))
    assert intake_pkg["manifest"]["deliverable_01"]["sha256"] == h1
    assert intake_pkg["manifest"]["deliverable_02"]["sha256"] == h2
    assert intake_pkg["manifest"]["deliverable_03"]["sha256"] == h3
