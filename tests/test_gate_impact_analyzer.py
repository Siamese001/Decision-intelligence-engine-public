"""
Unit tests for subsystem impact analyzer.
"""

from __future__ import annotations

from tools.gate_impact_core import (
    GateLayer,
    classify_diff_impact,
)
from tools.gate_impact_core.subsystem_mapper import classify_file_impact


def test_classify_file_impact_docs():
    assert classify_file_impact("docs/plan.md") == GateLayer.DOCS
    assert classify_file_impact("README.md") == GateLayer.DOCS
    assert classify_file_impact("walkthrough.md") == GateLayer.DOCS


def test_classify_file_impact_client_deliverables():
    assert classify_file_impact("output/internal/01_rec.md") == GateLayer.CLIENT_DELIVERABLES
    assert classify_file_impact("output/nexus/03_signal.json") == GateLayer.CLIENT_DELIVERABLES


def test_classify_file_impact_developer_tools():
    assert classify_file_impact("tools/my_script.py") == GateLayer.DEVELOPER_TOOLS
    assert classify_file_impact("scripts/run.sh") == GateLayer.DEVELOPER_TOOLS
    assert classify_file_impact("tests/test_unit.py") == GateLayer.DEVELOPER_TOOLS


def test_classify_file_impact_core_engine():
    assert classify_file_impact("dual_pipeline/engine.py") == GateLayer.CORE_ENGINE
    assert classify_file_impact("pipeline_a_intake/audio.py") == GateLayer.CORE_ENGINE
    assert classify_file_impact("storage/graph.py") == GateLayer.CORE_ENGINE
    assert classify_file_impact("config/credentials.py") == GateLayer.CORE_ENGINE


def test_classify_diff_impact_escalation():
    # If a diff contains both docs and core engine, it escalates to CORE_ENGINE
    files = ["docs/readme.md", "dual_pipeline/canonical_pipeline/pipeline.py"]
    rec = classify_diff_impact(files)
    assert rec.layer == GateLayer.CORE_ENGINE
    assert rec.profile_name == "full"
    assert not rec.bypasses_bge_m3
