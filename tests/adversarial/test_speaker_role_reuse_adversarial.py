#!/usr/bin/env python3
"""
tests/adversarial/test_speaker_role_reuse_adversarial.py

Adversarial and stress test suite for Wave 4: Speaker role reuse invariants.
Covers:
1. Boundary payload & Malformed input (corrupted timestamps, NaN/Inf confidence, malformed JSON).
2. Invariant bypass attempt (quarantine bypass, invalid predicates).
3. Concurrency & race (multi-threaded concurrent resolution & cache invalidation).
4. Exact production regression (conflicting equally valid roles deadlock requiring HITL).
5. Cross-runtime namespace isolation.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    invalidate_resolver_cache,
    resolve_person_entity,
)


def _init_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            confidence REAL DEFAULT 1.0,
            status TEXT DEFAULT 'CONFIRMED',
            observed_at TEXT,
            stale_after TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            validation_state TEXT,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


def _setup_runtime_root(base_dir: Path, gen_id: str = "gen-adv-001") -> Path:
    rt_root = base_dir / "runtime"
    rt_root.mkdir(parents=True, exist_ok=True)
    db_path = rt_root / "CANONICAL_GRAPH.db"
    _init_test_graph(db_path)
    ap = {
        "generation_id": gen_id,
        "graph_path": "CANONICAL_GRAPH.db",
        "index_path": "CANONICAL_GRAPH.idx",
    }
    (rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")
    return rt_root


class TestSpeakerRoleReuseAdversarial(unittest.TestCase):
    """Adversarial and stress test cases for speaker role reuse."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.rt_root = _setup_runtime_root(self.work_dir)
        self.db_path = self.rt_root / "CANONICAL_GRAPH.db"
        invalidate_resolver_cache()

    def tearDown(self) -> None:
        invalidate_resolver_cache()
        self.temp_dir.cleanup()

    def test_adversarial_malformed_timestamps_and_confidence(self) -> None:
        """Category: Malformed input — corrupt timestamps and confidence values fail open to safe defaults."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json, node_type, active) "
            "VALUES ('p_bad', 'Bad Data Person', 'Bad Data Person', 'Role: Default Title', '{bad-json', 'person', 1)"
        )
        # Assertion with unparseable observed_at, stale_after, and confidence text
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, observed_at, stale_after, status) "
            "VALUES ('ass_bad_01', 'p_bad', 'HAS_ROLE', 'Corrupted Fact Role', 'not-a-float', 'not-a-timestamp', '9999-99-99', 'CONFIRMED')"
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            as_of_timestamp="invalid-as-of-time",
            enable_graph_query=True,
        )
        # Should not raise exception
        ent = resolver.resolve("Bad Data Person")
        self.assertIsNotNone(ent)
        # Corrupted confidence falls back to 1.0; role parsed without crash
        self.assertEqual(ent.role, "Corrupted Fact Role")
        self.assertEqual(ent.confidence, 1.0)

    def test_adversarial_quarantine_bypass_blocked(self) -> None:
        """Category: Invariant bypass — quarantined assertion cannot set truth even with 1.0 confidence."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json, node_type, active) "
            "VALUES ('p_quar', 'Quarantined Person', 'Quarantined Person', 'Role: Baseline Role', '{}', 'person', 1)"
        )
        # Attempt 1: is_quarantined = 1
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, is_quarantined) "
            "VALUES ('ass_q1', 'p_quar', 'HAS_ROLE', 'Fake Malicious Role 1', 1.0, 1)"
        )
        # Attempt 2: validation_state = 'QUARANTINED'
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, validation_state) "
            "VALUES ('ass_q2', 'p_quar', 'HAS_ROLE', 'Fake Malicious Role 2', 1.0, 'QUARANTINED')"
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Quarantined Person")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Baseline Role")
        self.assertEqual(ent.source, "knowledge_graph")

    def test_adversarial_unsupported_predicate_bypass_blocked(self) -> None:
        """Category: Invariant bypass — non-role predicates cannot inject or overwrite speaker roles."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json, node_type, active) "
            "VALUES ('p_pred', 'Predicate Person', 'Predicate Person', 'Role: Legitimate Role', '{}', 'person', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence) "
            "VALUES ('ass_m1', 'p_pred', 'MENTIONS', 'Injected Mentions Role', 1.0)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence) "
            "VALUES ('ass_m2', 'p_pred', 'WORKS_ON_PROJECT', 'Injected Project Role', 1.0)"
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Predicate Person")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Legitimate Role")

    def test_adversarial_multi_threaded_concurrency_stress(self) -> None:
        """Category: Concurrency & race — 16 parallel threads querying and invalidating cache simultaneously."""
        conn = sqlite3.connect(str(self.db_path))
        for i in range(20):
            p_id = f"p_stress_{i:02d}"
            name = f"Stress Person {i:02d}"
            role = f"Role Title {i:02d}"
            conn.execute(
                "INSERT INTO node VALUES (?, ?, ?, ?, '{}', 'person', 1)",
                (p_id, name, name, f"Role: Old {i}"),
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
                "VALUES (?, ?, 'HAS_ROLE', ?, 0.95, 'CONFIRMED')",
                (f"ass_{p_id}", p_id, role),
            )
        conn.commit()
        conn.close()

        errors = []

        def worker_task(thread_id: int) -> bool:
            try:
                for round_idx in range(10):
                    target_name = f"Stress Person {round_idx:02d}"
                    ent = resolve_person_entity(target_name, runtime_root=self.rt_root)
                    if ent is None or ent.role != f"Role Title {round_idx:02d}":
                        return False
                    if round_idx % 3 == 0:
                        invalidate_resolver_cache()
                return True
            except Exception as exc:
                errors.append(exc)
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, i) for i in range(16)]
            results = [f.result() for f in futures]

        self.assertEqual(len(errors), 0, f"Concurrent exceptions: {errors}")
        self.assertTrue(all(results))

    def test_adversarial_conflicting_identical_confidence_deadlock(self) -> None:
        """Category: Exact production regression — two competing claims at identical 1.0 confidence cannot guess."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, properties_json, node_type, active) "
            "VALUES ('p_lead', 'Executive Leader', 'Executive Leader', 'Role: Participant', '{}', 'person', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status, observed_at) "
            "VALUES ('ass_c1', 'p_lead', 'HAS_ROLE', 'Chief Technology Officer', 1.0, 'CONFIRMED', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status, observed_at) "
            "VALUES ('ass_c2', 'p_lead', 'HAS_ROLE', 'Chief Product Officer', 1.0, 'CONFIRMED', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Executive Leader")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.status, "AMBIGUOUS_CONFLICT")
        self.assertTrue(ent.requires_operator_confirmation)
        self.assertIn("Chief Technology Officer", ent.role)
        self.assertIn("Chief Product Officer", ent.role)

    def test_adversarial_cross_runtime_isolation_strict(self) -> None:
        """Category: Invariant bypass — attempting to resolve relative or arbitrary paths stays bounded."""
        sub_root = self.work_dir / "isolated_root"
        sub_root.mkdir(parents=True, exist_ok=True)
        # Empty root without graph or active pair
        ent = resolve_person_entity("Nonexistent Person", runtime_root=sub_root)
        self.assertIsNone(ent)


if __name__ == "__main__":
    unittest.main()
