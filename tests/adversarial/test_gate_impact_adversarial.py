"""
Adversarial and boundary tests for Subsystem Impact Gater.
Tests edge-case paths, Windows separators, hidden files, and scaling limits.
"""

from __future__ import annotations

from tools.gate_impact_core import (
    GateLayer,
    classify_diff_impact,
)
from tools.gate_impact_core.subsystem_mapper import classify_file_impact


def test_adversarial_empty_diff():
    rec = classify_diff_impact([])
    assert rec.layer == GateLayer.DOCS
    assert rec.file_count == 0


def test_adversarial_windows_paths():
    # Windows backslash paths should be normalized
    path = "output\\internal\\2026-09-25\\transcript.md"
    assert classify_file_impact(path) == GateLayer.CLIENT_DELIVERABLES


def test_adversarial_hidden_and_security_paths():
    # Hidden configs and root files should fail safe to CORE_ENGINE
    assert classify_file_impact(".env") == GateLayer.CORE_ENGINE
    assert classify_file_impact("secret.key") == GateLayer.CORE_ENGINE
    assert classify_file_impact(".git/config") == GateLayer.CORE_ENGINE


def test_adversarial_path_traversal():
    assert classify_file_impact("../../etc/passwd") == GateLayer.CORE_ENGINE


def test_adversarial_massive_path_classification_perf():
    paths = [f"output/proj/file_{i}.json" for i in range(5000)]
    rec = classify_diff_impact(paths)
    assert rec.layer == GateLayer.CLIENT_DELIVERABLES
    assert rec.file_count == 5000
    assert rec.bypasses_bge_m3
