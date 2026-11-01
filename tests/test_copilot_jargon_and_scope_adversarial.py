"""
tests/test_copilot_jargon_and_scope_adversarial.py

Wave 4 Adversarial & Edge-Case Suite:
Covers 5 mandatory edge-case categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty turns, whitespace-only prompts, missing target speaker.
2. Malformed input: Raw Candidate AI-SDLC control, Candidate Gemini/enterprise-agent, and internal spelling attribution caveats.
3. Overflow & exhaustion: Oversized multi-clause assertions (>40 words) distilled to concise first clause; tight word budget exhaustion.
4. Invariant bypass: Cross-project technical anchor leakage and Gate 31 smoke verifier rejection of jargon dumps.
5. Exact production regression: Pinning `--intent respond --target Speaker_Beta --request "challenge productivity increase from Speaker_Beta"`.
"""
from __future__ import annotations

import argparse
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.cli.copilot_speech import render_speaker_comments, spoken_claim
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_candidate_control_checklist,
    is_node_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    parse_copilot_request,
)
from tools.verify_copilot_smoke import ROOT, verify_copilot_active_graph_smoke


def test_category1_boundary_missing_target_and_empty_claims():
    """Category 1 (Boundary payload): Missing target for respond intent and empty assertion dicts."""
    retriever = MagicMock()
    retriever._topology = None
    assert spoken_claim({}, retriever) == ""
    res = render_speaker_comments("respond", "Speaker_Beta", [{}], max_words=40, retriever=retriever)
    assert res.rendered_count == 0
    assert res.failure_reason == "MALFORMED_REPRESENTATION"

    with tempfile.TemporaryDirectory(prefix="adv_cat1_") as td:
        args = argparse.Namespace(
            intent="respond",
            target="   ",
            project="project_beacon",
            runtime_root=td,
            meeting_dir=td,
            json=True,
        )
        out = handle_copilot(args)
        assert out["status"] == "FAIL"
        assert out["error"] == "MISSING_TARGET"


def test_category2_malformed_candidate_control_and_internal_caveat_rejection():
    """Category 2 (Malformed input): Rejects Candidate AI-SDLC/Gemini control rows and strips internal caveats."""
    retriever = MagicMock()
    retriever._topology = None

    for raw_control in (
        "Candidate AI-SDLC control (Risk, Audit & Regulatory Traceability): Capture evidence packs.",
        "Candidate enterprise-retrieval control (Retrieval Quality): Enforce hybrid recall gates.",
        "Candidate Gemini/enterprise-agent delivery-path criterion: when Strict latency, the default path is Full review.",
    ):
        item = {"assertion_id": "ctrl_1", "object_literal": raw_control}
        assert is_candidate_control_checklist(item) is True
        assert spoken_claim(item, retriever) == ""

    caveat_ast = {
        "assertion_id": "ast_caveat",
        "object_literal": (
            "Speaker_Alpha and Marcus stated that the outcome-velocity criticism had not yet been tied to a specific KPI. "
            "Person-specific attribution from the spelling in the raw transcript remains blocked pending identity confirmation."
        ),
    }
    distilled = spoken_claim(caveat_ast, retriever)
    assert "outcome-velocity criticism" in distilled
    assert "person-specific attribution" not in distilled.lower()


def test_category3_overflow_clause_distillation_and_word_budget():
    """Category 3 (Overflow & exhaustion): Multi-sentence claims >24 words distill to the primary clause."""
    retriever = MagicMock()
    retriever._topology = None
    long_ast = {
        "assertion_id": "ast_long",
        "object_literal": (
            "Taken together, these points reframe the issue: the team is already producing commits and outputs, "
            "while the material gaps are artifact-backed proof, stakeholder visibility, shared outcome and velocity measures, "
            "and a governed definition of in-path runtime evaluation. "
            "Scope: applies to Project Beacon pods across Q4 sprints and release readiness reviews."
        ),
    }
    res = render_speaker_comments("respond", "Speaker_Beta", [long_ast], max_words=None, retriever=retriever)
    assert res.rendered_count == 1
    assert len(res.body.split()) <= 40
    assert "scope:" not in res.body.lower()


def test_category4_invariant_bypass_cross_project_anchor_and_gate31_jargon_guard():
    """Category 4 (Invariant bypass): Foreign project technical anchors and Gate 31 jargon dumps fail closed."""
    lens_drone_anchor = {
        "node_id": "anchor_drone_01",
        "node_type": "technical_anchor",
        "canonical_name": "Drone Pipeline",
        "description": "LENS SOW 1 Drone Pipeline Review",
    }
    assert is_node_in_scope(
        "anchor_drone_01",
        lens_drone_anchor,
        bound_engagement="project:project_beacon",
        permitted_engagements={"project:project_beacon"},
    ) is False

    def jargon_runner(*_args, **_kwargs):
        return types.SimpleNamespace(
            returncode=0,
            stdout="Speaker_Beta, could we clarify Candidate AI-SDLC control (Risk, Audit & Regulatory Traceability)?",
            stderr="",
        )

    ok, msg = verify_copilot_active_graph_smoke(ROOT, runner=jargon_runner)
    assert ok is False
    assert "forbidden jargon" in msg.lower()


def test_category5_exact_production_regression_ninan_flag_fusion():
    """Category 5 (Exact production regression): Combining --intent, --target, and --request retains topic keywords."""
    parsed = parse_copilot_request(
        raw_request="challenge productivity increase from Speaker_Beta",
        intent_override="respond",
        target_override="Speaker_Beta",
        topic_override=None,
    )
    assert parsed.inferred_intent == "respond"
    assert parsed.target_speaker == "Speaker_Beta"
    assert "productivity" in parsed.topic_keywords
    assert "increase" in parsed.topic_keywords
    assert "ninan" not in [k.lower() for k in parsed.topic_keywords]
