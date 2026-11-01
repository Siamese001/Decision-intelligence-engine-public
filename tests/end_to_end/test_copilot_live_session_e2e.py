"""
tests/end_to_end/test_copilot_live_session_e2e.py

End-to-End lifecycle verification for Live Copilot during active meeting recording.
Verifies:
1. LiveBufferReader polymorphic session resolution: accepts either session ID string or SessionManifest.
2. Acoustic turn fallback in UtteranceSelector: resolves turns when acoustic clustering is still consolidating (Voice A / Voice B).
3. End-to-end execution of copilot requests with 50 live turns, generating grounded speakable output.
4. Persistence of AnswerRecord in AnswerLedger and copilot_answers.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.answer_ledger import get_default_answer_ledger
from dual_pipeline.live_meeting_recorder_core.copilot_context import UtteranceSelector
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


def _init_test_graph_db(db_path: Path) -> None:
    """Populates an isolated test graph database with Nexus and NEA assertions."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            engagement_id TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1
        )"""
    )
    cur.execute(
        """CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )"""
    )
    cur.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            engagement_id TEXT,
            canonical_status TEXT DEFAULT 'ACTIVE',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality REAL DEFAULT 1.0,
            classification TEXT DEFAULT 'INTERNAL',
            allowed_audiences_json TEXT DEFAULT '[]',
            scope_text TEXT,
            primary_source_id TEXT
        )"""
    )
    cur.execute(
        """CREATE VIEW v_canonical_edge AS
        SELECT assertion_id, subject_node_id AS from_node_id,
               'RELATION' AS relationship_family, predicate AS edge_type,
               object_node_id AS to_node_id, materiality,
               primary_source_id AS source_id, 0 AS hitl_required
          FROM assertion
         WHERE object_node_id IS NOT NULL"""
    )
    cur.execute("INSERT INTO source VALUES ('src-intake-1', 'Intake Package Nexus-NEA')")
    cur.executemany(
        "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, 'active', 1)",
        [
            ("node_nexus", "Project Nexus", "Nexus", "project", "project:project_nexus", "Nexus initiative"),
            ("node_nea", "NEA Architecture", "NEA", "project", "project:nea", "Apex Global Architecture"),
            ("node_cweos", "GRID_OS Enterprise System", "GRID_OS", "technical_anchor", "project:nea", "GRID_OS core platform"),
            ("node_mandy", "Mandy", "Mandy", "person", "project:nea", "Tech lead for GRID_OS stream"),
            ("node_jeffrey", "Jeffrey", "Jeffrey", "person", "project:project_nexus", "Nexus engineering director"),
        ],
    )
    cur.executemany(
        "INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 1, 1.0, 'INTERNAL', '[]', ?, 'src-intake-1')",
        [
            ("ast-nexus-nea", "node_nexus", "RELATIONSHIP_FAMILY", "node_nea", None, "project:project_nexus", "project_nexus"),
            ("ast-grid_os-arch", "node_cweos", "DESCRIBES", None, "GRID_OS enterprise orchestration architecture and technical delivery", "project:nea", "nea"),
            ("ast-mandy-role", "node_mandy", "ROLE", None, "Mandy serves as tech lead with high expectations on GRID_OS architectural alignment", "project:nea", "nea"),
        ],
    )
    conn.commit()
    conn.close()


def _generate_50_turns() -> List[Dict[str, Any]]:
    """Generates 50 turns: first 25 acoustic placeholders (Voice A/Voice B), remaining 25 named."""
    turns: List[Dict[str, Any]] = []
    for seq in range(1, 26):
        spk = "Voice A" if seq % 2 == 1 else "Voice B"
        if seq == 12:
            txt = "We need to align on Mandy's role as tech lead and expectations with Jeffrey regarding GRID_OS delivery."
        elif seq == 14:
            txt = "Mandy has concerns about the architectural timeline and technical expectations."
        else:
            txt = f"Reviewing active delivery checkpoint segment number {seq}."
        sec = float(seq * 5.0)
        ts = f"00:0{seq // 12}:{(seq % 12) * 5:02d}"
        turns.append({"seq": seq, "speaker": spk, "text": txt, "timestamp": ts, "turn_sec": sec})

    for seq in range(26, 51):
        spk = "Jeffrey" if seq % 2 == 0 else "Amit"
        txt = f"Engineering synchronization update discussing milestones at stage {seq}."
        sec = float(seq * 5.0)
        ts = f"00:0{seq // 12}:{(seq % 12) * 5:02d}"
        turns.append({"seq": seq, "speaker": spk, "text": txt, "timestamp": ts, "turn_sec": sec})
    return turns


def test_live_buffer_reader_polymorphic_session_resolution():
    """Verify LiveBufferReader.read_turns resolves transparently from either session ID string or SessionManifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        reg_dir = tmp_path / ".session_registry"
        out_dir = tmp_path / "output" / "project_nexus" / "10-09-2026" / "Jeffrey_Amit_Catchup"
        out_dir.mkdir(parents=True, exist_ok=True)
        reg_dir.mkdir(parents=True, exist_ok=True)

        session_id = "sess-live-nexus-polymorphic-001"
        turns = _generate_50_turns()

        # Write segments.jsonl
        segments_file = out_dir / "segments.jsonl"
        with open(segments_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        # Write transcript.md
        transcript_file = out_dir / "transcript.md"
        with open(transcript_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(f"**[{t['timestamp']}]** {t['speaker']}: {t['text']}\n")

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id=session_id,
            engagement_id="project:project_nexus",
            engagement_name="Nexus",
            meeting_slug="jeffrey_amit_catchup",
            attendees=["Jeffrey", "Amit"],
            output_dir=str(out_dir.relative_to(tmp_path)),
            transcript_md=str(transcript_file.relative_to(tmp_path)),
        )

        reader = LiveBufferReader(tmp_path)

        # 1. Read turns via SessionManifest object
        turns_from_manifest = reader.read_turns(manifest)
        assert len(turns_from_manifest) == 50
        assert turns_from_manifest[11]["speaker"] == "Voice B"

        # 2. Read turns via session_id string (polymorphic resolution)
        turns_from_str = reader.read_turns(session_id)
        assert len(turns_from_str) == 50
        assert turns_from_str[11]["text"] == turns[11]["text"]

        # 3. Read transcript via session_id string
        md_text = reader.read_transcript(session_id)
        assert "Voice A:" in md_text
        assert "Jeffrey:" in md_text


def test_copilot_acoustic_fallback_and_e2e_answer_persistence():
    """Verify acoustic turn fallback in UtteranceSelector and end-to-end AnswerRecord persistence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        reg_dir = tmp_path / ".session_registry"
        out_dir = tmp_path / "output" / "project_nexus" / "10-09-2026" / "Jeffrey_Amit_Catchup"
        out_dir.mkdir(parents=True, exist_ok=True)
        reg_dir.mkdir(parents=True, exist_ok=True)

        session_id = "sess-live-nexus-acoustic-002"
        turns = _generate_50_turns()

        segments_file = out_dir / "segments.jsonl"
        with open(segments_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        transcript_file = out_dir / "transcript.md"
        with open(transcript_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(f"**[{t['timestamp']}]** {t['speaker']}: {t['text']}\n")

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id=session_id,
            engagement_id="project:project_nexus",
            engagement_name="Nexus",
            meeting_slug="jeffrey_amit_catchup",
            attendees=["Jeffrey", "Amit"],
            output_dir=str(out_dir.relative_to(tmp_path)),
            transcript_md=str(transcript_file.relative_to(tmp_path)),
        )

        graph_path = tmp_path / "nexus_graph.sqlite"
        _init_test_graph_db(graph_path)

        active_pair = {
            "graph_path": "nexus_graph.sqlite",
            "graph_sha256": None,
            "generation_id": "gen-test-001",
        }
        (tmp_path / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")

        # 1. Verify UtteranceSelector acoustic fallback
        # When turns for "Mandy" are still labeled as "Voice A" / "Voice B",
        # UtteranceSelector falls back to acoustic turns and finds matching topic.
        sel_res = UtteranceSelector.select(
            turns=turns[:25],
            target_speaker="Mandy",
            topic_keywords=["tech", "lead", "expectations", "grid_os"],
            window_sec=30.0,
        )
        assert sel_res.status == "SELECTED"
        assert sel_res.selected_utterance is not None
        assert "Mandy's role as tech lead" in sel_res.selected_utterance.text
        assert sel_res.selected_utterance.speaker in ("Voice A", "Voice B")

        # 2. Execute end-to-end copilot query targeting cross-project NEA context from Nexus session
        args = argparse.Namespace(
            session=session_id,
            active=False,
            request="what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey",
            intent=None,
            topic=None,
            target=None,
            words=60,
            json=True,
            meeting_dir=str(out_dir),
            mock=True,
            project="project_nexus",
            runtime_root=str(tmp_path),
            graph=str(graph_path),
        )

        res = execute_copilot_request(args)
        assert res.get("status") == "PASS", f"Copilot execution failed: {res}"
        assert res.get("command") == "copilot"
        assert res.get("intent") in ("graph-context", "respond")

        # 3. Verify AnswerRecord was written to output_dir/copilot_answers.jsonl
        answers_file = out_dir / "copilot_answers.jsonl"
        assert answers_file.exists()
        answer_lines = [json.loads(line) for line in answers_file.read_text(encoding="utf-8").strip().splitlines()]
        assert len(answer_lines) >= 1
        latest_answer = answer_lines[-1]
        assert latest_answer.get("response") != ""

        # 4. Verify AnswerRecord was recorded in AnswerLedger
        ledger = get_default_answer_ledger(tmp_path)
        persisted_answers = ledger.list_answers(session_id=session_id)
        assert len(persisted_answers) >= 1
        assert persisted_answers[0].session_id == session_id
        assert persisted_answers[0].response_text != ""
