"""
tests/end_to_end/test_copilot_wave_9_golden.py

Wave 9 Golden dataset and isolation gate qualification for Live Copilot under AGENTS.md.
Enforces strata representation (grounded, abstain, ownership traps, commitment, c19, cross-project)
and hermetic transcript isolation.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from dual_pipeline.query_core.firewall import ProhibitedCommandGuard
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer


def test_wave_9_golden_t1_gates():
    """Verify golden dataset strata completeness, schema conformance, and transcript isolation."""
    golden_path = Path(__file__).resolve().parents[1] / "evals" / "copilot_golden" / "v1" / "golden_cases.jsonl"
    assert golden_path.exists(), f"Golden dataset missing at {golden_path}"

    lines = [line.strip() for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 60, f"Expected 60 golden cases, found {len(lines)}"

    strata_counts = Counter()
    for raw in lines:
        case = json.loads(raw)
        assert "id" in case and case["id"], f"Case missing valid id: {case}"
        assert "strata" in case and case["strata"], f"Case missing valid strata: {case}"
        assert "prompt" in case, f"Case missing prompt: {case}"
        strata_counts[case["strata"]] += 1

    expected_strata = {
        "grounded": 25,
        "abstain": 10,
        "ownership_traps": 8,
        "commitment": 6,
        "c19": 5,
        "cross_project": 6,
    }
    assert dict(strata_counts) == expected_strata, f"Strata mismatch: {strata_counts} vs {expected_strata}"

    # Isolation invariant: Live transcript files must never be directly read by inspection tools
    prohibited_cmd = "cat .worktrees/feat_live/meeting_live_transcript.md"
    assert ProhibitedCommandGuard.is_prohibited(prohibited_cmd)

    # C-19 egress firewall invariant
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_messages([{"content": "Trajectory $1.2M secret"}])

