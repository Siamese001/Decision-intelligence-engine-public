"""Unit tests for staleness_core: models, two_track_classifier, and cascade_resolver."""
from __future__ import annotations

import sqlite3

from dual_pipeline.staleness_core.cascade_resolver import CascadeResolver
from dual_pipeline.staleness_core.models import StrategicIntentSignal
from dual_pipeline.staleness_core.two_track_classifier import TwoTrackClassifier


def test_strategic_intent_signal_serialization():
    sig = StrategicIntentSignal(
        signal_id="sig-001",
        target_topic_or_entity="workstream:ai_sdlc",
        posture="DEFERRED_OUT_OF_SCOPE",
        evidence_statement_ids=["stmt-01", "stmt-02"],
        rationale="AI SDLC standard is deferred in favor of full stack SWE staffing.",
        observed_at="08/27/2026",
    )
    data = sig.to_dict()
    assert data["signal_id"] == "sig-001"
    assert data["posture"] == "DEFERRED_OUT_OF_SCOPE"

    reconstructed = StrategicIntentSignal.from_dict(data)
    assert reconstructed.signal_id == sig.signal_id
    assert reconstructed.target_topic_or_entity == sig.target_topic_or_entity
    assert reconstructed.evidence_statement_ids == ["stmt-01", "stmt-02"]


def test_two_track_classifier_explicit_compliance():
    classifier = TwoTrackClassifier(ambiguity_threshold=0.25)
    # Gordon VPC blocker must be Track B
    res = classifier.classify_item(
        item_id="v26_action_001_val_gordon_public_api_vpc",
        item_type="action",
        title_or_claim="Validate public API vs VPC incident",
        lane_or_predicate="P0 closure",
        priority="P0",
    )
    assert res.track == "TRACK_B_COMPLIANCE"
    assert res.hitl_required is False
    assert res.margin_delta >= 0.25

    # ETRM pricing must be Track B
    res_etrm = classifier.classify_item(
        item_id="action_validation_v23_etrm_pricing",
        item_type="action",
        title_or_claim="Validate pricing workbook fees and invoices",
        lane_or_predicate="Commercials",
        priority="P0",
    )
    assert res_etrm.track == "TRACK_B_COMPLIANCE"


def test_two_track_classifier_strategic_focus():
    classifier = TwoTrackClassifier(ambiguity_threshold=0.25)
    # 106-SWE decomposition must be Track A
    res_swe = classifier.classify_item(
        item_id="validation_v35_q3_106_swe_decomposition_p1",
        item_type="action",
        title_or_claim="Close Q3 EL map and 106-SWE decomposition",
        lane_or_predicate="Operating model",
        priority="P1",
    )
    assert res_swe.track == "TRACK_A_STRATEGIC"
    assert res_swe.margin_delta >= 0.25

    # AI SDLC minimum standard must be Track A
    res_sdlc = classifier.classify_item(
        item_id="val_v20_agentic_control_standard",
        item_type="action",
        title_or_claim="Validate AI SDLC and agentic control minimum standard",
        lane_or_predicate="Governance controls",
        priority="P1",
    )
    assert res_sdlc.track == "TRACK_A_STRATEGIC"


def test_cascade_resolver_sqlite_mutation():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    # Create test tables
    cursor.execute(
        """CREATE TABLE p0_p1_action_register (
            action_id TEXT PRIMARY KEY,
            source_validation_id TEXT,
            action_title TEXT,
            lane TEXT,
            priority TEXT,
            supporting_context TEXT,
            status TEXT
        )"""
    )
    cursor.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            validation_priority TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            valid_to TEXT,
            superseded_at TEXT
        )"""
    )
    cursor.execute(
        """CREATE TABLE dan_next_best_action (
            action_rank INTEGER PRIMARY KEY,
            action TEXT,
            owner TEXT,
            why_it_matters TEXT,
            linked_validation_ids TEXT
        )"""
    )

    # Insert test rows
    cursor.execute(
        """INSERT INTO p0_p1_action_register VALUES
        ('act_106_swe', 'val_106', 'Close 106-SWE decomposition', 'Operating model', 'P1', 'ctx', 'open'),
        ('act_ai_sdlc', 'val_sdlc', 'Validate AI SDLC standard', 'Governance', 'P1', 'ctx', 'open'),
        ('act_gordon_vpc', 'val_vpc', 'Validate Gordon public API vs VPC', 'P0 closure', 'P0', 'ctx', 'open')"""
    )
    cursor.execute(
        """INSERT INTO assertion VALUES
        ('ass_1', 'node_1', 'GOVERNS', '106-SWE breakdown', 'P1', 'current', 1, NULL, NULL),
        ('ass_2', 'node_2', 'GOVERNS', 'AI SDLC controls', 'P1', 'current', 1, NULL, NULL),
        ('ass_3', 'node_3', 'SECURITY', 'VPC lock incident', 'P0', 'current', 1, NULL, NULL)"""
    )
    cursor.execute(
        """INSERT INTO dan_next_best_action VALUES
        (1, 'Forum cadence', 'Dan', 'why 1', 'val_1'),
        (2, 'Resolve Gordon VPC', 'Dan', 'why 2', 'val_vpc'),
        (3, 'Close 106-SWE decomposition', 'Dan', 'why 3', 'val_106'),
        (4, 'Validate AI SDLC standard', 'Andrew', 'why 4', 'val_sdlc'),
        (5, 'Rewire product inventory', 'Dan', 'why 5', 'val_5')"""
    )
    conn.commit()

    resolver = CascadeResolver(dry_run=False)
    signals = [
        StrategicIntentSignal(
            signal_id="sig-106",
            target_topic_or_entity="initiative:106_swe",
            posture="COMPLETED_IN_READOUT",
            evidence_statement_ids=["stmt-01"],
            rationale="106-SWE provided in readout.",
            observed_at="08/27/2026",
        ),
        StrategicIntentSignal(
            signal_id="sig-sdlc",
            target_topic_or_entity="workstream:ai_sdlc",
            posture="DEFERRED_OUT_OF_SCOPE",
            evidence_statement_ids=["stmt-02"],
            rationale="AI SDLC out of scope.",
            observed_at="08/27/2026",
        ),
    ]

    resolution = resolver.resolve_and_apply(
        conn=conn,
        signals=signals,
        directive_id="test-dir",
        meeting_id="meeting-chakra-0827",
        meeting_date_iso="2026-08-27",
    )
    conn.commit()

    # Verify actions
    assert "act_106_swe" in resolution.closed_actions
    assert "act_ai_sdlc" in resolution.closed_actions
    assert "act_gordon_vpc" not in resolution.closed_actions

    # Check database state
    cursor.execute("SELECT action_id, status FROM p0_p1_action_register")
    action_statuses = dict(cursor.fetchall())
    assert action_statuses["act_106_swe"] == "closed / superseded_by_strategic_readout"
    assert action_statuses["act_ai_sdlc"] == "closed / superseded_by_strategic_readout"
    assert action_statuses["act_gordon_vpc"] == "open"

    # Check assertions state
    cursor.execute("SELECT assertion_id, currentness_state, retrieval_eligible, valid_to FROM assertion")
    assert_data = {r[0]: (r[1], r[2], r[3]) for r in cursor.fetchall()}
    assert assert_data["ass_1"] == ("historical", 0, "2026-08-27")
    assert assert_data["ass_2"] == ("historical", 0, "2026-08-27")
    assert assert_data["ass_3"] == ("current", 1, None)

    # Check dan_next_best_action: Rank 3 and 4 should be pruned, remaining re-ranked 1, 2, 3
    cursor.execute("SELECT action_rank, action FROM dan_next_best_action ORDER BY action_rank")
    dan_remaining = cursor.fetchall()
    assert len(dan_remaining) == 3
    assert dan_remaining[0] == (1, "Forum cadence")
    assert dan_remaining[1] == (2, "Resolve Gordon VPC")
    assert dan_remaining[2] == (3, "Rewire product inventory")


def test_cascade_resolver_account_team_actions():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE account_team_next_best_action (
            action_rank INTEGER PRIMARY KEY,
            action TEXT,
            owner TEXT,
            why_it_matters TEXT,
            linked_validation_ids TEXT
        )"""
    )
    cursor.execute(
        """INSERT INTO account_team_next_best_action VALUES
        (1, 'Close Gemini scope', 'Amit', 'why 1', 'val_1'),
        (2, 'Validate AI SDLC standard', 'Andrew', 'why 2', 'val_sdlc'),
        (3, 'Recurring cadence with Speaker_Alpha', 'Amit', 'why 3', 'val_3')"""
    )
    conn.commit()

    resolver = CascadeResolver(dry_run=False)
    signals = [
        StrategicIntentSignal(
            signal_id="sig-sdlc",
            target_topic_or_entity="workstream:ai_sdlc",
            posture="DEFERRED_OUT_OF_SCOPE",
            evidence_statement_ids=[],
            rationale="AI SDLC out of scope.",
            observed_at="08/27/2026",
        )
    ]
    resolver.resolve_and_apply(conn, signals)
    conn.commit()

    cursor.execute("SELECT action_rank, action FROM account_team_next_best_action ORDER BY action_rank")
    remaining = cursor.fetchall()
    assert len(remaining) == 2
    assert remaining[0] == (1, "Close Gemini scope")
    assert remaining[1] == (2, "Recurring cadence with Speaker_Alpha")


def test_action_ttl_sweeper():
    from dual_pipeline.staleness_core.ttl_sweeper import ActionTTLSweeper

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE p0_p1_action_register (
            action_id TEXT PRIMARY KEY,
            action_title TEXT,
            priority TEXT,
            lane TEXT,
            due_date TEXT,
            status TEXT
        )"""
    )
    cursor.execute(
        """INSERT INTO p0_p1_action_register VALUES
        ('act_old_strategic', 'Ancient strategic note', 'P1', 'Strategy', '2026-06-01', 'open'),
        ('act_fresh_strategic', 'Fresh strategic note', 'P1', 'Strategy', '2026-09-20', 'open'),
        ('act_old_compliance', 'Old VPC incident', 'P0', 'Compliance', '2026-06-01', 'open')"""
    )
    conn.commit()

    sweeper = ActionTTLSweeper(ttl_days=30, dry_run=False)
    res = sweeper.sweep_action_register(conn, as_of_date="2026-09-23T00:00:00Z")
    conn.commit()

    assert res["status"] == "PASS"
    assert res["swept_count"] == 1
    assert res["swept_actions"][0]["action_id"] == "act_old_strategic"

    cursor.execute("SELECT action_id, status FROM p0_p1_action_register")
    statuses = dict(cursor.fetchall())
    assert statuses["act_old_strategic"] == "closed / stale_ttl_expired"
    assert statuses["act_fresh_strategic"] == "open"
    assert statuses["act_old_compliance"] == "open"  # P0 compliance never auto-swept


def test_intent_extractor_from_output_3():
    from dual_pipeline.staleness_core.intent_extractor import IntentExtractor

    output_3_payload = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "graph_mutations": {
            "strategic_intent_signals": [
                {
                    "signal_id": "sig-01",
                    "target_topic_or_entity": "initiative:106_swe",
                    "posture": "COMPLETED_IN_READOUT",
                    "evidence_ids": ["stmt-01"],
                    "rationale": "Decomposition delivered.",
                }
            ]
        },
    }
    signals = IntentExtractor.extract_from_output_3(output_3_payload)
    assert len(signals) == 1
    assert signals[0].target_topic_or_entity == "initiative:106_swe"
    assert signals[0].posture == "COMPLETED_IN_READOUT"

