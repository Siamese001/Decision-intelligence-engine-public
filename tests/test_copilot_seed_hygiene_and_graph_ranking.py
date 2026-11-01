"""
tests/test_copilot_seed_hygiene_and_graph_ranking.py

Wave 3 unit and integration tests for:
1. Seed extraction hygiene on Debrief_Rob_PartnerAdvisory turns (LENS, harper Hoffman, Eduardo More,
   Michael Reiss, SOW 1, Golden Dataset, Serial Number) with zero stopword noise ("Back", "Thank", "Like", "Right", "Yeah").
2. Substantive LENS assertion ranking across the 6 target source families (10/22 baseline + 10/15-10/21 friction)
   while blocking markdown attendee roster headers (- **Apex Global Enterprises (NEA):**...).
3. Preserving verified GraphDB assertions ahead of raw live_turn_* fragments when not explicitly live-only.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
    extract_turn_entities,
)
from dual_pipeline.live_meeting_recorder_core.copilot_intents import _maybe_augment_live_assertions
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


@dataclass
class _MockTopology:
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    assertions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    supporting_evidence: Dict[str, List[str]] = field(default_factory=dict)
    evidence_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    forward_adjacency: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def test_debrief_rob_partner_advisory_seed_hygiene() -> None:
    """Verify Debrief_Rob_PartnerAdvisory turns extract clean domain/stakeholder seeds without stopword noise."""
    model = ConversationContextModel(
        project_scope="project_lens",
        attendees=["Rob", "Amit Ayer"],
        salient_entities_top_k=15,
    )
    sample_turns = [
        "Back on the LENS sprint call with harper Hoffman and Eduardo More, they flagged the Golden Dataset gap.",
        "Thank you Rob. Like we discussed with Michael Reiss under SOW 1, Serial Number linkage across the 200 cards is critical.",
        "Right, yeah, look at the little book and bookies from last week—we need LENS Golden Dataset validation.",
    ]
    for idx, txt in enumerate(sample_turns, 1):
        ents = extract_turn_entities(txt)
        for noisy in ("Back", "Thank", "Like", "Right", "Yeah", "Look", "BRD", "FW", "POV", "SW", "book", "bookies", "little", "last"):
            assert noisy not in ents
        model.add_turn(speaker="Amit Ayer", text=txt, timestamp=f"00:0{idx}:00", turn_sec=float(idx * 30))

    seeds = model.retrieval_seeds()
    for expected in ("LENS", "harper Hoffman", "Eduardo More", "Michael Reiss", "SOW 1", "Golden Dataset", "Serial Number"):
        assert expected in seeds, f"Expected '{expected}' in retrieval_seeds: {seeds}"
    for forbidden in ("Back", "Thank", "Like", "Right", "Yeah", "BRD", "FW", "POV", "SW"):
        assert forbidden not in seeds, f"Forbidden stopword '{forbidden}' leaked into retrieval_seeds: {seeds}"


def test_lens_6_source_families_ranking_and_roster_header_blocking() -> None:
    """Verify the 6 LENS source families rank in top candidates and markdown roster headers are blocked."""
    topo = _MockTopology(
        nodes={
            "workstream:lens": {
                "canonical_name": "LENS",
                "display_name": "Project LENS",
                "node_type": "workstream",
                "engagement_id": "project_lens",
                "aliases": ["LENS", "Single Turbine", "Turbine Fleet"],
            },
            "person:mandy-hopmann": {
                "canonical_name": "harper Hoffman",
                "display_name": "harper Hoffman",
                "node_type": "person",
                "engagement_id": "project_lens",
                "aliases": ["Mandy"],
            },
            "person:eduardo-more": {
                "canonical_name": "Eduardo More",
                "display_name": "Eduardo More",
                "node_type": "person",
                "engagement_id": "project_lens",
                "aliases": ["Eduardo", "Ed"],
            },
        },
        assertions={
            "asrt-roster-header-1": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_STATUS",
                "claim_text": "- **Apex Global Enterprises (NEA):** harper Hoffman (Product Owner), Eduardo More (Tech Lead).",
                "engagement_id": "project_lens",
                "primary_source_id": "source-roster-noise",
                "source_date_normalized": "2026-10-22",
                "confidence_score": 0.95,
            },
            "asrt-1022-baseline": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_OUTCOME",
                "claim_text": "On 10/22/2026, harper Hoffman and Eduardo More confirmed strong enthusiasm for LENS sprint progress and production rollout readiness.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-d335b3123d936158964e8f55",
                "source_date_normalized": "2026-10-22",
                "confidence_score": 0.95,
            },
            "asrt-1015-golden": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_RISK",
                "claim_text": "harper Hoffman and Eduardo More identified missing golden dataset benchmarks and serial number lineage gaps in LENS.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-f6a551f3843692a28b182c2a",
                "source_date_normalized": "2026-10-15",
                "confidence_score": 0.93,
            },
            "asrt-1016-cards": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_RISK",
                "claim_text": "Eduardo More flagged that the 200-card Jira board skews toward plumbing rather than end-to-end LENS user outcomes for harper Hoffman.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-a5f026e415d55a99ae85b288",
                "source_date_normalized": "2026-10-16",
                "confidence_score": 0.92,
            },
            "asrt-1021-rules": {
                "subject_node_id": "person:mandy-hopmann",
                "predicate": "RAISED_CONCERN",
                "claim_text": "harper Hoffman raised concern that business rule authoring and validation workflow in LENS still requires manual engineering intervention.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-a61f52bae598d501b6778298",
                "source_date_normalized": "2026-10-21",
                "confidence_score": 0.94,
            },
            "asrt-1021-sow": {
                "subject_node_id": "person:eduardo-more",
                "predicate": "HAS_COMMITMENT",
                "claim_text": "Eduardo More noted that SOW 1 deliverables for LENS single-turbine and fleet views must reconcile PRD acceptance criteria with harper Hoffman.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-7205dbc733300947730c698a",
                "source_date_normalized": "2026-10-21",
                "confidence_score": 0.91,
            },
            "asrt-1021-arch": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_DEPENDENCY",
                "claim_text": "LENS production cutover depends on resolving serial number mapping and golden dataset sign-off with harper Hoffman and Eduardo More.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-e8d8adfc50acdbdf588ba996",
                "source_date_normalized": "2026-10-21",
                "confidence_score": 0.92,
            },
        },
    )

    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "Rob", "org": "PartnerAdvisory"}, {"name": "Amit Ayer", "org": "PartnerAdvisory"}],
        engagement_id="project_lens",
    )

    parsed = parse_copilot_request(
        raw_request="what changed from Mandy and Ed's view we were good last spring call last Thursday",
    )
    query_str = " ".join([parsed.target_speaker or "", parsed.topic_query or ""] + parsed.topic_keywords + ["LENS"])
    results = retriever.retrieve_structured_assertions(query_str, max_results=10)

    retrieved_sources = {r.get("source_id") for r in results}
    expected_sources = {
        "source-d335b3123d936158964e8f55",
        "source-f6a551f3843692a28b182c2a",
        "source-a5f026e415d55a99ae85b288",
        "source-a61f52bae598d501b6778298",
        "source-7205dbc733300947730c698a",
        "source-e8d8adfc50acdbdf588ba996",
    }
    assert expected_sources.issubset(retrieved_sources)
    assert all(not str(r.get("claim_text", "")).startswith("- **Apex Global Enterprises") for r in results)


def test_graph_assertions_precede_live_turn_fragments_when_not_explicit_live() -> None:
    """Verify _maybe_augment_live_assertions keeps non-stub GraphDB assertions ahead of live_turn_*."""
    model = ConversationContextModel(project_scope="project_lens", attendees=["harper Hoffman"])
    model.add_turn("harper Hoffman", "We should check the LENS golden dataset.", "00:01:00", turn_sec=60.0)

    graph_asrt = {
        "assertion_id": "asrt-graph-1",
        "predicate": "HAS_RISK",
        "claim_text": "harper Hoffman flagged missing golden dataset validation for LENS.",
    }
    _, merged, ok = _maybe_augment_live_assertions(
        intent="respond",
        target="harper Hoffman",
        dialogue=model,
        selected_utterance=None,
        topic_keywords=["golden", "dataset"],
        body="Body",
        assertions=[graph_asrt],
        ok=True,
    )
    assert ok is True
    assert merged[0]["assertion_id"] == "asrt-graph-1"
