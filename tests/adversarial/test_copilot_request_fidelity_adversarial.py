"""Adversarial and regression test suite for Phase 4: Copilot request fidelity and grounding."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.copilot_handlers import handle_copilot


def _setup_regression_graph(runtime_root: Path) -> str:
    """Sets up an isolated SQLite graph with relevant concurrency assertions and unrelated person distractions."""
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "graph.sqlite"
    con = sqlite3.connect(db_path)
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

    con.execute("INSERT INTO node VALUES ('person:mitchell_simmons', 'Alex Rivera', 'Alex Rivera', 'person', 'Delivery Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:jen_davis', 'Jen Davis', 'Jen Davis', 'person', 'Product Manager', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:dave_cooper', 'Dave Cooper', 'Dave Cooper', 'person', 'Security Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_sqlite', 'SQLite Engine', 'SQLite Engine', 'system', 'Write serializer', 'active', 1)")

    con.execute("INSERT INTO source VALUES ('s1', 'Architecture Review 10/01/2026', '10/01/2026')")

    # Relevant assertion: addresses load and agents concurrency
    con.execute("""
        INSERT INTO assertion VALUES (
            'ast-concurrency-001', 'anchor_sqlite', 'CONSTRAINS', NULL,
            'SQLite write serializer limits concurrent agent load to 1 writer',
            'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's1'
        )
    """)

    # Distraction assertion: attached to Alex Rivera, but completely unrelated to load/agents
    con.execute("""
        INSERT INTO assertion VALUES (
            'ast-distraction-002', 'person:mitchell_simmons', 'ROLE', NULL,
            'Delivery Lead for client engagements',
            'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's1'
        )
    """)

    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": "generation-reg-01",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return sha256


class TestCopilotRequestFidelityAdversarial(unittest.TestCase):
    """Adversarial and production regression tests for Phase 4."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_req_adv_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.graph_sha = _setup_regression_graph(self.runtime_root)
        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write transcript with relevant Mitch utterance
        seg_file = self.meeting_dir / "segments.jsonl"
        self.turns = [
            {"seq": 1, "speaker": "Jen Davis", "text": "Let us review current deliverables.", "timestamp": "00:00:10", "turn_sec": 10.0},
            {"seq": 2, "speaker": "Alex Rivera", "text": "I am concerned about high system load when 16 agents run concurrently on SQLite.", "timestamp": "00:00:25", "turn_sec": 25.0},
            {"seq": 3, "speaker": "Amit Ayer", "text": "We should check the write serializer.", "timestamp": "00:00:35", "turn_sec": 35.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in self.turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_canonical_regression_request_fidelity_and_distraction_filtering(self) -> None:
        """
        Regression Request: 'React to Mitch's comment about load and agents.'
        Must:
        1. Resolve 'Mitch' to 'Alex Rivera'
        2. Select Turn 2 (load and agents comment)
        3. Cite 'ast-concurrency-001' (load assertion)
        4. Exclude 'ast-distraction-002' (person role assertion)
        5. Record distinguishable evidence breakdown in answers and attempts
        """
        args = argparse.Namespace(
            request="React to Mitch's comment about load and agents.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "PASS", f"Copilot execution failed: {res}")
        self.assertEqual(res.get("intent"), "respond")
        self.assertEqual(res.get("target"), "Alex Rivera")

        # Crucial invariant: The cited assertion must be the concurrency assertion, NOT the distraction role!
        cited = res.get("cited_assertion_ids", [])
        self.assertIn("ast-concurrency-001", cited)
        self.assertNotIn("ast-distraction-002", cited)

        # Verify evidence breakdown preserves distinct source types
        eb = res.get("evidence_breakdown", {})
        self.assertTrue(bool(eb.get("acoustic_transcript_evidence")))
        self.assertTrue(bool(eb.get("historical_graph_assertions")))
        acoustic_entry = eb["acoustic_transcript_evidence"][0]
        self.assertIn("Alex Rivera", acoustic_entry["speaker"])
        self.assertIn("load", acoustic_entry["matched_keywords"])

        # Verify copilot_answers.jsonl
        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        answers = [json.loads(line) for line in answers_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(answers), 1)
        self.assertIn("Mitch", answers[0]["response"])
        self.assertIn("SQLite write serializer limits concurrent agent load", answers[0]["response"])

        # Verify copilot_attempts.jsonl
        attempts_file = self.meeting_dir / "copilot_attempts.jsonl"
        self.assertTrue(attempts_file.is_file())
        attempts = [json.loads(line) for line in attempts_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["terminal_outcome"], "SUCCESS")
        self.assertEqual(attempts[0]["request"], "React to Mitch's comment about load and agents.")

    def test_ambiguous_speaker_identity_fails_closed(self) -> None:
        """If multiple roster candidates match the query alias, copilot must fail closed without guessing."""
        # Add another Mitchell to the graph
        db_path = self.runtime_root / "graph.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("INSERT INTO node VALUES ('person:mitchell_davis', 'Mitchell Davis', 'Mitchell Davis', 'person', 'Advisor', 'active', 1)")
        con.commit()
        con.close()

        # Update active_pair SHA
        sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
        (self.runtime_root / "active_pair.json").write_text(json.dumps({
            "schema_version": "1.0.0",
            "graph_path": "graph.sqlite",
            "graph_sha256": sha256,
            "generation_id": "gen-ambiguous",
        }), encoding="utf-8")

        args = argparse.Namespace(
            request="React to Mitch's comment about load and agents.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "AMBIGUOUS_SPEAKER_IDENTITY")
        self.assertEqual(res.get("failure_category"), "usage")

    def test_missing_topic_utterance_fails_closed(self) -> None:
        """If target speaker exists in transcript but never spoke about requested topic, fail closed."""
        args = argparse.Namespace(
            request="React to Mitch's comment about quantum cryptography.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "ZERO_RELEVANT_UTTERANCES")

    def test_missing_topic_graph_evidence_fails_closed_without_falling_back_to_distractions(self) -> None:
        """
        If Mitch spoke about 'quantum networking' and graph has distraction assertions about Mitch,
        it must fail closed (ZERO_RELEVANT_ASSERTIONS) rather than returning unrelated person assertions.
        """
        # Overwrite transcript turn 2 with 'quantum networking'
        seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {"seq": 1, "speaker": "Jen Davis", "text": "Review.", "timestamp": "00:00:10", "turn_sec": 10.0},
            {"seq": 2, "speaker": "Alex Rivera", "text": "I noticed quantum networking instability.", "timestamp": "00:00:25", "turn_sec": 25.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        args = argparse.Namespace(
            request="React to Mitch's comment about quantum networking.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        # Must fail closed on absent topic assertions, NOT fall back to person:mitchell_simmons Delivery Lead!
        self.assertIn(res.get("error"), ("ZERO_RELEVANT_ASSERTIONS", "ZERO_MATCHING_ASSERTIONS"))

    def test_target_speaker_with_no_turns_fails_closed(self) -> None:
        """If target speaker is known in roster but never spoke in transcript, fail closed."""
        args = argparse.Namespace(
            request="React to Dave's comment about load and agents.",  # Dave is in roster but has 0 turns in transcript
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "SPEAKER_HAS_NO_TURNS")

    def test_target_speaker_not_in_roster_fails_closed(self) -> None:
        """If target speaker is completely unknown in roster, fail closed."""
        args = argparse.Namespace(
            request="React to Alice's comment about load and agents.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "SPEAKER_NOT_IN_ROSTER")

    def test_request_and_topic_override_fusion_preserves_request_keywords(self) -> None:
        """Passing both --request and --topic must fuse keywords rather than dropping --request."""
        from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request

        parsed = parse_copilot_request(
            raw_request="challenge productivity increase from Speaker_Beta",
            target_override="Speaker_Beta",
            topic_override="metrics",
            intent_override="respond",
        )
        self.assertEqual(parsed.target_speaker, "Speaker_Beta")
        self.assertIn("metrics", parsed.topic_keywords)
        self.assertIn("productivity", parsed.topic_keywords)
        self.assertIn("increase", parsed.topic_keywords)
        self.assertNotIn("ninan", [k.lower() for k in parsed.topic_keywords])

