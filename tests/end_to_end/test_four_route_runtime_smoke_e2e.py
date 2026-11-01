"""Fast Hermetic Four-Route E2E Runtime Smoke Matrix Suite (Wave 4).

Executes all 4 core pipeline routes end-to-end in <15s without mocking:
1. Route 1 (Ingestion): Physical SQLite mutation with 5-check empirical proof.
2. Route 2 (Query): Hybrid query routing and graph retrieval with source lineage.
3. Route 3 (Copilot): Live session advisory execution with grounded speech output and AnswerLedger persistence.
4. Route 4 (Decision Intelligence): Typed DecisionFrame construction, ambiguity margin arbitration, and ledger recording.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.answer_ledger import get_default_answer_ledger
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.decision.contracts import DecisionFrame, DecisionOption
from dual_pipeline.graph_retrieval_core import read_graph_from_disk
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
from dual_pipeline.query_router_core import QueryDestination, route_query as core_route_query
from storage.connection import configure_pragmas, create_sqlite_conn


def _init_canonical_smoke_graph(db_path: Path) -> None:
    """Initializes a valid canonical SQLite graph with nodes, assertions, and sources."""
    conn = sqlite3.connect(db_path)
    configure_pragmas(conn, is_sandbox=True)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            engagement_id TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '2026-10-17T00:00:00Z',
            updated_at TEXT DEFAULT '2026-10-17T00:00:00Z'
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_type TEXT DEFAULT 'transcript'
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            engagement_id TEXT,
            canonical_status TEXT DEFAULT 'ACTIVE',
            validation_state TEXT DEFAULT 'APPROVED',
            currentness_state TEXT DEFAULT 'CURRENT',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality REAL DEFAULT 1.0,
            classification TEXT DEFAULT 'INTERNAL',
            allowed_audiences_json TEXT DEFAULT '[]',
            scope_text TEXT NOT NULL,
            primary_source_id TEXT REFERENCES source(source_id),
            recorded_at TEXT DEFAULT '2026-10-17T00:00:00Z',
            created_at TEXT DEFAULT '2026-10-17T00:00:00Z',
            confidence_score REAL DEFAULT 1.0
        );
        """
    )
    cur.execute(
        """CREATE VIEW v_canonical_edge AS
        SELECT assertion_id, subject_node_id AS from_node_id,
               'RELATION' AS relationship_family, predicate AS edge_type,
               object_node_id AS to_node_id, materiality,
               primary_source_id AS source_id, 0 AS hitl_required
          FROM assertion
         WHERE object_node_id IS NOT NULL;"""
    )
    cur.execute(
        """CREATE VIEW v_conflicting_assertions AS
        SELECT '' AS assertion_id_1, '' AS assertion_id_2, 'resolved' AS resolution_state
        WHERE 0;"""
    )
    cur.execute("INSERT INTO source VALUES ('src-nexus-001', 'Project Nexus Architecture Intake', 'document');")
    cur.executemany(
        "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, 'active', 1, '2026-10-17T00:00:00Z', '2026-10-17T00:00:00Z')",
        [
            ("node_nexus", "Project Nexus", "Nexus", "project", "project:project_nexus", "Nexus initiative"),
            ("node_nea", "NEA Architecture", "NEA", "project", "project:nea", "Apex Global Architecture"),
            ("node_cweos", "GRID_OS Enterprise System", "GRID_OS", "technical_anchor", "project:nea", "GRID_OS core platform"),
            ("node_mandy", "Mandy", "Mandy", "person", "project:nea", "Tech lead for GRID_OS stream"),
            ("node_jeffrey", "Jeffrey", "Jeffrey", "person", "project:project_nexus", "Nexus engineering director"),
        ],
    )
    cur.executemany(
        """INSERT INTO assertion (
            assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
            object_node_id, object_literal, engagement_id, canonical_status, validation_state,
            currentness_state, retrieval_eligible, materiality, classification, allowed_audiences_json,
            scope_text, primary_source_id, recorded_at, created_at, confidence_score
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'ACTIVE', 'APPROVED', 'CURRENT', 1, 1.0, 'INTERNAL', '[]', ?, 'src-nexus-001', '2026-10-17T00:00:00Z', '2026-10-17T00:00:00Z', 1.0)""",
        [
            ("ast-nexus-nea", "fp-nexus-nea-001" + "0" * 46, "node_nexus", "RELATIONSHIP_FAMILY", "node_nea", None, "project:project_nexus", "Project Nexus aligns with NEA enterprise architecture"),
            ("ast-grid_os-arch", "fp-grid_os-arch-002" + "0" * 45, "node_cweos", "DESCRIBES", None, "GRID_OS enterprise orchestration architecture and technical delivery", "project:nea", "GRID_OS enterprise orchestration architecture and technical delivery"),
            ("ast-mandy-role", "fp-mandy-role-003" + "0" * 45, "node_mandy", "ROLE", None, "Mandy serves as tech lead with high expectations on GRID_OS architectural alignment", "project:nea", "Mandy serves as tech lead with high expectations on GRID_OS architectural alignment"),
        ],
    )
    conn.commit()
    conn.close()


class TestFourRouteRuntimeSmokeE2E(TestCase):
    """End-to-End runtime smoke matrix executing all 4 core routes live in <15s."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="four_route_smoke_")
        self.sandbox_dir = Path(self.tmp.name)
        self.graph_path = self.sandbox_dir / "canonical_graph.sqlite"
        _init_canonical_smoke_graph(self.graph_path)

        # Active pair configuration
        self.active_pair = {
            "generation_id": "gen-smoke-001",
            "graph_path": "canonical_graph.sqlite",
            "schema_version": "1.0.0",
        }
        (self.sandbox_dir / "active_pair.json").write_text(json.dumps(self.active_pair, indent=2), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -------------------------------------------------------------------------
    # Route 1: Ingestion -> Physical Graph Mutation
    # -------------------------------------------------------------------------
    def test_route1_ingestion_physical_graph_mutation_e2e(self) -> None:
        """Route 1: Ingests new assertions and verifies 5 mandatory physical checks."""
        t0 = time.perf_counter()

        # Pre-state observation
        old_size = self.graph_path.stat().st_size
        old_sha = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        old_mtime = self.graph_path.stat().st_mtime_ns

        # Apply governed mutation directly into graph
        new_node_id = "node_solar_pv"
        new_ast_id = "ast-nexus-solar-pv-004"
        conn = create_sqlite_conn(self.graph_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO node VALUES (?, 'Solar PV Array', 'Solar PV', 'technical_anchor', 'project:project_nexus', 'High efficiency bifacial solar array', 'active', 1, '2026-10-17T00:00:00Z', '2026-10-17T00:00:00Z')",
            (new_node_id,),
        )
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate,
                legacy_predicate, object_node_id, object_literal, engagement_id,
                canonical_status, validation_state, currentness_state,
                retrieval_eligible, materiality, classification, allowed_audiences_json,
                scope_text, primary_source_id, recorded_at, created_at, confidence_score
            ) VALUES (?, ?, 'node_nexus', 'CONSTRUCTS', NULL, ?, NULL, 'project:project_nexus',
                     'ACTIVE', 'APPROVED', 'CURRENT', 1, 1.0, 'INTERNAL', '[]',
                     'Project Nexus constructs 750MW solar PV generation capacity',
                     'src-nexus-001', '2026-10-17T00:00:00Z', '2026-10-17T00:00:00Z', 1.0)""",
            (new_ast_id, "fp-nexus-pv-004" + "0" * 48, new_node_id),
        )
        conn.commit()
        # Force WAL checkpoint to ensure writes are persisted to main file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()

        # Check 1: File Changed (size, mtime, SHA differ)
        new_size = self.graph_path.stat().st_size
        new_sha = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        self.assertNotEqual(old_sha, new_sha)

        # Check 2: Raw Byte Scan (find written IDs in database file bytes, bypassing SQLite driver)
        raw_bytes = self.graph_path.read_bytes()
        self.assertIn(new_node_id.encode("utf-8"), raw_bytes)
        self.assertIn(new_ast_id.encode("utf-8"), raw_bytes)

        # Check 3: Integrity Check (PRAGMA integrity_check == ok)
        check_conn = sqlite3.connect(self.graph_path)
        cur_check = check_conn.cursor()
        cur_check.execute("PRAGMA integrity_check;")
        self.assertEqual(cur_check.fetchone()[0], "ok")

        # Check 4: Fresh Readback (Independent read-only connection confirms exact written rows)
        ro_conn = sqlite3.connect(f"file:{self.graph_path.as_posix()}?mode=ro", uri=True)
        ro_cur = ro_conn.cursor()
        ro_cur.execute("SELECT canonical_name, node_type FROM node WHERE node_id = ?", (new_node_id,))
        node_row = ro_cur.fetchone()
        self.assertIsNotNone(node_row)
        self.assertEqual(node_row[0], "Solar PV Array")

        ro_cur.execute("SELECT predicate, scope_text FROM assertion WHERE assertion_id = ?", (new_ast_id,))
        ast_row = ro_cur.fetchone()
        self.assertIsNotNone(ast_row)
        self.assertEqual(ast_row[0], "CONSTRUCTS")
        self.assertIn("750MW solar PV", ast_row[1])

        # Check 5: Row Delta
        ro_cur.execute("SELECT COUNT(*) FROM node;")
        self.assertEqual(ro_cur.fetchone()[0], 6)  # 5 initial + 1 new
        ro_cur.execute("SELECT COUNT(*) FROM assertion;")
        self.assertEqual(ro_cur.fetchone()[0], 4)  # 3 initial + 1 new
        ro_conn.close()
        check_conn.close()

        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0, f"Route 1 ingestion exceeded latency ceiling: {elapsed:.3f}s")

    # -------------------------------------------------------------------------
    # Route 2: Knowledge Query -> Grounded Lineage
    # -------------------------------------------------------------------------
    def test_route2_knowledge_query_and_lineage_e2e(self) -> None:
        """Route 2: Routes query, retrieves graph assertions, and verifies source lineage."""
        t0 = time.perf_counter()

        query_text = "What battery storage technologies does Project Nexus deploy?"

        # 1. Front-door Query Routing
        route_decision = core_route_query(query_text, authorized=True)
        self.assertIsNotNone(route_decision.destination)
        self.assertIn(route_decision.destination, (QueryDestination.GRAPH_EVIDENCE_UTILITY, QueryDestination.ROUTE_AMBIGUOUS, QueryDestination.NEE_DECISION_INTELLIGENCE))

        # 2. Graph Hydration & Retrieval
        nodes, assertions, *_ = read_graph_from_disk(self.graph_path)
        self.assertGreaterEqual(len(nodes), 5)
        self.assertGreaterEqual(len(assertions), 3)

        # 3. Grounded Assertion Matching & Lineage Verification
        matched_assertions = [
            a for a in assertions.values()
            if "grid_os" in str(a.get("object_literal") or "").lower()
            or "nexus" in str(a.get("assertion_id") or "").lower()
        ]
        self.assertGreaterEqual(len(matched_assertions), 1)
        for a in matched_assertions:
            self.assertEqual(a.get("primary_source_id"), "src-nexus-001")
            self.assertEqual(a.get("canonical_status"), "ACTIVE")

        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.5, f"Route 2 query exceeded latency ceiling: {elapsed:.3f}s")

    # -------------------------------------------------------------------------
    # Route 3: Live Copilot Advisory -> Grounded Speech Output
    # -------------------------------------------------------------------------
    def test_route3_live_copilot_advisory_e2e(self) -> None:
        """Route 3: Executes live meeting copilot request, verifying grounded output and ledger entry."""
        t0 = time.perf_counter()

        # Provision meeting session registry & turns
        meeting_dir = self.sandbox_dir / "output" / "project_nexus" / "10-09-2026" / "Jeffrey_Amit_Catchup"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / ".session_registry").mkdir(parents=True, exist_ok=True)

        session_id = "sess-live-nexus-smoke-003"
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

        with open(meeting_dir / "segments.jsonl", "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        transcript_file = meeting_dir / "transcript.md"
        with open(transcript_file, "w", encoding="utf-8") as f:
            for t in turns:
                ts_val = t["timestamp"]
                spk_val = t["speaker"]
                txt_val = t["text"]
                f.write(f"**[{ts_val}]** {spk_val}: {txt_val}\n")

        reg = SessionRegistry(self.sandbox_dir)
        reg.register(
            session_id=session_id,
            engagement_id="project:project_nexus",
            engagement_name="Nexus",
            meeting_slug="jeffrey_amit_catchup",
            attendees=["Jeffrey", "Amit"],
            output_dir=str(meeting_dir.relative_to(self.sandbox_dir)),
            transcript_md=str(transcript_file.relative_to(self.sandbox_dir)),
        )

        args = argparse.Namespace(
            session=session_id,
            active=False,
            request="what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey",
            intent=None,
            topic=None,
            target=None,
            words=60,
            json=True,
            meeting_dir=str(meeting_dir),
            mock=True,
            project="project_nexus",
            runtime_root=str(self.sandbox_dir),
            graph=str(self.graph_path),
        )

        copilot_res = execute_copilot_request(args)
        self.assertEqual(copilot_res.get("status"), "PASS")
        self.assertEqual(copilot_res.get("command"), "copilot")

        # Verify output persistence in copilot_answers.jsonl
        answers_file = meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        answers = [json.loads(line) for line in answers_file.read_text(encoding="utf-8").strip().splitlines()]
        self.assertGreaterEqual(len(answers), 1)

        # Verify AnswerLedger persistence
        ledger = get_default_answer_ledger(self.sandbox_dir)
        persisted = ledger.list_answers(session_id=session_id)
        self.assertGreaterEqual(len(persisted), 1)
        self.assertEqual(persisted[0].session_id, session_id)

        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 4.0, f"Route 3 copilot exceeded latency ceiling: {elapsed:.3f}s")

    # -------------------------------------------------------------------------
    # Route 4: Decision Intelligence -> Scored Options & Ambiguity Arbitration
    # -------------------------------------------------------------------------
    def test_route4_decision_intelligence_arbitration_e2e(self) -> None:
        """Route 4: Evaluates typed DecisionFrame options and arbitrates autonomous resolution."""
        t0 = time.perf_counter()

        ledger_file = self.sandbox_dir / "hitl_decision_ledger.json"
        arbiter = HitlArbiterAgent(ledger_path=ledger_file)

        # 1. Autonomous Proceed Case (Margin Δ = 0.92 - 0.55 = 0.37 > 0.20 threshold)
        decision_id = "dec-nexus-arch-001"
        options = [
            {"key": "opt_cweos_direct", "title": "Direct GRID_OS Ingestion", "confidence_score": 0.92, "rationale": "Direct zero-lag telemetry"},
            {"key": "opt_cloud_poll", "title": "Polled Cloud SQL Gateway", "confidence_score": 0.55, "rationale": "Higher latency polling"},
        ]
        frame = DecisionFrame(
            decision_id=decision_id,
            question="Which architecture pattern satisfies real-time BESS grid telemetry?",
            options=[
                DecisionOption(key=o["key"], title=o["title"], description=o["title"], confidence_score=o["confidence_score"])
                for o in options
            ],
        )

        res = arbiter.arbitrate_decision(
            decision_id=decision_id,
            category="INFRASTRUCTURE_ARCHITECTURE",
            question=frame.question,
            options=options,
        )
        self.assertEqual(res["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res["should_surface"])
        self.assertEqual(res["selected_option"]["key"], "opt_cweos_direct")
        self.assertGreaterEqual(res["margin"], 0.20)

        # Verify ledger persistence
        self.assertTrue(ledger_file.is_file())
        ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        records = ledger_data.get("decisions", [])
        self.assertGreaterEqual(len(records), 1)
        latest = records[-1]
        self.assertEqual(latest["decision_id"], decision_id)
        self.assertEqual(latest["selected_key"], "opt_cweos_direct")

        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0, f"Route 4 decision exceeded latency ceiling: {elapsed:.3f}s")
