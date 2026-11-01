"""Adversarial cross-stage tests for Phase 5: Monotonic rejection, graph pinning, and rendering fidelity."""
import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from typing import Tuple

import numpy as np

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.cli.copilot_speech import render_speaker_comments
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
    check_assertion_validity,
    rerank_assertions_vectorized,
)


def _setup_test_graph(runtime_root: Path) -> Tuple[str, str]:
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

    con.execute("INSERT INTO node VALUES ('person:mitchell_simmons', 'Alex Rivera', 'Alex Rivera', 'person', 'Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_sqlite', 'SQLite Engine', 'SQLite Engine', 'system', 'Write serializer', 'active', 1)")
    con.execute("INSERT INTO source VALUES ('s1', 'Review 10/01/2026', '10/01/2026')")

    # Valid assertion
    con.execute("""
        INSERT INTO assertion VALUES (
            'ast-valid-001', 'anchor_sqlite', 'CONSTRAINS', NULL,
            'SQLite write serializer limits concurrent agent load to 1 writer',
            'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's1'
        )
    """)
    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    gen_id = "gen-phase5-pinned-01"
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": gen_id,
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return sha256, gen_id


class TestCopilotGroundingMonotonicAdversarial(unittest.TestCase):
    """Cross-stage adversarial tests covering the 6 Phase 5 acceptance scenarios."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_p5_adv_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.graph_sha, self.gen_id = _setup_test_graph(self.runtime_root)
        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        seg_file = self.meeting_dir / "segments.jsonl"
        self.turns = [
            {"seq": 1, "speaker": "Alex Rivera", "text": "I am concerned about high system load when 16 agents run concurrently on SQLite.", "timestamp": "00:00:25", "turn_sec": 25.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in self.turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adversarial_fallback_retrieval(self) -> None:
        """Fallback when dense vector is None must rerank using normalized lexical/structural mixture without error."""
        candidates = [
            {"assertion_id": "c1", "claim_text": "Concurrent agents load", "predicate": "CONSTRAINS", "is_priority": True},
            {"assertion_id": "c2", "claim_text": "Arbitrary unrelated text", "predicate": "MENTIONS"},
        ]
        reranked, fallback = rerank_assertions_vectorized(
            candidates,
            query_tokens={"concurrent", "agents", "load"},
            query_vector=None,
        )
        self.assertTrue(fallback)
        self.assertEqual(len(reranked), 2)
        # c1 has higher lexical overlap and priority predicate, so ranks first
        self.assertEqual(reranked[0]["assertion_id"], "c1")

    def test_adversarial_missing_provenance(self) -> None:
        """Candidates with missing evidence and unanchored sources must fail provenance and never escape."""
        verifier = CopilotGroundingVerifier(min_provenance_score=0.35)
        candidates = [
            {"assertion_id": "p_good", "is_active": True, "claim_text": "Proven fact", "evidence_ids": ["ev1", "ev2"], "source_ids": ["s1"]},
            {"assertion_id": "p_empty", "is_active": True, "claim_text": "Hearsay", "evidence_ids": [], "source_ids": []},
            {"assertion_id": "p_none", "is_active": True, "claim_text": "Fabrication", "evidence_ids": None, "source_ids": None},
        ]
        res = verifier.verify_and_ground(candidates, dialogue="Proven fact")
        verified_ids = [c["assertion_id"] for c in res.verified_assertions]
        self.assertIn("p_good", verified_ids)
        self.assertNotIn("p_empty", verified_ids)
        self.assertNotIn("p_none", verified_ids)

    def test_adversarial_deadline_injection_after_rejection(self) -> None:
        """Injecting a deadline breach must NEVER resurrect candidates rejected across validity, provenance, or conflict."""
        verifier = CopilotGroundingVerifier(budget_ms=0.00001, min_provenance_score=0.4)
        candidates = [
            # Rejected in Validity (quarantined)
            {"assertion_id": "bad_q", "claim_text": "Career advisory note", "namespace": "INTERNAL_ADVISORY", "is_quarantined": 1},
            # Rejected in Validity (stale)
            {"assertion_id": "bad_stale", "claim_text": "Stale fact", "stale_after": "2020-01-01"},
            # Rejected in Provenance (low provenance)
            {"assertion_id": "bad_lowprov", "is_active": True, "claim_text": "Unverified rumor", "evidence_ids": []},
            # Rejected in Conflict (contradictory)
            {"assertion_id": "bad_conf1", "is_active": True, "subject_node_id": "system_x", "predicate": "STATE", "object_literal": "on", "claim_text": "System is on", "evidence_ids": ["e1"]},
            {"assertion_id": "bad_conf2", "is_active": True, "subject_node_id": "system_x", "predicate": "STATE", "object_literal": "off", "claim_text": "System is off", "evidence_ids": ["e2"]},
            # Legitimate survivor
            {"assertion_id": "good_survivor", "is_active": True, "subject_node_id": "system_y", "predicate": "STATE", "object_literal": "ready", "claim_text": "System is ready", "evidence_ids": ["e3", "e4"], "source_ids": ["s1", "s2"]},
        ]
        res = verifier.verify_and_ground(candidates, dialogue="System is ready")
        self.assertTrue(res.is_budget_breached)
        verified_ids = [c["assertion_id"] for c in res.verified_assertions]
        # Zero rejected candidates restored
        self.assertNotIn("bad_q", verified_ids)
        self.assertNotIn("bad_stale", verified_ids)
        self.assertNotIn("bad_lowprov", verified_ids)
        self.assertNotIn("bad_conf1", verified_ids)
        self.assertNotIn("bad_conf2", verified_ids)
        self.assertEqual(verified_ids, ["good_survivor"])

    def test_adversarial_changing_graph_pointers(self) -> None:
        """When an assertion claims a different generation than pinned for the request, it fails closed."""
        cand_wrong_gen = {
            "assertion_id": "cand_rogue",
            "is_active": True,
            "claim_text": "Foreign generation assertion",
            "_pinned_generation": "generation-ROGUE-999",
            "_pinned_graph_sha": self.graph_sha,
        }
        ok, reason = check_assertion_validity(
            cand_wrong_gen,
            pinned_generation=self.gen_id,
            packet_graph_sha=self.graph_sha,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "GRAPH_POINTER_MISMATCH")

        cand_wrong_sha = {
            "assertion_id": "cand_rogue_sha",
            "is_active": True,
            "claim_text": "Foreign SHA assertion",
            "_pinned_generation": self.gen_id,
            "_pinned_graph_sha": "sha256-untrusted-hash",
        }
        ok2, reason2 = check_assertion_validity(
            cand_wrong_sha,
            pinned_generation=self.gen_id,
            packet_graph_sha=self.graph_sha,
        )
        self.assertFalse(ok2)
        self.assertEqual(reason2, "GRAPH_POINTER_MISMATCH")

    def test_adversarial_rendering_failures_distinguish_malformed_vs_word_budget(self) -> None:
        """Verify CLI error paths distinguish malformed representations from word budget exhaustion."""
        # 1. Word budget exhaustion: valid assertions exist, but word budget is 2 words
        args_tight = argparse.Namespace(
            request="React to Mitch's comment about load and agents.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
            words=2,  # Word budget 2: cannot fit "Mitch, could we clarify SQLite write serializer..."
        )
        res_tight = handle_copilot(args_tight)
        self.assertEqual(res_tight.get("status"), "FAIL")
        self.assertIn(res_tight.get("error"), ("WORD_BUDGET_EXHAUSTED", "NO_SPEAKABLE_ASSERTIONS"))
        self.assertEqual(res_tight.get("failure_category"), "rendering_failure")

        # 2. Malformed representation: assertion object_literal/nodes are missing
        retriever_mock = MagicMock()
        retriever_mock._topology = None
        malformed = [{"assertion_id": "m1", "subject_node_id": None, "object_literal": None}]
        render_res = render_speaker_comments("respond", "Mitch", malformed, max_words=20, retriever=retriever_mock)
        self.assertEqual(render_res.body, "")
        self.assertEqual(render_res.failure_reason, "MALFORMED_REPRESENTATION")

    def test_adversarial_long_first_short_second_candidate_execution(self) -> None:
        """End-to-end rendering must render a short second candidate when the first candidate is oversized."""
        retriever_mock = MagicMock()
        retriever_mock._topology = None

        long_cand = {
            "assertion_id": "ast_oversized_first",
            "object_literal": "This is an extremely long multi-clause assertion description that deliberately consumes forty words to exceed typical real-time spoken executive word limits and verify non-blocking handling of subsequent shorter candidates.",
        }
        short_cand = {
            "assertion_id": "ast_concise_second",
            "object_literal": "SQLite uses single-writer serialization.",
        }

        render_res = render_speaker_comments(
            intent="respond",
            target="Mitch",
            assertions=[long_cand, short_cand],
            max_words=12,  # Budget 12 words: long_cand (~32 words) fails; short_cand (4 words) fits cleanly!
            retriever=retriever_mock,
        )
        self.assertNotEqual(render_res.body, "")
        self.assertEqual(render_res.rendered_count, 1)
        self.assertEqual(render_res.budget_exhausted_count, 1)
        self.assertIn("SQLite uses single-writer serialization", render_res.body)


if __name__ == "__main__":
    unittest.main()
