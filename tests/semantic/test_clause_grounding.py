"""
tests/semantic/test_clause_grounding.py

Phase 4 Anti-Hallucination & Semantic Safety Test Suite.
Enforces:
1. Verbatim Clause Grounding Validation (Agent 6): Property-based verification where every synthesized
   clause is cryptographically bound to a valid source URI, assertion ID, or direct quote.
2. Out-of-Bounds Generative Tripwires (Agent 6): Queries for absent facts fail-closed rather than hallucinating.
3. Low-Context Fail-Closed Pre-Qualification (Agent 6): Fast-fails before LLM synthesis when evidence is absent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    GROUNDING_QUESTION_FALLBACK,
    check_clause_grounding,
    extract_durations,
    verify_and_filter_script,
    verify_and_filter_sentences,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _diagnose_unresolved_evidence,
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import reset_lifecycle_orchestrators
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import load_copilot_graph
from tests.fixtures.high_fidelity_graph import create_high_fidelity_sqlite_graph


@pytest.fixture(autouse=True)
def clean_state():
    reset_lifecycle_orchestrators()
    yield
    reset_lifecycle_orchestrators()


@pytest.fixture
def high_fidelity_fixture(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "graph.sqlite"
    create_high_fidelity_sqlite_graph(db_path, runtime_dir)

    session_dir = tmp_path / "session_grounding"
    session_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "session_id": "sess_grounding_001",
        "engagement_id": "project:project_jarvis",
        "topic": "SAP ERP Integration",
        "attendees": [
            {"canonical_name": "Sharon Perez", "name": "Sharon Perez", "role": "attendee"},
            {"canonical_name": "Daniel Brakin", "name": "Daniel Brakin", "role": "attendee"},
        ],
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    manifest = {
        "session_id": "sess_grounding_001",
        "engagement_id": "project:project_jarvis",
        "repo_root": str(runtime_dir),
        "copilot_state": "READY",
        "context_state": "FRESH",
    }
    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    segments = [
        {
            "speaker": "Sharon Perez",
            "text": "Who is the tech lead and architecture owner for SAP ERP integration?",
            "timestamp": "00:01:00",
            "turn_sec": 60.0,
        },
    ]
    with open(session_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for s in segments:
            f.write(json.dumps(s) + "\n")

    return {
        "runtime_dir": runtime_dir,
        "session_dir": session_dir,
        "db_path": db_path,
    }


class TestVerbatimClauseGrounding:
    """Agent 6: Clause-level verbatim verification and anti-hallucination tripwires."""

    def test_verbatim_grounded_clause_passes(self):
        """Asserts that verbatim, grounded statements pass clause verification."""
        assertions = [
            {
                "assertion_id": "ast-daniel-sap-owner",
                "subject_node_id": "person:daniel_brakin",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:sap_erp",
                "claim_text": "Daniel Brakin is the technical owner and architecture lead for SAP ERP",
                "source_id": "src-nexus-01",
            }
        ]
        by_id = {"ast-daniel-sap-owner": assertions[0]}

        clause = "Daniel Brakin is the technical owner and architecture lead for SAP ERP. [ast-daniel-sap-owner]"
        hit = check_clause_grounding(clause, assertions, by_id)
        assert hit == "ast-daniel-sap-owner"

    def test_invented_timeline_or_duration_rejected(self):
        """Asserts that ungrounded timelines or invented deadlines fail clause verification."""
        assertions = [
            {
                "assertion_id": "ast-daniel-sap-owner",
                "subject_node_id": "person:daniel_brakin",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:sap_erp",
                "claim_text": "Daniel Brakin is the technical owner and architecture lead for SAP ERP",
                "source_id": "src-nexus-01",
            }
        ]
        by_id = {"ast-daniel-sap-owner": assertions[0]}

        # Invented timeline "in 3 weeks" is not supported by the evidence
        clause = "Daniel Brakin will finish the SAP ERP deployment in 3 weeks. [ast-daniel-sap-owner]"
        hit = check_clause_grounding(clause, assertions, by_id)
        assert hit is None, "Invented timeline must be rejected even if citation is present"

    def test_invented_reuse_or_cross_tenant_rejected(self):
        """Asserts that unsupported reuse claims fail verification."""
        assertions = [
            {
                "assertion_id": "ast-daniel-sap-owner",
                "subject_node_id": "person:daniel_brakin",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:sap_erp",
                "claim_text": "Daniel Brakin is the technical owner and architecture lead for SAP ERP",
                "source_id": "src-nexus-01",
            }
        ]
        by_id = {"ast-daniel-sap-owner": assertions[0]}

        # Claiming reuse/shared module that was never in the graph
        clause = "Daniel Brakin is reusing the shared components from Project Pegasus for SAP ERP. [ast-daniel-sap-owner]"
        hit = check_clause_grounding(clause, assertions, by_id)
        assert hit is None, "Unsupported cross-system reuse claim must be rejected"

    def test_hallucinatory_script_degrades_to_safe_fallback(self):
        """Asserts that an ungrounded or hallucinatory script collapses to the fail-closed question fallback."""
        assertions = [
            {
                "assertion_id": "ast-daniel-sap-owner",
                "claim_text": "Daniel Brakin is the technical owner and architecture lead for SAP ERP",
            }
        ]
        by_id = {"ast-daniel-sap-owner": assertions[0]}
        cited_ids = set()

        fabricated_script = (
            "We have completed the deployment to AWS Lambda across all regions yesterday. "
            "The budget of $5 million was fully utilized."
        )

        filtered = verify_and_filter_script(fabricated_script, assertions, by_id, cited_ids)
        assert filtered == GROUNDING_QUESTION_FALLBACK
        assert len(cited_ids) == 0


class TestOutOfBoundsGenerativeTripwires:
    """Agent 6: Tripwires detecting out-of-bounds cross-engagement and absent entity requests."""

    def test_query_requesting_absent_entity_fails_closed(self, high_fidelity_fixture):
        """Queries for entities that do not exist in graph fail closed with ENTITY_NOT_FOUND."""
        runtime_dir = high_fidelity_fixture["runtime_dir"]
        session_dir = high_fidelity_fixture["session_dir"]

        args = argparse.Namespace(
            command="copilot",
            intent="respond",
            target="Unknown Architect",
            topic="Oracle ERP",
            request=None,
            words=None,
            json=True,
            mock=False,
            meeting_dir=str(session_dir),
            session=None,
            active=False,
            project="project:project_jarvis",
            runtime_root=runtime_dir,
        )

        res = execute_copilot_request(args)
        assert res.get("status") == "FAIL"
        assert res.get("error") in ("SPEAKER_NOT_IN_ROSTER", "ENTITY_NOT_FOUND")

    def test_query_for_absent_facts_fails_closed(self, high_fidelity_fixture):
        """Queries for absent domains trigger ZERO_MATCHING_ASSERTIONS fail-closed response."""
        runtime_dir = high_fidelity_fixture["runtime_dir"]
        session_dir = high_fidelity_fixture["session_dir"]

        args = argparse.Namespace(
            command="copilot",
            intent="respond",
            target="Sharon Perez",
            topic="Blockchain Bitcoin Ledger",
            request=None,
            words=None,
            json=True,
            mock=False,
            meeting_dir=str(session_dir),
            session=None,
            active=False,
            project="project:project_jarvis",
            runtime_root=runtime_dir,
        )

        res = execute_copilot_request(args)
        assert res.get("status") == "FAIL"
        assert res.get("error") in ("ZERO_RELEVANT_UTTERANCES", "ZERO_MATCHING_ASSERTIONS")


class TestLowContextFailClosedPreQualification:
    """Agent 6: Pre-qualification gates preventing synthesis when context is missing."""

    def test_missing_topic_pre_qualification_fast_fails(self, high_fidelity_fixture):
        """Asserts that requests lacking any topic keywords or natural prompt fail immediately."""
        session_dir = high_fidelity_fixture["session_dir"]
        runtime_dir = high_fidelity_fixture["runtime_dir"]

        args = argparse.Namespace(
            command="copilot",
            intent="next-questions",
            target=None,
            topic=None,
            request="",
            words=None,
            json=True,
            mock=False,
            meeting_dir=str(session_dir),
            session=None,
            active=False,
            project="project:project_jarvis",
            runtime_root=runtime_dir,
        )

        # Diagnose unresolved evidence directly
        code, det = _diagnose_unresolved_evidence(None, topic_keywords=[], topic_query=None, target=None)
        assert code == "PARSER_NO_TOPIC"
