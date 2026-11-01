"""
End-to-End Verification for Phase 5:
1. RETRIEVAL_PARAMETER_BOUNDS enforcement for contradiction & entity_anchor route multipliers
2. Weekly Energy & Utilities Market Trends Briefing (10-28-2026) & Stage 0 Snapshot
3. Frontier LLM Research Digest (10-28-2026)
4. Look-Around-the-Corner Blind-Spot Radar Brief (10-28-2026)
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.core import ROOT
from dual_pipeline.learning_loop_core.models import RETRIEVAL_PARAMETER_BOUNDS
from dual_pipeline.learning_loop_core.simulation import (
    _check_simulation_bounds,
    _validate_bounds,
)


def test_phase5_contradiction_and_entity_anchor_bounds_e2e() -> None:
    """Verifies RETRIEVAL_PARAMETER_BOUNDS governs contradiction and entity_anchor route multipliers."""
    assert "retrieval.route_multiplier.contradiction" in RETRIEVAL_PARAMETER_BOUNDS
    assert "retrieval.route_multiplier.entity_anchor" in RETRIEVAL_PARAMETER_BOUNDS

    ok_in, msg_in = _validate_bounds("retrieval.route_multiplier.contradiction", 0.55)
    assert ok_in is True
    assert msg_in == "VALID"

    ok_out, msg_out = _validate_bounds("retrieval.route_multiplier.contradiction", 0.95)
    assert ok_out is False
    assert "outside governed bounds [0.2, 0.7]" in msg_out

    sim_fail = _check_simulation_bounds(
        "retrieval.route_multiplier.entity_anchor", 0.10, "prop-phase5-bounds", "2026-10-28T11:50:00Z"
    )
    assert sim_fail is not None
    assert sim_fail.success is False
    assert sim_fail.status == "SIMULATED_FAIL"


def test_phase5_external_intelligence_and_foresight_deliverables_e2e() -> None:
    """Verifies Stage 0 snapshot, Weekly Market Update, Frontier LLM Digest, and Corner Foresight Radar."""
    snap_path = ROOT / "reports" / "weekly_market_intel" / "pre_snapshot_10-28-2026.json"
    weekly_path = ROOT / "documents" / "market_intelligence" / "weekly_update_10-28-2026.md"
    digest_path = ROOT / "reports" / "frontier_llm_scout" / "digest_10-28-2026.md"
    radar_path = ROOT / "reports" / "corner_foresight" / "radar_10-28-2026.md"

    assert snap_path.is_file()
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap["snapshot_id"] == "pre-weekly-10-28-2026"
    assert len(snap["table_counts_14"]) == 14
    assert snap["grounding_ratio"] == 1.0

    assert weekly_path.is_file()
    weekly_txt = weekly_path.read_text(encoding="utf-8")
    assert "# Weekly Energy & Utilities Market Trends Update - 10/28/2026" in weekly_txt
    assert "OBSERVED_FACT" in weekly_txt
    assert "Strategic Frame 1:" in weekly_txt
    assert "Strategic Frame 2:" in weekly_txt
    assert "## 4. Source Provenance" in weekly_txt

    assert digest_path.is_file()
    digest_txt = digest_path.read_text(encoding="utf-8")
    for arxiv_id in ("2610.00238", "2610.00321", "2610.00010", "2610.00320"):
        assert arxiv_id in digest_txt
    assert "Plain-Language Analogy" in digest_txt
    assert "Practical Engineering Takeaways" in digest_txt

    assert radar_path.is_file()
    radar_txt = radar_path.read_text(encoding="utf-8")
    assert "Blind Spot 1:" in radar_txt
    assert "Blind Spot 5:" in radar_txt
    assert "Top 3 Unasked Questions" in radar_txt
    assert snap["graph_sha256"] in radar_txt
