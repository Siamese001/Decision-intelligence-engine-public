"""End-to-end verification of LiveProactiveInflectionAgent, Speak-Gate Compression, and TemporalDriftSupersessionAgent."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.proactive_inflection_agent import (
    LiveProactiveInflectionAgent,
    compress_for_speak_gate,
)
from dual_pipeline.maintenance_swarm import MaintenanceSubAgent
from dual_pipeline.staleness_core.temporal_drift_agent import (
    TemporalDriftSupersessionAgent,
)


def test_live_proactive_inflection_agent_stages_speakable_insight_e2e() -> None:
    """Verifies that in-memory live turns contradicting historical assertions stage <=35-word insights."""
    agent = LiveProactiveInflectionAgent(max_steps=2, max_tool_calls=3, max_words=35, min_confidence=0.85)
    turns = [
        {
            "speaker": "Elena Rostova",
            "text": "We need to delay the Billing_Gateway migration until November due to schema testing.",
            "confidence": 0.94,
            "timestamp": "10:14:00",
        }
    ]
    historical_assertions = [
        {
            "assertion_id": "asrt-hist-201",
            "subject_id": "Billing_Gateway",
            "predicate": "HAS_MILESTONE",
            "claim_text": "Billing_Gateway migration is committed for October delivery.",
            "source_id": "src:meeting:09_15",
            "namespace": "CANONICAL",
            "is_quarantined": 0,
        }
    ]

    result = agent.evaluate_live_window(turns=turns, historical_assertions=historical_assertions)

    assert result.status == "STAGED"
    assert len(result.insights) == 1
    insight = result.insights[0]
    assert insight.inflection_type == "CONTRADICTION"
    assert insight.speaker == "Elena Rostova"
    assert insight.word_count <= 35
    assert insight.c19_clean is True
    assert "asrt-hist-201" in insight.cited_assertion_ids
    assert result.steps_executed == 2
    assert result.terminal_outcome == "COMPLETED"


def test_compress_for_speak_gate_reduces_verbose_line_within_word_budget_e2e() -> None:
    """Verifies bounded 2-step compression preserves entity tokens while enforcing <=35 words."""
    verbose_line = (
        "In order to ensure alignment, based on the historical record, "
        "Elena Rostova confirmed that Billing_Gateway migration faces a two-week delay "
        "(which was previously discussed during the architecture pre-brief session) "
        "and requires immediate test environment provisioning across all staging clusters before Friday."
    )
    assert len(verbose_line.split()) > 35
    compressed = compress_for_speak_gate(verbose_line, max_words=28, max_steps=2)
    assert 0 < len(compressed.split()) <= 28
    assert "Elena Rostova" in compressed
    assert "Billing_Gateway" in compressed


def test_temporal_drift_agent_and_maintenance_swarm_distinguish_supersession_from_contradiction_e2e(
    tmp_path: Path,
) -> None:
    """Verifies chronological supersession vs concurrent contradiction over read-only SQLite."""
    db_file = tmp_path / "temporal_graph.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            """
            CREATE TABLE assertions (
                assertion_id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_id TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                valid_from TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO assertions VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("asrt-q1", "Project_Orion", "HAS_STATUS", "AMBER", "Project_Orion status is AMBER.", "2026-01-15"),
                ("asrt-q2", "Project_Orion", "HAS_STATUS", "GREEN", "Project_Orion status is GREEN.", "2026-03-20"),
                ("asrt-c1", "Service_Auth", "OWNED_BY", "Team_Alpha", "Service_Auth owned by Team_Alpha.", "2026-03-20"),
                ("asrt-c2", "Service_Auth", "OWNED_BY", "Team_Beta", "Service_Auth owned by Team_Beta.", "2026-03-20"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    before_sha = hashlib.sha256(db_file.read_bytes()).hexdigest()
    td_agent = TemporalDriftSupersessionAgent(max_steps=2, max_tool_calls=4, min_supersession_days=1)
    scan_res = td_agent.scan_temporal_drift(db_path=db_file)
    after_sha = hashlib.sha256(db_file.read_bytes()).hexdigest()

    assert before_sha == after_sha
    assert scan_res.db_sha256_unchanged is True
    assert scan_res.supersession_count == 1
    assert scan_res.concurrent_contradiction_count == 1
    assert all(p.approval_required is True and p.auto_apply is False for p in scan_res.proposals)

    sup_prop = next(p for p in scan_res.proposals if p.subject_id == "Project_Orion")
    assert sup_prop.action == "supersede_assertion"
    assert sup_prop.newer_assertion_id == "asrt-q2"
    assert sup_prop.older_assertion_id == "asrt-q1"

    con_prop = next(p for p in scan_res.proposals if p.subject_id == "Service_Auth")
    assert con_prop.action == "escalate_concurrent_contradiction"

    maint = MaintenanceSubAgent("maint_temporal_test", db_path=db_file)
    maint.perform_bounded_scan(max_cycles=1, db_path=db_file)
    assert len(maint.temporal_drift_proposals) == 2
