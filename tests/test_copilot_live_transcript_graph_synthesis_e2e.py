"""
tests/test_copilot_live_transcript_graph_synthesis_e2e.py

Wave 4 End-to-End Regression Suite:
Verifies the full live-transcript-to-graph copilot pipeline:
1. Multi-turn speaker utterance aggregation (speaker_summary + window_summary).
2. Request-topic fusion when --intent, --target, and --request are combined.
3. Project-first retrieval scoping excluding Candidate AI-SDLC control noise.
4. Plain-English conversational synthesis (<= 40 words) and dual-source grounding verification.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from dual_pipeline.cli.copilot_handlers import handle_copilot


def _provision_production_derived_sandbox(root: Path) -> tuple[Path, Path]:
    """Builds an isolated sandbox seeded with production-derived Project Beacon and enterprise assertions."""
    runtime_root = root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "graph.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            node_type TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT,
            retrieval_eligible INTEGER,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            observed_at TEXT,
            source_date_normalized TEXT,
            primary_source_id TEXT,
            engagement_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            locator TEXT,
            content TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            title TEXT,
            source_date_normalized TEXT
        );
        CREATE VIEW v_canonical_edge AS
        SELECT assertion_id, subject_node_id AS from_node_id, predicate AS relationship_family,
               predicate AS edge_type, object_node_id AS to_node_id, materiality, primary_source_id AS source_id,
               0 AS hitl_required
        FROM assertion WHERE object_node_id IS NOT NULL;
        """
    )

    con.execute(
        "INSERT INTO node VALUES ('project:project_beacon', 'Project Beacon', 'Project Beacon', 'project', 'Project Beacon modernization program', 'active', 1)"
    )
    con.execute(
        "INSERT INTO node VALUES ('person:ninan', 'Speaker_Beta', 'Speaker_Beta', 'person', 'Engineering Lead', 'active', 1)"
    )
    con.execute(
        "INSERT INTO node VALUES ('anchor_sdlc_ctrl', 'AI-SDLC Control', 'AI-SDLC Control', 'technical_anchor', 'Enterprise AI-SDLC control catalog', 'active', 1)"
    )
    con.execute("INSERT INTO source VALUES ('src-sherpa-1009', 'Project Beacon Review', '2026-10-09')")

    # Production-derived Project Beacon assertions
    con.execute(
        """
        INSERT INTO assertion VALUES (
            'asrt_sherpa_kpi_gap', 'project:project_beacon', 'IDENTIFIES_RISK', NULL,
            'Taken together, these points reframe the issue: the team is already producing commits and outputs, while the material gaps are artifact-backed proof, stakeholder visibility, shared outcome and velocity measures, and a governed definition of in-path runtime evaluation. This is internal synthesis, not formal delivery status.',
            'affirmed', 1, 'high', 'internal', '[]', '2026-10-09', '2026-10-09', 'src-sherpa-1009', 'project:project_beacon'
        )
        """
    )
    con.execute(
        """
        INSERT INTO assertion VALUES (
            'asrt_sherpa_scorecard', 'project:project_beacon', 'REQUIRES_VALIDATION', NULL,
            'Speaker_Alpha and Marcus stated that the outcome-velocity criticism had not yet been tied to a specific KPI, calculation, or shared scorecard. Person-specific attribution from the spelling in the raw transcript remains blocked pending identity confirmation.',
            'affirmed', 1, 'high', 'internal', '[]', '2026-10-09', '2026-10-09', 'src-sherpa-1009', 'project:project_beacon'
        )
        """
    )
    # Competing enterprise AI-SDLC control checklist noise
    con.execute(
        """
        INSERT INTO assertion VALUES (
            'asrt_sdlc_noise_01', 'anchor_sdlc_ctrl', 'GOVERNS', NULL,
            'Candidate AI-SDLC control (Risk, Audit & Regulatory Traceability): Evidence packs, control attestations, and release lineage must be captured for productivity and velocity reviews.',
            'affirmed', 1, 'high', 'GLOBAL', '[]', '2026-10-01', '2026-10-01', 'src-sherpa-1009', 'project:enterprise'
        )
        """
    )
    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    (runtime_root / "active_pair.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "graph_path": "graph.sqlite",
                "graph_sha256": sha256,
                "generation_id": "gen-wave4-e2e-001",
            }
        ),
        encoding="utf-8",
    )

    meeting_dir = root / "project_beacon_live"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    turns = [
        {
            "seq": 1,
            "speaker": "Speaker_Beta",
            "text": "Our engineering team productivity increased by 35 percent over the last two sprints.",
            "timestamp": "00:01:10",
            "start_sec": 70.0,
        },
        {
            "seq": 2,
            "speaker": "Speaker_Alpha",
            "text": "How are we measuring that across the pods?",
            "timestamp": "00:01:18",
            "start_sec": 78.0,
        },
        {
            "seq": 3,
            "speaker": "Speaker_Beta",
            "text": "That velocity gain comes from automated code generation and faster PR merges.",
            "timestamp": "00:01:25",
            "start_sec": 85.0,
        },
    ]
    with open(meeting_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for t in turns:
            f.write(json.dumps(t) + "\n")

    return runtime_root, meeting_dir


def test_e2e_ninan_productivity_challenge_synthesizes_live_and_graph():
    """Verifies end-to-end CLI copilot execution for Speaker_Beta's productivity claim."""
    with tempfile.TemporaryDirectory(prefix="wave4_copilot_e2e_") as td:
        runtime_root, meeting_dir = _provision_production_derived_sandbox(Path(td))
        args = argparse.Namespace(
            intent="respond",
            target="Speaker_Beta",
            request="challenge productivity increase from Speaker_Beta",
            project="project_beacon",
            meeting_dir=str(meeting_dir),
            runtime_root=str(runtime_root),
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = handle_copilot(args)

        assert res["status"] == "PASS", f"Expected PASS, got: {res}"
        stdout_txt = buf.getvalue().strip()

        # 1. Single fenced block output contract
        assert stdout_txt.startswith("```text\n") and stdout_txt.endswith("\n```")
        spoken = stdout_txt[len("```text\n") : -len("\n```")].strip()
        # 2. References Speaker_Beta and his live productivity point
        assert spoken.startswith("Speaker_Beta, on your productivity point, could we clarify ")
        # 3. Grounded in Project Beacon assertions, not Candidate AI-SDLC control
        assert set(res["cited_assertion_ids"]) <= {"asrt_sherpa_kpi_gap", "asrt_sherpa_scorecard"}
        assert "asrt_sdlc_noise_01" not in res["cited_assertion_ids"]
        # 4. Zero jargon or internal provenance caveats
        low = spoken.lower()
        assert "candidate ai-sdlc control" not in low
        assert "internal synthesis" not in low
        assert "person-specific attribution" not in low
        # 5. Concise executive word ceiling (<= 40 words)
        assert len(spoken.split()) <= 40

        # 6. Verify durable answer ledger records aggregated live speaker summary
        answers_file = meeting_dir / "copilot_answers.jsonl"
        assert answers_file.is_file()
        answer_rec = json.loads(answers_file.read_text(encoding="utf-8").splitlines()[-1])
        acoustic = answer_rec["evidence_breakdown"]["acoustic_transcript_evidence"][0]
        assert acoustic["speaker"] == "Speaker_Beta"
        assert "35 percent" in acoustic["speaker_summary"]
        assert "velocity gain" in acoustic["speaker_summary"]


def test_e2e_next_questions_synthesizes_project_scoped_inquiries():
    """Verifies next-questions intent in a live session prioritizes project assertions and strips caveats."""
    with tempfile.TemporaryDirectory(prefix="wave4_copilot_nbq_") as td:
        runtime_root, meeting_dir = _provision_production_derived_sandbox(Path(td))
        args = argparse.Namespace(
            intent="next-questions",
            request="velocity and outcome metrics",
            project="project_beacon",
            meeting_dir=str(meeting_dir),
            runtime_root=str(runtime_root),
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = handle_copilot(args)
        assert res["status"] == "PASS"
        stdout_txt = buf.getvalue().strip()
        assert stdout_txt.startswith("```text\n") and stdout_txt.endswith("\n```")
        body = stdout_txt[len("```text\n") : -len("\n```")].strip()
        assert len(body.split()) <= 40
        assert "candidate ai-sdlc control" not in body.lower()
        assert "person-specific attribution" not in body.lower()
        assert any(aid in res["cited_assertion_ids"] for aid in ("asrt_sherpa_kpi_gap", "asrt_sherpa_scorecard"))

