"""tests/end_to_end/test_query_graph_grounding_e2e.py

End-to-end lifecycle receipt test suite for query graph grounding and anti-bypass controls.
Verifies full-process CLI invocations of:
1. scripts/hitl_pre_tool_guard.py (stdin/stdout subshell execution).
2. scripts/hitl_post_invocation_guard.py (full transcript JSON parsing).
3. GraphFirstQueryEngine defense-in-depth inspection.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


class TestQueryGraphGroundingE2E(unittest.TestCase):
    """End-to-end lifecycle verification for query grounding and pre/post guards."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.python_bin = sys.executable

    def test_e2e_pre_tool_guard_blocks_ls_output_subprocess(self) -> None:
        """E2E test: hitl_pre_tool_guard.py process exits 2 on ls -la output/."""
        payload = json.dumps({
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "ls -la output/"}
            }
        })
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "scripts" / "hitl_pre_tool_guard.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 2, f"Expected returncode 2, got {proc.returncode}: {proc.stdout} {proc.stderr}")
        out = json.loads(proc.stdout)
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("GRAPHDB_TRAVERSAL_REQUIRED", out.get("reason", ""))

    def test_e2e_pre_tool_guard_allows_governed_tool_subprocess(self) -> None:
        """E2E test: hitl_pre_tool_guard.py process exits 0 on governed validator."""
        payload = json.dumps({
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python3 tools/validate_output_hierarchy.py --check"}
            }
        })
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "scripts" / "hitl_pre_tool_guard.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 0, f"Expected returncode 0, got {proc.returncode}: {proc.stdout} {proc.stderr}")
        out = json.loads(proc.stdout)
        self.assertEqual(out.get("decision"), "allow")

    def test_e2e_post_invocation_guard_forces_query_subprocess(self) -> None:
        """E2E test: hitl_post_invocation_guard.py forces continue when -query has zero tool calls."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "review NEE agenda -query"},
                {"type": "PLANNER_RESPONSE", "content": "Here is an ungrounded draft", "tool_calls": []}
            ]
        })
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "scripts" / "hitl_post_invocation_guard.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out.get("terminationBehavior"), "force_continue")
        self.assertTrue(len(out.get("injectSteps", [])) > 0)
        self.assertIn("GRAPHDB TRAVERSAL INTERCEPT", out["injectSteps"][0]["userMessage"])

    def test_e2e_post_invocation_guard_sticky_multi_turn_subprocess(self) -> None:
        """E2E test: hitl_post_invocation_guard.py preserves -query requirement on multi-turn follow-up."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "review agenda -query"},
                {"type": "PLANNER_RESPONSE", "content": "Traversed graph", "tool_calls": [{"name": "nee_query"}]},
                {"type": "USER_INPUT", "content": "make it more specific"},
                {"type": "PLANNER_RESPONSE", "content": "Unverified second response", "tool_calls": []}
            ]
        })
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "scripts" / "hitl_post_invocation_guard.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out.get("terminationBehavior"), "force_continue")

    def test_e2e_post_invocation_guard_allows_grounded_turn_subprocess(self) -> None:
        """E2E test: hitl_post_invocation_guard.py allows completion when nee_query is executed."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "review agenda -query"},
                {"type": "PLANNER_RESPONSE", "content": "Traversed graph", "tool_calls": [{"name": "nee_query", "args": {"question": "agenda"}}]},
                {"type": "PLANNER_RESPONSE", "content": "Grounded answer with citations", "tool_calls": []}
            ]
        })
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "scripts" / "hitl_post_invocation_guard.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertIsNone(out.get("terminationBehavior"))
        self.assertEqual(out.get("injectSteps"), [])

    def test_e2e_snapshot_schema_unification(self) -> None:
        """E2E test: ReadOnlyGraphSnapshot queries canonical node and assertion tables cleanly."""
        import tempfile
        import sqlite3
        import hashlib
        from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot

        with tempfile.TemporaryDirectory() as td:
            rt_root = Path(td)
            db_path = rt_root / "canonical_graph.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    active INTEGER DEFAULT 1
                );
            """)
            cur.execute("""
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_literal TEXT NOT NULL,
                    object_node_id TEXT,
                    currentness_state TEXT DEFAULT 'current',
                    retrieval_eligible INTEGER DEFAULT 1
                );
            """)
            cur.execute("INSERT INTO node VALUES ('person_amit', 'Amit Ayer', 'Amit Ayer', 'person', 1)")
            cur.execute(
                "INSERT INTO assertion VALUES ('ast_001', 'person_amit', 'HAS_ROLE', 'Lead Architect', NULL, 'current', 1)"
            )
            conn.commit()
            conn.close()

            # Create active_pair.json
            sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
            pair_file = rt_root / "active_pair.json"
            pair_file.write_text(json.dumps({
                "generation_id": "GEN-001",
                "graph_path": str(db_path),
                "graph_sha256": sha,
            }))

            snapshot = ReadOnlyGraphSnapshot(rt_root, enforce_pair_integrity=True)
            try:
                # 1. get_node
                node_res = snapshot.get_node("person_amit")
                self.assertIsNotNone(node_res)
                self.assertEqual(node_res.get("canonical_name"), "Amit Ayer")
                self.assertEqual(node_res.get("name"), "Amit Ayer")

                # 2. get_assertion
                asrt_res = snapshot.get_assertion("ast_001")
                self.assertIsNotNone(asrt_res)
                self.assertEqual(asrt_res.get("object_literal"), "Lead Architect")
                self.assertEqual(asrt_res.get("subject_name"), "Amit Ayer")

                # 3. find_assertions_for_claim
                matched = snapshot.find_assertions_for_claim("Amit Ayer", "HAS_ROLE", "Lead Architect")
                self.assertEqual(len(matched), 1)
                self.assertEqual(matched[0]["assertion_id"], "ast_001")
            finally:
                snapshot.close()

    def test_e2e_snapshot_concurrency_generation_swap(self) -> None:
        """E2E test: ReadOnlyGraphSnapshot detects concurrent generation swap without crash."""
        import tempfile
        import sqlite3
        import hashlib
        from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot

        with tempfile.TemporaryDirectory() as td:
            rt_root = Path(td)
            db_path = rt_root / "canonical_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT)")
            conn.commit()
            conn.close()

            sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
            pair_file = rt_root / "active_pair.json"
            pair_file.write_text(json.dumps({
                "generation_id": "GEN-001",
                "graph_path": str(db_path),
                "graph_sha256": sha,
            }))

            snapshot = ReadOnlyGraphSnapshot(rt_root, enforce_pair_integrity=True)
            try:
                self.assertTrue(snapshot.is_generation_current())
                # Concurrently swap generation in active_pair.json
                pair_file.write_text(json.dumps({
                    "generation_id": "GEN-002",
                    "graph_path": str(db_path),
                    "graph_sha256": sha,
                }))
                self.assertFalse(snapshot.is_generation_current())
            finally:
                snapshot.close()

    def test_e2e_query_router_executive_synthesis_enforcement(self) -> None:
        """E2E test: route_query enforces cross_workstream_synthesis on executive agenda prompts."""
        import unittest.mock as mock
        from dual_pipeline.query_router import route_query

        with mock.patch("dual_pipeline.query_router._check_graphdb_available", return_value=True):
            decision = route_query("Please draft the executive agenda and talking points for the All-Hands meeting")
            self.assertEqual(decision.query_type, "cross_workstream_synthesis")
            self.assertEqual(decision.materiality, "high")
            self.assertIn("EXECUTIVE_SYNTHESIS_FORCED", decision.reason_codes)

    def test_e2e_audit_governance_harmonization_subprocess(self) -> None:
        """E2E test: audit_governance_harmonization.py runs and passes with code 0."""
        proc = subprocess.run(
            [self.python_bin, str(self.repo_root / "tools" / "audit_governance_harmonization.py")],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(proc.returncode, 0, f"Expected returncode 0, got {proc.returncode}: {proc.stdout} {proc.stderr}")
        self.assertIn("Policy Registry Sync", proc.stdout)
        self.assertIn("0 CRITICAL", proc.stdout)
        self.assertIn("0 WARNING", proc.stdout)


if __name__ == "__main__":
    unittest.main()
