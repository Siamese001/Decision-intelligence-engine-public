"""Tests for Wave 7: Stakeholder-Calibrated Next Best Actions & Thought Leadership."""
from __future__ import annotations

from dual_pipeline.query_core.dynamic_entities import resolve_named_coverage_subjects
from dual_pipeline.query_core.reasoning import reason_over_packet, validate_answer
from dual_pipeline.query_core.thought_leadership import (
    extract_audience_stakeholders,
    strip_audience_clause,
)


def test_extract_audience_stakeholders_parses_dan_and_stephan_without_polluting_subjects() -> None:
    question = (
        "Provide honest analysis of Brad and Beau discussion on pods local to West Palm Beach "
        "area from the perspective of NEE. Audience Dan and Stephan who are intimately familiar "
        "with topic per transcript and call we had yesterday."
    )
    audiences = extract_audience_stakeholders(question)
    assert audiences == ["Dan", "Stephan"]

    stripped = strip_audience_clause(question)
    assert "Brad" in stripped
    assert "Beau" in stripped

    subjects = resolve_named_coverage_subjects(
        question,
        plan={
            "primary_entities": ["Brad Jackson", "Beau Williamson", "NEE"],
            "dynamic_entities": [
                {"canonical_name": "Brad Jackson"},
                {"canonical_name": "Beau Williamson"},
                {"canonical_name": "Drew Oakley"},
                {"canonical_name": "Cameron Mercer"},
            ],
        },
        active=None,
    )
    assert "Brad Jackson" in subjects
    assert "Beau Williamson" in subjects
    assert "Drew Oakley" not in subjects
    assert "Cameron Mercer" not in subjects


def test_reason_over_packet_synthesizes_quantitative_thought_leadership_nbas_and_passes_validate_answer() -> None:
    question = (
        "Provide honest analysis of Brad and Beau discussion on pods local to West Palm Beach "
        "area from the perspective of NEE. Audience Dan and Stephan who are intimately familiar "
        "with topic."
    )
    plan = {
        "question": question,
        "query_type": "opportunity_nba",
        "retrieval_mode": "strategic",
        "allowed_lanes": ["approved", "candidate"],
        "primary_entities": ["Brad Jackson", "Beau Williamson", "Dan", "Stephan"],
    }
    packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec_fact_wpb",
                    "title": "Brad and Beau West Palm Beach Pod Proposal",
                    "retrieval_text": (
                        "Brad Jackson and Beau Williamson discussed deploying 10 local pods "
                        "(50 full-stack engineers) in West Palm Beach under Accelerate Power."
                    ),
                    "record_type": "assertion",
                    "lane": "approved",
                    "evidence_ids": ["ev_fact_wpb"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            },
            {
                "record": {
                    "record_id": "rec_constraint_nee",
                    "title": "NEE Screening Bar & Portfolio Capacity Constraints",
                    "retrieval_text": (
                        "NEE enforces a 5-10% technical screening pass rate, a 1:8 lead-to-engineer ratio, "
                        "4 active workstreams (5-6 engineers per pod), a 42 hours/week billing cap, "
                        "a 45% higher onshore rate differential, and a 14-day IT deprovisioning notice window."
                    ),
                    "record_type": "assertion",
                    "lane": "approved",
                    "evidence_ids": ["ev_constraint_nee"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "decision_constraint"},
            },
        ],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
    }
    answer = reason_over_packet(plan, packet)
    validation = validate_answer(answer, packet)
    assert validation["status"] == "PASS", validation

    # Audience entities Dan and Stephan must not trigger missing factual evidence gaps
    assert not any("Dan" in g or "Stephan" in g for g in packet["gaps"])

    # Next best actions must include quantitative funnel/capacity proofs and audience calibration
    nba_texts = " ".join(item["action"] for item in answer["next_best_actions"])
    assert "Dan and Stephan" in nba_texts
    assert "667" in nba_texts
    assert "2-3 shared platform pods" in nba_texts
    assert "milestone holdbacks" in nba_texts
    assert "14-day" in nba_texts
