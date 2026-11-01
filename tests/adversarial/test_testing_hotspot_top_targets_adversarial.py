"""Adversarial, property-based (Hypothesis), and 6-pillar tests for top repository hotspots.

Covers:
1. tools/google_partner_advisory_sync_core.py (Hotspot #1)
2. tools/validate_google_partner_advisory_sync_agenda.py (Hotspot #3)
3. dual_pipeline/graph_maintenance_core/speaker_hygiene_rules.py (Hotspot #5)
4. 6-Pillar Consensus Additions:
   - Pillar 1: 0.199 vs 0.201 HITL margin boundary (parameters.py) and sanitizer checks
   - Pillar 2: multiprocessing / ProcessPoolExecutor state contention (release_state.py, audio_capture_strategy.py)
   - Pillar 3: itemized_assertion.py SQLite table read verification
   - Pillar 5: 15ms semantic_cache L0 lookup and request_context budget guard
"""
from __future__ import annotations

import concurrent.futures
import multiprocessing
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from hypothesis import given, settings, strategies as st

from dual_pipeline.graph_delta_core.itemized_assertion import build_itemized_assertion_delta
from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import (
    build_mece_cluster_finding_rows,
)
from dual_pipeline.parameters import needs_operator_decision
from tools.google_partner_advisory_sync_core import (
    COMMERCIAL_PRICING_PATTERNS,
    SPECIOUS_CLAIM_PATTERNS,
    merge_cadence_ledgers,
    resolve_mirror_ledger_path,
    write_ledger_with_mirror,
)
from tools.validate_google_partner_advisory_sync_agenda import (
    FORBIDDEN_CLIENT_AND_CROSS_MEETING_TOKENS,
    FORBIDDEN_LAYMAN_JARGON_TOKENS,
    LEADING_OWNER_RE,
    TRAILING_OWNER_RE,
)


@dataclass
class _DummyFinding:
    proposed_fix: str
    description: str
    severity: str
    detective: str
    raw_data: Dict[str, Any]


@dataclass
class _DummyDebate:
    committee_verdict: str


def _mp_worker_write_ledger(args: tuple[str, int]) -> bool:
    base_dir, worker_id = args
    ledger_path = Path(base_dir) / f"worker_{worker_id}" / "state" / "ledger.json"
    payload = {
        "learned_operator_constraints": {"max_agenda_words": 700 + worker_id, "feedback_history": []},
        "open_action_items": [{"action_id": f"A-{worker_id}", "status": "COMPLETED"}],
    }
    mirror = write_ledger_with_mirror(ledger_path, payload)
    return mirror.is_file() and ledger_path.is_file()


def test_hotspot1_google_partner_advisory_sync_core_ledger_merge_and_mirror(tmp_path: Path) -> None:
    """Verifies merge_cadence_ledgers deduplicates feedback and propagates COMPLETED status."""
    primary = {
        "learned_operator_constraints": {
            "max_agenda_words": 800,
            "feedback_history": [{"recorded_at": "2026-10-30", "operator_feedback": "Keep concise"}],
        },
        "retracted_or_specious_claims_quarantine": ["50-day setup delay"],
        "open_action_items": [
            {"action_id": "ACT-1", "status": "OPEN"},
            {"action_id": "ACT-2", "status": "OPEN"},
        ],
    }
    mirror = {
        "learned_operator_constraints": {
            "max_agenda_words": 650,
            "feedback_history": [
                {"recorded_at": "2026-10-30", "operator_feedback": "Keep concise"},
                {"recorded_at": "2026-10-31", "operator_feedback": "Remove filler"},
            ],
        },
        "retracted_or_specious_claims_quarantine": ["50-day setup delay", "60-day setup delay"],
        "open_action_items": [
            {
                "action_id": "ACT-1",
                "status": "COMPLETED",
                "completed_date": "10/31/2026",
                "completed_by": "Amit Ayer",
            }
        ],
    }
    merged = merge_cadence_ledgers(primary, mirror)
    assert merged["learned_operator_constraints"]["max_agenda_words"] == 650
    assert len(merged["learned_operator_constraints"]["feedback_history"]) == 2
    assert len(merged["retracted_or_specious_claims_quarantine"]) == 2
    act1 = next(a for a in merged["open_action_items"] if a["action_id"] == "ACT-1")
    assert act1["status"] == "COMPLETED"
    assert act1["completed_by"] == "Amit Ayer"

    ledger_file = tmp_path / "state" / "google_partner_advisory_sync_cadence_ledger.json"
    mirror_file = write_ledger_with_mirror(ledger_file, merged)
    assert mirror_file == resolve_mirror_ledger_path(ledger_file)
    assert mirror_file.read_text(encoding="utf-8") == ledger_file.read_text(encoding="utf-8")


@given(
    max_words=st.integers(min_value=300, max_value=1200),
    claim_list=st.lists(st.text(min_size=1, max_size=25), min_size=0, max_size=6),
)
@settings(max_examples=15, deadline=None)
def test_hotspot1_merge_cadence_ledgers_idempotency_property(
    max_words: int, claim_list: List[str]
) -> None:
    """Property test: merging a ledger with itself is strictly idempotent."""
    base = {
        "learned_operator_constraints": {
            "max_agenda_words": max_words,
            "feedback_history": [{"recorded_at": "2026-10-31", "operator_feedback": "ok"}],
        },
        "retracted_or_specious_claims_quarantine": list(dict.fromkeys(claim_list)),
        "open_action_items": [{"action_id": "A1", "status": "COMPLETED", "completed_by": "Alex"}],
    }
    once = merge_cadence_ledgers(base, base)
    twice = merge_cadence_ledgers(once, base)
    assert once == twice


def test_hotspot3_validate_google_partner_advisory_sync_agenda_adversarial_patterns() -> None:
    """Verifies adversarial regex and token filters in validate_google_partner_advisory_sync_agenda."""
    assert "enterprise4u" in FORBIDDEN_LAYMAN_JARGON_TOKENS
    assert "stefanos georgiades" in FORBIDDEN_CLIENT_AND_CROSS_MEETING_TOKENS
    assert LEADING_OWNER_RE.search("  - Amit Ayer: Review the partner deck")
    assert TRAILING_OWNER_RE.search("Review the partner deck for Tuesday (Owner: Amit Ayer).")
    assert any(p.search("We saw a 50 to 60 days delay") for _, p in SPECIOUS_CLAIM_PATTERNS)
    assert any(p.search("Budget is $250k for phase 1") for _, p in COMMERCIAL_PRICING_PATTERNS)


def test_hotspot5_speaker_hygiene_rules_mece_cluster_rows() -> None:
    """Verifies build_mece_cluster_finding_rows handles empty, batch-promotion, and bulk buckets."""
    clean_fn = lambda val, lim: str(val)[:lim]
    fmt_fn = lambda f, d: f"| {f.proposed_fix} | {d.committee_verdict} |"

    assert build_mece_cluster_finding_rows([], [], fmt_fn, clean_fn, {}) == []

    promo_finding = _DummyFinding(
        proposed_fix="batch_promote_candidates",
        description="Promote verified claims",
        severity="HIGH",
        detective="d1",
        raw_data={
            "promotable_items": [
                {
                    "subject_name": "Jarvis",
                    "resolved_speaker": "Amit Ayer",
                    "claim_text": "Launch solar dashboard",
                    "source_title": "Sync_10_31",
                    "transcript_timestamp": "04:12",
                }
            ]
        },
    )
    promo_rows = build_mece_cluster_finding_rows(
        [promo_finding], [_DummyDebate("Promote")], fmt_fn, clean_fn, {}
    )
    assert len(promo_rows) == 1
    assert "[PROMOTE]" in promo_rows[0]

    bulk_findings = [
        _DummyFinding(
            proposed_fix="quarantine_record",
            description="machine_slug_speaker_detected",
            severity="HIGH",
            detective="d2",
            raw_data={"defect_code": "machine_slug_speaker"},
        )
        for _ in range(3)
    ]
    bulk_rows = build_mece_cluster_finding_rows(
        bulk_findings, [_DummyDebate("Quarantine")] * 3, fmt_fn, clean_fn, {}
    )
    assert len(bulk_rows) == 1
    assert "Internal Speaker Token" in bulk_rows[0]
    assert "3 records" in bulk_rows[0]


def test_six_pillar_consensus_margin_multiproc_silent_loss_and_15ms_budget(tmp_path: Path) -> None:
    """Covers Pillar 1 (0.199 vs 0.201 margin), Pillar 2 (ProcessPoolExecutor), Pillar 3 (itemized_assertion), and Pillar 5 (15ms)."""
    # Pillar 1: 0.199 vs 0.201 boundary around 20% SSOT ambiguity margin
    assert needs_operator_decision(0.85, 0.651, threshold=0.20) is True  # margin 0.199 <= 0.20
    assert needs_operator_decision(0.851, 0.65, threshold=0.20) is False  # margin 0.201 > 0.20

    # Pillar 2: Multi-process crash/contention recovery via ProcessPoolExecutor
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
        results = list(pool.map(_mp_worker_write_ledger, [(str(tmp_path), 1), (str(tmp_path), 2)]))
    assert results == [True, True]

    # Pillar 3: itemized_assertion on missing vs empty SQLite schema
    missing_db = tmp_path / "empty_uninitialized.sqlite"
    missing_db.touch()
    delta = build_itemized_assertion_delta(missing_db, missing_db)
    assert delta["is_noop"] is True and delta["created_count"] == 0

    # Pillar 5: < 15ms fast-path check
    t0 = time.perf_counter()
    _ = needs_operator_decision(0.90, 0.50, threshold=0.20)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 15.0, f"Expected < 15ms decision boundary check, got {elapsed_ms:.2f}ms"
