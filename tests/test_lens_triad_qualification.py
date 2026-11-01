"""
tests/test_lens_triad_qualification.py

Deterministic qualification test suite for the Project LENS deliverable triad
(01, 02, 03) and governance receipts under output/project_lens/09-18-2026/Jake_Amit_Miles_PartnerAdvisory/.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_qa_core.consistency import run_cross_artifact_consistency
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3

BASE_DIR = Path("output/project_lens/09-18-2026/Jake_Amit_Miles_PartnerAdvisory")
F1_PATH = BASE_DIR / "01_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_hitl_resolved_transcript.md"
F2_PATH = BASE_DIR / "02_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_executive_recap.md"
F3_PATH = BASE_DIR / "03_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_canonical_signal_projection.json"
QA_REPORT_PATH = BASE_DIR / "qa_gates_report.json"
RELEASE_DECISION_PATH = BASE_DIR / "final_release_decision.json"


@pytest.fixture(scope="module")
def triad_artifacts():
    assert F1_PATH.exists(), f"Missing {F1_PATH}"
    assert F2_PATH.exists(), f"Missing {F2_PATH}"
    assert F3_PATH.exists(), f"Missing {F3_PATH}"

    f1_text = F1_PATH.read_text(encoding="utf-8")
    f2_text = F2_PATH.read_text(encoding="utf-8")
    f3_data = json.loads(F3_PATH.read_text(encoding="utf-8"))

    return f1_text, f2_text, f3_data


def test_output_1_deterministic_gate_and_hygiene(triad_artifacts):
    f1_text, _, _ = triad_artifacts
    res = run_qa_gate_1(f1_text)
    assert res.get("passed") is True, f"Gate 1 failed: {res.get('failures')}"

    # YouTube hallucination guard in spoken dialogue body
    dialogue_body = f1_text.split("# Ambiguity Lock Register")[0].lower()
    banned = ["watching", "subscribe", "notification", "bell icon", "like and share"]
    for phrase in banned:
        assert phrase not in dialogue_body, f"Found hallucinated phrase in dialogue: {phrase}"


def test_output_2_deterministic_gate_and_structure(triad_artifacts):
    _, f2_text, _ = triad_artifacts
    meta = {"duration_minutes": 49, "archetype": "ARCHITECTURE_DEEP_DIVE"}
    res = run_qa_gate_2(f2_text, meta)
    assert res.get("passed") is True, f"Gate 2 failed: {res.get('failures')}"
    assert not res.get("failures"), f"Unexpected failures: {res.get('failures')}"


def test_output_3_deterministic_gate_and_ontological_integrity(triad_artifacts):
    _, _, f3_data = triad_artifacts
    res = run_qa_gate_3(f3_data)
    assert res.get("passed") is True, f"Gate 3 failed: {res.get('failures')}"

    # Verify ev-15
    assert "ev-15" in f3_data.get("evidence_index", {}), "ev-15 missing from evidence_index"

    # Verify system:fns3
    entity_ids = {e.get("id") for e in f3_data.get("graph_mutations", {}).get("entity_upserts", [])}
    assert "system:fns3" in entity_ids, "system:fns3 missing from entity_upserts"

    # Verify relationship signal description
    rels = f3_data.get("graph_mutations", {}).get("relationship_signals", [])
    depends_rel = next((r for r in rels if r.get("signal_id") == "rel-dual-llm-pipeline--mobile-capture-app--DEPENDS_ON"), None)
    assert depends_rel is not None, "Missing depends_on relationship"
    assert "Field Asset Capture Application" in depends_rel.get("description", "")


def test_cross_artifact_consistency(triad_artifacts):
    f1_text, f2_text, f3_data = triad_artifacts
    meta = {"duration_minutes": 49, "archetype": "ARCHITECTURE_DEEP_DIVE"}

    q1 = run_qa_gate_1(f1_text)
    q2 = run_qa_gate_2(f2_text, meta)
    q3 = run_qa_gate_3(f3_data)

    f3_str = json.dumps(f3_data)
    c_res = run_cross_artifact_consistency(q1, q2, q3, f1_text=f1_text, f2_text=f2_text, f3_text=f3_str, metadata=meta)
    assert c_res.get("passed") is True, f"Cross-artifact consistency failed: {c_res.get('failures')}"


def test_governance_receipts_integrity():
    assert QA_REPORT_PATH.exists(), "qa_gates_report.json missing"
    assert RELEASE_DECISION_PATH.exists(), "final_release_decision.json missing"

    qa_report = json.loads(QA_REPORT_PATH.read_text(encoding="utf-8"))
    assert qa_report.get("overall_passed") is True

    release_dec = json.loads(RELEASE_DECISION_PATH.read_text(encoding="utf-8"))
    assert release_dec.get("status") == "PASS"
    assert release_dec.get("final_pass") is True

    # Check matching sha256
    manifest = release_dec.get("artifact_manifest", {})
    for path, key in [(F1_PATH, "output_1_sha256"), (F2_PATH, "output_2_sha256"), (F3_PATH, "output_3_sha256")]:
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        actual = manifest.get(key)
        assert actual == expected, f"Digest mismatch for {path.name}: expected {expected}, got {actual}"
