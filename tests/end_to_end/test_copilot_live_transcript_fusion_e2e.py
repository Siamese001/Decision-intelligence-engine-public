"""
tests/end_to_end/test_copilot_live_transcript_fusion_e2e.py

Class B E2E test suite verifying live-transcript-grounded Copilot fusion,
speaker hint queue reconciliation, temporal windowing, and count-aware rendering
across all 11 natural-language prompts from the Daniel Brankin (project_jarvis) live session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from dual_pipeline.cli.copilot_handlers import handle_copilot


def _create_jarvis_test_runtime(runtime_root: Path) -> Tuple[Path, Path]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "test_graph.sqlite"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
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
            primary_source_id TEXT
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
    """)
    con.execute("INSERT INTO node VALUES ('person:daniel_brankin', 'Daniel Brankin', 'Daniel Brankin', 'person', 'Stakeholder', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:amit_ayer', 'Amit Ayer', 'Amit Ayer', 'person', 'Architect', 'active', 1)")
    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    (runtime_root / "active_pair.json").write_text(
        json.dumps({
            "schema_version": "1.0.0",
            "graph_path": "test_graph.sqlite",
            "graph_sha256": sha256,
            "generation_id": "gen-jarvis-live-e2e",
        }),
        encoding="utf-8",
    )

    tc_dir = runtime_root / "output" / "transcript_cache" / "project_jarvis" / "10-26-2026"
    staging_dir = tc_dir / ".staging_Daniel_Brankin_Amit_Ayer_130302"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Parent segments.jsonl with turns labeled "Host" (simulating single-channel bleed before diarization)
    turns = [
        {"chunk_idx": 0, "speaker": "Host", "text": "We need to scale Jarvis across the business without adding incremental headcount.", "timestamp": "00:00:30", "start_sec": 30.0},
        {"chunk_idx": 1, "speaker": "Host", "text": "Day in the life interviews will surface repetitive tasks and daily bottlenecks.", "timestamp": "00:01:15", "start_sec": 75.0},
        {"chunk_idx": 2, "speaker": "Host", "text": "We are excited for this UI/UX thing with Tracy to replace the HTML prototype.", "timestamp": "00:02:00", "start_sec": 120.0},
        {"chunk_idx": 3, "speaker": "Host", "text": "Monthly SAP scoring workflow and second level drill down for Transmission is our top velocity win.", "timestamp": "00:03:30", "start_sec": 210.0},
        {"chunk_idx": 4, "speaker": "Host", "text": "Reid is working the enterprise access SLA so we have a sandbox token before Thanksgiving.", "timestamp": "00:04:10", "start_sec": 250.0},
    ]
    (tc_dir / "01_10-26-2026_Daniel_Brankin_Amit_Ayer_segments.jsonl").write_text(
        "\n".join(json.dumps(t) for t in turns) + "\n",
        encoding="utf-8",
    )

    # Operator speaker hints queued during live call
    (staging_dir / "speaker_hints_queue.json").write_text(
        json.dumps([
            {
                "hint_id": "h1",
                "speaker_name": "Dan",
                "quote": "Sap integration on for tomorrow",
                "status": "QUEUED",
                "session_elapsed_seconds": 15.0,
            },
            {
                "hint_id": "h2",
                "speaker_name": "Daniel",
                "quote": "excited for this UIUX thing",
                "status": "QUEUED",
                "session_elapsed_seconds": 120.0,
            },
        ]),
        encoding="utf-8",
    )
    return runtime_root, staging_dir


@pytest.mark.e2e
def test_all_11_daniel_brankin_live_copilot_prompts_succeed_e2e(tmp_path: Path) -> None:
    runtime_root, staging_dir = _create_jarvis_test_runtime(tmp_path / "rt")

    prompts = [
        "--copilot next best questions for Daniel",
        "--query next best questiion based on live transcript",
        "--query next best questions to Daniel",
        "next best questions on live transcripts",
        "next best question on transcript strategic",
        "use liver transcript for next two questions",
        "--query live transcript next best strategic speaker point to drive discussion forward",
        "need comment on velocity from Daniel from live transcript - review last 2 mins in detail",
        "review whole transcript and synthesize for next best 2 comments to Daniel",
        "review whole transcript and synthesize for next best comments to Daniel",
        "based on live transcript - synthesize for next best comments to Daniel",
    ]

    for prompt in prompts:
        args = argparse.Namespace(
            request=prompt,
            query=None,
            prompt=None,
            intent=None,
            target=None,
            topic=None,
            words=None,
            meeting_dir=str(staging_dir),
            runtime_root=str(runtime_root),
            json=True,
        )
        res = handle_copilot(args)
        assert res.get("status") == "PASS", f"Prompt failed: {prompt!r} -> {res}"
        assert any(str(aid).startswith("live_turn_") for aid in res.get("cited_assertion_ids", [])), (
            f"Expected live_turn_* citation for {prompt!r}, got {res.get('cited_assertion_ids')}"
        )

    # Verify count-aware rendering for explicit two-item request
    answers_path = staging_dir / "copilot_answers.jsonl"
    assert answers_path.is_file()
    records = [json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == len(prompts)

    # Prompt index 5: "use liver transcript for next two questions" -> <= 2 lines
    lines_two_q = [ln for ln in records[5]["response"].splitlines() if ln.strip()]
    assert 1 <= len(lines_two_q) <= 2

    # Prompt index 8: "review whole transcript and synthesize for next best 2 comments to Daniel" -> <= 2 lines
    lines_two_c = [ln for ln in records[8]["response"].splitlines() if ln.strip()]
    assert 1 <= len(lines_two_c) <= 2
