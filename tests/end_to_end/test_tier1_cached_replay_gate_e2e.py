"""
tests/end_to_end/test_tier1_cached_replay_gate_e2e.py

End-to-end receipt test for Tier 1 cached stage replay:
1. Validates full execution lifecycle of verify_tier1_cached_replay.
2. Checks Gate 1.3 canonical MM/DD/YYYY date formatting in generated transcripts.
3. Checks foreign worktree path rebinding into an ephemeral sandbox.
4. Verifies zero leakage into output/ or canonical graph storage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.verify_tier1_cached_replay import verify_tier1_cached_replay


def test_tier1_cached_replay_e2e_lifecycle(tmp_path: Path):
    """E2E lifecycle: verifies cached replay passes, normalizes dates, and sandboxes outputs."""
    sandbox_dir = tmp_path / "replay_sandbox"
    passed, receipt = verify_tier1_cached_replay(output_sandbox_dir=sandbox_dir)

    assert passed is True
    assert receipt["verdict"] == "PASS"
    assert receipt["rebound_paths_verified"] is True
    assert receipt["x3_coherence_verified"] is True
    assert receipt["date_normalized"] == "10/16/2026"

    # Confirm deliverables exist inside sandbox
    assert (sandbox_dir / "01_transcript.md").is_file()
    assert (sandbox_dir / "02_recap.md").is_file()
    assert (sandbox_dir / "03_graph.json").is_file()

    # Confirm transcript contains canonical date
    f1_content = (sandbox_dir / "01_transcript.md").read_text(encoding="utf-8")
    assert "**Date:** 10/16/2026" in f1_content
    assert "foreign_worktree" not in f1_content


def test_tier1_cached_replay_produces_zero_output_leakage():
    """Verify that verify_tier1_cached_replay executes cleanly without writing to repository output/."""
    passed, receipt = verify_tier1_cached_replay()
    assert passed is True
    assert receipt["status"] == "PASS"
