"""End-to-end test suite for receptive watchlist injection and soft transition extraction.

Validates that injecting stale graph role watchlists enables the extraction pipeline
to capture role-transition cues (roll-offs, handovers, replacements) from incoming text.
"""

from __future__ import annotations

import pytest

from unified_pipeline.temporal_reconciliation.receptive_watchlist_injector import (
    ReceptiveWatchlistInjector,
    WatchlistEntry,
)
from unified_pipeline.temporal_reconciliation.staleness_auditor import (
    StalenessAuditReport,
    StalenessRecord,
)


@pytest.fixture
def mock_staleness_report() -> StalenessAuditReport:
    records = [
        StalenessRecord(
            edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
            from_node_id="person_abhijit_mahishi",
            from_node_name="Abhijit Mahishi",
            edge_type="OWNS_OR_LEADS",
            to_node_id="workstream_fcdb",
            to_node_name="FCDB",
            source_id="source_nea_status_2026_06_29",
            source_name="NEA Tech Lead Q2 Weekly Status Packet",
            source_date="2026-06-29",
            age_days=111,
            staleness_tier="P1",
            recommended_action="RETIRE_OR_REPLACE",
        ),
        StalenessRecord(
            edge_id="edge_v44_row_7_tech_lead_abhijit_mahishi",
            from_node_id="person_abhijit_mahishi",
            from_node_name="Abhijit Mahishi",
            edge_type="DATED_ROLE_IN",
            to_node_id="workstream_fcdb",
            to_node_name="FCDB",
            source_id="source_v44_nea_charter_roster_xlsx_2026_07_11",
            source_name="NEA Charter Roster",
            source_date="2026-07-11",
            age_days=99,
            staleness_tier="P1",
            recommended_action="RETIRE_OR_REPLACE",
        ),
    ]
    return StalenessAuditReport(
        reference_date="2026-10-18",
        db_path="backups/weekly_market_intel_10-14-2026/graph.sqlite",
        warning_ttl_days=45,
        critical_ttl_days=90,
        total_active_edges_scanned=10,
        mutable_edges_checked=5,
        stale_count_p1=2,
        stale_count_p2=0,
        records=records,
    )


def test_build_watchlist_and_prompt_formatting(mock_staleness_report: StalenessAuditReport) -> None:
    injector = ReceptiveWatchlistInjector()
    watchlist = injector.build_watchlist(mock_staleness_report, max_items=10)

    assert len(watchlist) == 2
    assert watchlist[0].entity_id == "person_abhijit_mahishi"
    assert watchlist[0].target_name == "FCDB"
    assert "Abhijit" in watchlist[0].aliases

    prompt_context = injector.format_prompt_context(watchlist)
    assert "TEMPORAL RECONCILIATION WATCHLIST" in prompt_context
    assert "Abhijit Mahishi" in prompt_context
    assert "FCDB" in prompt_context
    assert "edge_v36_person_abhijit_mahishi_leads_fcdb" in prompt_context


def test_extract_explicit_replacement_signal(mock_staleness_report: StalenessAuditReport) -> None:
    injector = ReceptiveWatchlistInjector()
    watchlist = injector.build_watchlist(mock_staleness_report)

    transcript = (
        "Good morning team. Quick staffing update: Bailey Morton has taken over for Abhijit Mahishi "
        "on FCDB starting this sprint."
    )
    signals = injector.extract_transition_signals(transcript, watchlist)

    assert len(signals) == 2
    superseded_ids = {s.supersedes_edge_id for s in signals}
    assert "edge_v36_person_abhijit_mahishi_leads_fcdb" in superseded_ids
    assert "edge_v44_row_7_tech_lead_abhijit_mahishi" in superseded_ids

    sig = next(s for s in signals if s.supersedes_edge_id == "edge_v36_person_abhijit_mahishi_leads_fcdb")
    assert sig.candidate_type == "REPLACEMENT"
    assert sig.retracted_entity_id == "person_abhijit_mahishi"
    assert sig.replacement_name == "Bailey Morton"
    assert sig.workstream_name == "FCDB"
    assert sig.confidence_score >= 0.95
    assert "Bailey Morton has taken over for Abhijit Mahishi" in sig.verbatim_quote


def test_extract_standalone_rolloff_signal(mock_staleness_report: StalenessAuditReport) -> None:
    injector = ReceptiveWatchlistInjector()
    watchlist = injector.build_watchlist(mock_staleness_report)

    transcript = (
        "Regarding the backend architecture, note that Abhijit is no longer on the FCDB team, "
        "so please route tickets to Lancey for now."
    )
    signals = injector.extract_transition_signals(transcript, watchlist)

    assert len(signals) == 2
    sig = next(s for s in signals if s.supersedes_edge_id == "edge_v36_person_abhijit_mahishi_leads_fcdb")
    assert sig.candidate_type == "RETRACTION"
    assert sig.retracted_entity_id == "person_abhijit_mahishi"
    assert sig.replacement_name is None
    assert sig.workstream_name == "FCDB"
    assert sig.confidence_score >= 0.90


def test_extract_former_role_cue_signal(mock_staleness_report: StalenessAuditReport) -> None:
    injector = ReceptiveWatchlistInjector()
    watchlist = injector.build_watchlist(mock_staleness_report)

    text = "We reviewed the technical docs authored by former tech lead Abhijit Mahishi on FCDB."
    signals = injector.extract_transition_signals(text, watchlist)

    assert len(signals) == 2
    assert all(s.candidate_type == "RETRACTION" for s in signals)
    assert all(s.detection_method == "FORMER_ROLE_CUE" for s in signals)


def test_negative_cases_unrelated_dialogue_yields_zero_signals(
    mock_staleness_report: StalenessAuditReport,
) -> None:
    injector = ReceptiveWatchlistInjector()
    watchlist = injector.build_watchlist(mock_staleness_report)

    text = "Jose Medina and Farrukh Khan discussed the Enterprise AI Products roadmap with Matt Barrows."
    signals = injector.extract_transition_signals(text, watchlist)
    assert len(signals) == 0

    assert injector.extract_transition_signals("", watchlist) == []
    assert injector.extract_transition_signals(text, []) == []
    assert injector.format_prompt_context([]) == ""
