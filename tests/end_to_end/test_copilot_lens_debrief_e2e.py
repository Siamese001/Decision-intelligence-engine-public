"""
tests/end_to_end/test_copilot_lens_debrief_e2e.py

Wave 5 End-to-End Class B Receipt Test:
Hermetic replay of the 10/27/2026 Prd_Eduardo_PartnerAdvisory + Debrief_Rob_PartnerAdvisory sessions
and the operator query:
  "what changed from Mandy and Ed's view we were good last spring call last Thursday"
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)


@dataclass
class _MockTopology:
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    assertions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    supporting_evidence: Dict[str, List[str]] = field(default_factory=dict)
    evidence_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    forward_adjacency: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def _build_lens_topology() -> _MockTopology:
    return _MockTopology(
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
                "aliases": ["Eduardo", "Ed", "Ed More"],
            },
        },
        assertions={
            "asrt-roster-header": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_STATUS",
                "claim_text": "- **Apex Global Enterprises (NEA):** harper Hoffman (Product Owner), Eduardo More (Tech Lead).",
                "engagement_id": "project_lens",
                "primary_source_id": "source-roster-noise",
                "source_date_normalized": "2026-10-22",
                "confidence_score": 0.95,
                "canonical_status": "ACTIVE",
                "is_primary": True,
            },
            "asrt-1022-baseline": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_OUTCOME",
                "claim_text": "On 10/22/2026, harper Hoffman and Ed More confirmed production deployment readiness alongside the Chaffee Burke and Rob Koch transition.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-d335b3123d936158964e8f55",
                "source_date_normalized": "2026-10-22",
                "confidence_score": 0.95,
                "canonical_status": "ACTIVE",
                "is_primary": True,
            },
            "asrt-1015-golden": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_RISK",
                "claim_text": "harper Hoffman and Ed More flagged golden dataset opacity versus the rolling queue in LENS.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-f6a551f3843692a28b182c2a",
                "source_date_normalized": "2026-10-15",
                "confidence_score": 0.93,
                "canonical_status": "ACTIVE",
                "is_primary": True,
            },
            "asrt-1016-cards": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_RISK",
                "claim_text": "Ed More noted that 50 mapped equipment types are active while 1800 scale items and rule authoring mistakes remain open.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-a5f026e415d55a99ae85b288",
                "source_date_normalized": "2026-10-16",
                "confidence_score": 0.92,
                "canonical_status": "ACTIVE",
                "is_primary": True,
            },
            "asrt-1021-rules": {
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_RISK",
                "claim_text": "harper Hoffman raised concern over rule authoring versus model mistakes and unverified self-learning loop claims in LENS.",
                "engagement_id": "project_lens",
                "primary_source_id": "source-a61f52bae598d501b6778298",
                "source_date_normalized": "2026-10-21",
                "confidence_score": 0.94,
                "canonical_status": "ACTIVE",
                "is_primary": True,
            },
        },
        supporting_evidence={
            "asrt-1022-baseline": ["ev-1022"],
            "asrt-1015-golden": ["ev-1015"],
            "asrt-1016-cards": ["ev-1016"],
            "asrt-1021-rules": ["ev-1021"],
        },
        evidence_meta={
            "ev-1022": {"source_id": "source-d335b3123d936158964e8f55"},
            "ev-1015": {"source_id": "source-f6a551f3843692a28b182c2a"},
            "ev-1016": {"source_id": "source-a5f026e415d55a99ae85b288"},
            "ev-1021": {"source_id": "source-a61f52bae598d501b6778298"},
        },
    )


def test_copilot_lens_debrief_comparative_synthesis_e2e(tmp_path: Path) -> None:
    """Replays the Prd_Eduardo_PartnerAdvisory + Debrief_Rob_PartnerAdvisory query end-to-end."""
    topo = _build_lens_topology()
    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "Rob", "org": "PartnerAdvisory"}, {"name": "Amit Ayer", "org": "PartnerAdvisory"}],
        engagement_id="project_lens",
        graph_sha256="sha256_lens_e2e_test",
        generation="gen_lens_e2e_1",
    )

    out_dir = tmp_path / "Debrief_Rob_PartnerAdvisory"
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx_model = ConversationContextModel(
        project_scope="project_lens",
        attendees=["Rob", "Amit Ayer", "harper Hoffman", "Eduardo More"],
    )
    # Earlier same-day Prd_Eduardo_PartnerAdvisory sibling turn + live Debrief_Rob_PartnerAdvisory turns
    ctx_model.add_turn(
        speaker="Eduardo More",
        text="In the earlier LENS session we reviewed 2500 production images versus the last 200 filtered UI card skew.",
        timestamp="00:05:00",
        turn_sec=300.0,
        is_sibling_session=True,
    )
    ctx_model.add_turn(
        speaker="Amit Ayer",
        text="Michael Reiss will run an independent data science audit of the golden dataset and hold twice-weekly working sessions with Ed More.",
        timestamp="00:12:00",
        turn_sec=720.0,
    )

    args = argparse.Namespace(
        request="what changed from Mandy and Ed's view we were good last spring call last Thursday",
        intent=None,
        target=None,
        topic=None,
        words=None,
        project="project_lens",
        engagement=None,
        session="sess_debrief_rob_e2e",
        active=True,
        offline=False,
        graph_only=False,
        meeting_dir=None,
        runtime_root=tmp_path,
        bypass_graph_cache=True,
        json=True,
    )

    def _mock_load_graph(*_a: Any, **_kw: Any) -> Any:
        return (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever)

    with (
        patch(
            "dual_pipeline.cli.copilot_session._resolve_session_and_context_model",
            return_value=(out_dir, ctx_model, None, "Debrief_Rob_PartnerAdvisory"),
        ),
        patch("dual_pipeline.cli.copilot_handlers.load_copilot_graph", side_effect=_mock_load_graph),
    ):
        res = execute_copilot_request(args)

    assert res["status"] == "PASS"
    assert res["intent"] == "gaps"
    assert len(res["cited_assertion_ids"]) >= 2

    answers_file = out_dir / "copilot_answers.jsonl"
    attempts_file = out_dir / "copilot_attempts.jsonl"
    assert answers_file.exists()
    assert attempts_file.exists()

    answer_rec = json.loads(answers_file.read_text(encoding="utf-8").splitlines()[-1])
    attempt_rec = json.loads(attempts_file.read_text(encoding="utf-8").splitlines()[-1])
    assert attempt_rec["terminal_outcome"] == "SUCCESS"

    body_text = answer_rec.get("body") or answer_rec.get("response") or ""
    assert "**Apex Global Enterprises" not in body_text
    assert len(body_text.split()) > 20
    # Verify baseline, friction, and live debrief context are represented in grounded assertions/body
    assert any(k in body_text for k in ("10/22/2026", "2026-10-22", "production deployment", "golden dataset"))
