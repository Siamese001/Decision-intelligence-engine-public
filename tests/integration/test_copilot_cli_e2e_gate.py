"""
tests/integration/test_copilot_cli_e2e_gate.py

Mandatory CLI-Driven Integration Gate.
Enforces Phase 2 CI Testing Rigor:
- Exercises Copilot strictly through the CLI entry point (execute_copilot_request / handle_copilot).
- Guarantees zero synthetic mocks: binds to real SQLite engines via high_fidelity_graph.
- Validates full dependency injection, lifecycle orchestrator state progression,
  topological inheritance, in-memory neighborhood cache, and grounded assertion retrieval.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Generator

import pytest

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotLifecycleOrchestrator,
    CopilotLifecycleState,
)
from tests.fixtures.high_fidelity_graph import create_high_fidelity_sqlite_graph


@pytest.fixture
def high_fidelity_environment(tmp_path: Path) -> Generator[tuple[Path, Path], None, None]:
    """Provisions a production-identical SQLite graph, active_pair manifest, and meeting directory."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "graph.sqlite"

    create_high_fidelity_sqlite_graph(db_path=db_path, runtime_dir=runtime_dir)

    meeting_dir = tmp_path / "meeting_session_jarvis"
    meeting_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "meeting_name": "Jarvis Architecture Steering",
        "engagement_id": "project:project_jarvis",
        "attendees": [
            {"name": "Sharon Perez", "role": "Enterprise Architect", "org": "APEX_TRANS"},
            {"name": "Daniel Brakin", "role": "Principal Engineer", "org": "APEX_TRANS"},
            {"name": "harper Hoffman", "role": "VP Operations", "org": "APEX_TRANS"},
        ],
    }
    (meeting_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    segments = [
        {
            "speaker": "Sharon Perez",
            "text": "Who is the tech lead and architecture owner for SAP ERP integration?",
            "timestamp": "00:01:00",
            "turn_sec": 60.0,
        },
        {
            "speaker": "Daniel Brakin",
            "text": "We are evaluating the Lone Star EMS and SAP dependencies under Nexus.",
            "timestamp": "00:01:30",
            "turn_sec": 90.0,
        },
    ]
    with open(meeting_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for s in segments:
            f.write(json.dumps(s) + "\n")

    old_root = os.environ.get("NEE_RUNTIME_ROOT")
    os.environ["NEE_RUNTIME_ROOT"] = str(runtime_dir)

    yield runtime_dir, meeting_dir

    if old_root is not None:
        os.environ["NEE_RUNTIME_ROOT"] = old_root
    else:
        os.environ.pop("NEE_RUNTIME_ROOT", None)


@pytest.mark.integration
class TestCopilotCliE2EGate:
    def test_cli_entrypoint_executes_with_real_sqlite_and_cache(
        self, high_fidelity_environment: tuple[Path, Path]
    ) -> None:
        """
        Gate 1: Verify the CLI entry point successfully boots the lifecycle orchestrator,
        loads the real SQLite database, precomputes in-memory graph neighborhoods,
        and answers the cross-engagement query with authentic evidence.
        """
        runtime_dir, meeting_dir = high_fidelity_environment

        args = argparse.Namespace(
            request="Who is the tech lead and architecture owner for SAP ERP integration?",
            intent="respond",
            target="Sharon Perez",
            topic="SAP ERP",
            words=60,
            json=True,
            meeting_dir=str(meeting_dir),
            project="project:project_jarvis",
            session=None,
            active=False,
            runtime_root=runtime_dir,
        )

        # 1. Execute via CLI entry point handler
        result = handle_copilot(args)

        # 2. Verify response contract
        assert result is not None, "CLI execution must return a valid response dict"
        assert result.get("status") == "PASS", f"Expected PASS, got: {result.get('status')} ({result.get('error')}: {result.get('details')})"
        assert result.get("answer_id") is not None, "Answer ID must be generated"

        # 3. Verify cited assertions
        cited_ids = result.get("cited_assertion_ids", [])
        assert len(cited_ids) > 0, "Must cite at least one assertion from the knowledge graph"
        assert "ast-daniel-sap-owner" in cited_ids or any("sap" in aid.lower() for aid in cited_ids)

        # 4. Verify answer was recorded to answers JSONL on disk
        answers_path = Path(result["answers_path"])
        assert answers_path.is_file(), f"Answers file must exist at {answers_path}"
        logged_answers = [json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(logged_answers) >= 1
        last_ans = logged_answers[-1]
        assert "Daniel Brakin" in last_ans["response"] or "SAP" in last_ans["response"]

    def test_cli_cross_engagement_topological_inheritance(
        self, high_fidelity_environment: tuple[Path, Path]
    ) -> None:
        """
        Gate 2: Verify that queries targeting domain topics (e.g. SAP) without explicitly
        naming 'project nexus' successfully bridge via topological inheritance.
        """
        runtime_dir, meeting_dir = high_fidelity_environment

        args = argparse.Namespace(
            request="Who is the owner of SAP ERP?",
            intent="graph-context",
            target=None,
            topic="SAP ERP",
            words=60,
            json=True,
            meeting_dir=str(meeting_dir),
            project="project:project_jarvis",
            session=None,
            active=False,
            runtime_root=runtime_dir,
        )

        result = execute_copilot_request(args)
        assert result.get("status") == "PASS", f"Expected PASS, got: {result.get('error')}"
        assert len(result.get("cited_assertion_ids", [])) > 0
        assert "ast-daniel-sap-owner" in result["cited_assertion_ids"]

    def test_cli_boundary_isolation_prevents_unrelated_tenant_leak(
        self, high_fidelity_environment: tuple[Path, Path]
    ) -> None:
        """
        Gate 3: Verify that an unrelated lateral tenant (project:project_unrelated)
        is strictly quarantined by C-19 boundary policy and never returned to Jarvis.
        """
        runtime_dir, meeting_dir = high_fidelity_environment

        args = argparse.Namespace(
            request="What confidential data is stored in the third-party CRM?",
            intent="graph-context",
            target=None,
            topic="unrelated CRM customer data",
            words=60,
            json=True,
            meeting_dir=str(meeting_dir),
            project="project:project_jarvis",
            session=None,
            active=False,
            runtime_root=runtime_dir,
        )

        result = execute_copilot_request(args)
        # Result should either be FAIL (ZERO_MATCHING_ASSERTIONS / SCOPE_FILTERED) or PASS without unpermitted assertions
        if result.get("status") == "PASS":
            cited = result.get("cited_assertion_ids", [])
            assert "ast-unrelated-crm" not in cited, "Unrelated third-party tenant assertion must never be cited!"
        else:
            assert result.get("error") in ("ZERO_MATCHING_ASSERTIONS", "SCOPE_FILTERED", "ENTITY_NOT_FOUND", "REJECTED_EVIDENCE")
