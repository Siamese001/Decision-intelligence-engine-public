"""Wave 1 verification tests: CLI question ingress, multi-stakeholder routing, and unbounded word budget."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import GraphLoadStatus


def test_parser_defaults_words_to_none_and_captures_positional_prompt() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["copilot", "what changed from Mandy and Ed's view we were good last spring call last Thursday"]
    )
    assert args.words is None
    assert args.prompt == "what changed from Mandy and Ed's view we were good last spring call last Thursday"


def test_parse_copilot_request_extracts_multi_stakeholders_and_comparative_intent() -> None:
    query = "what changed from Mandy and Ed's view we were good last spring call last Thursday"
    parsed = parse_copilot_request(raw_request=query)
    assert parsed.inferred_intent == "gaps"
    assert parsed.target_speaker == "Mandy"
    assert parsed.targets == ["Mandy", "Ed"]
    assert parsed.topic_query is not None
    assert "mandy" in parsed.topic_keywords
    assert "ed" in parsed.topic_keywords


def test_debrief_query_tolerates_off_roster_stakeholders_and_logs_attempt_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    parser = build_parser()
    query = "what changed from Mandy and Ed's view we were good last spring call last Thursday"
    args = parser.parse_args(["copilot", "--request", query, "--meeting-dir", str(tmp_path), "--json"])
    args.runtime_root = tmp_path

    cm = ConversationContextModel(
        project_scope="project:lens",
        attendees=["Amit Ayer", "Rob Koch", "Chaffee Burke", "Miles Erickson"],
    )
    cm.add_turn(
        speaker="Rob Koch",
        text="In the live production UI the summary tiles showed the last two hundred filtered records rather than the golden dataset.",
        timestamp="00:08:25",
        turn_sec=505.0,
    )

    def _fake_resolve_session(_args):
        return (tmp_path, cm, None, "Debrief_Rob_PartnerAdvisory")

    fake_retriever = MagicMock()
    fake_retriever._topology = None
    fake_retriever.graph_sha256 = "abc123def456"
    fake_retriever.generation = "gen_test_1"
    fake_retriever.engagement_id = "project:lens"
    fake_retriever._last_initial_candidate_count = 2
    fake_retriever._last_rigor_tier = "VERIFIED"
    fake_retriever._last_grounding_result = None
    fake_retriever._last_gap_conflicts = [
        {
            "assertion_a": {
                "assertion_id": "asrt_1022_baseline",
                "subject_node_id": "workstream:lens",
                "claim_text": "harper Hoffman and Eduardo More praised the ninety percent golden dataset serial extraction demo during the sprint review.",
                "primary_source_id": "source-1022",
            },
            "assertion_b": {
                "assertion_id": "asrt_1027_shift",
                "subject_node_id": "workstream:lens",
                "claim_text": "Eduardo More and harper Hoffman questioned the production summary tiles after loading twenty five hundred unmapped field images and requested Michael Reiss to audit the golden dataset.",
                "primary_source_id": "source-1027",
            },
            "date_a": "2026-10-22",
            "date_b": "2026-10-27",
            "reason": "Temporal shift between 2026-10-22 and 2026-10-27",
        }
    ]
    assertions = [
        {
            "assertion_id": "asrt_1022_baseline",
            "subject_node_id": "workstream:lens",
            "predicate": "HAS_STATUS",
            "claim_text": "harper Hoffman and Eduardo More praised the ninety percent golden dataset serial extraction demo during the sprint review.",
            "observed_at": "2026-10-22",
            "primary_source_id": "source-1022",
            "confidence_score": 0.95,
            "is_primary": True,
        },
        {
            "assertion_id": "asrt_1027_shift",
            "subject_node_id": "workstream:lens",
            "predicate": "HAS_RISK",
            "claim_text": "Eduardo More and harper Hoffman questioned the production summary tiles after loading twenty five hundred unmapped field images and requested Michael Reiss to audit the golden dataset.",
            "observed_at": "2026-10-27",
            "primary_source_id": "source-1027",
            "confidence_score": 0.95,
            "is_primary": True,
        },
    ]
    fake_retriever.get_gaps.return_value = (assertions, True)
    fake_retriever.retrieve_gap_candidates.return_value = assertions
    fake_retriever.retrieve_candidates.return_value = assertions

    def _fake_load_graph(**_kwargs):
        return (GraphLoadStatus.LOADED_ACTIVE_PAIR, {}, fake_retriever)

    import dual_pipeline.cli.copilot_handlers as handlers
    import dual_pipeline.cli.copilot_session as sess_mod

    monkeypatch.setattr(sess_mod, "_resolve_session_and_context_model", _fake_resolve_session)
    monkeypatch.setattr(handlers, "load_copilot_graph", _fake_load_graph)

    res = execute_copilot_request(args)
    assert res["status"] == "PASS"

    attempts_file = tmp_path / "copilot_attempts.jsonl"
    assert attempts_file.is_file()
    records = [json.loads(line) for line in attempts_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    rec = records[0]
    assert rec["terminal_outcome"] == "SUCCESS"
    assert rec["request"] == query
    assert rec["parsed_topic"] is not None
    assert rec["targets"] == ["Mandy", "Ed"]
    assert rec["parsed_words"] is None
