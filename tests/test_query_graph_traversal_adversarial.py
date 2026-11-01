"""tests/test_query_graph_traversal_adversarial.py

Adversarial test suite for GraphDB traversal enforcement, -query lifecycle gate, and filesystem anti-bypass.
Asserts that:
1. Directory enumeration ('ls', 'tree', 'stat') and inline python targeting output/ are blocked by ProhibitedCommandGuard.
2. Governed validation and pipeline tools (validate_output_hierarchy.py, nee.py) remain allowlisted.
3. hitl_pre_tool_guard.py denies run_command targeting output/ with exit code 2.
4. hitl_post_invocation_guard.py forces continuation when -query is used with zero graph query tool calls.
5. Sticky thread memory preserves the -query requirement on follow-up turns ('yes more specific').
6. Turns that invoke nee_query or governed graph queries pass without interception.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.query_core.firewall import GraphDBRequiredError, ProhibitedCommandGuard
from scripts.hitl_post_invocation_guard import (
    QUERY_TRAVERSAL_FORCE_PROMPT,
    evaluate_post_invocation_payload,
)
from scripts.hitl_pre_tool_guard import handle_tool_call_guard


class TestQueryGraphTraversalAdversarial(unittest.TestCase):
    """Adversarial suite testing query traversal guards and anti-bypass controls."""

    def test_prohibited_command_ls_output_rejected(self) -> None:
        """Verify ls -la output/ raises GraphDBRequiredError."""
        with self.assertRaises(GraphDBRequiredError) as ctx:
            ProhibitedCommandGuard.inspect("ls -la output/")
        self.assertIn("ls", ctx.exception.matched_pattern)
        self.assertIn("output", ctx.exception.target_path)

    def test_prohibited_command_ls_subfolder_rejected(self) -> None:
        """Verify ls targeting output subdirectories is blocked."""
        with self.assertRaises(GraphDBRequiredError) as ctx:
            ProhibitedCommandGuard.inspect("ls -la output/internal_consulting output/enterprise_account_planning")
        self.assertIn("ls", ctx.exception.matched_pattern)

    def test_prohibited_command_tree_output_rejected(self) -> None:
        """Verify tree output/ is blocked."""
        with self.assertRaises(GraphDBRequiredError) as ctx:
            ProhibitedCommandGuard.inspect("tree output")
        self.assertIn("tree", ctx.exception.matched_pattern)

    def test_prohibited_command_python_inline_traversal_rejected(self) -> None:
        """Verify python -c inline directory scanning is blocked."""
        cmd = 'python3 -c "import os; print(os.listdir(\'output\'))"'
        with self.assertRaises(GraphDBRequiredError) as ctx:
            ProhibitedCommandGuard.inspect(cmd)
        self.assertIn("python -c", ctx.exception.matched_pattern)

    def test_governed_cli_allowlisted(self) -> None:
        """Verify governed validation and test scripts remain allowlisted."""
        allowlisted_commands = [
            "python3 tools/validate_output_hierarchy.py --check",
            "python3 tools/lint_production_purity.py",
            "pytest tests/test_foo.py --timeout=60",
            ".venv/bin/python nee.py ask --question 'test'",
        ]
        for cmd in allowlisted_commands:
            violation = ProhibitedCommandGuard.evaluate_command(cmd)
            self.assertFalse(violation.is_violation, f"Expected allow for '{cmd}', got {violation}")

    def test_hitl_pre_tool_guard_blocks_ls_output(self) -> None:
        """Verify hitl_pre_tool_guard handle_tool_call_guard denies ls -la output/."""
        payload = json.dumps({
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "ls -la output/"}
            }
        })
        res = handle_tool_call_guard(payload)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("GRAPHDB_TRAVERSAL_REQUIRED", res.get("reason", ""))

    def test_hitl_pre_tool_guard_allows_governed_script(self) -> None:
        """Verify hitl_pre_tool_guard allows governed tools."""
        payload = json.dumps({
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python3 tools/validate_output_hierarchy.py --check"}
            }
        })
        res = handle_tool_call_guard(payload)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("decision"), "allow")

    def test_post_invocation_guard_intercepts_query_flag(self) -> None:
        """Verify -query flag with zero tool calls forces continue."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "flesh out talking points -query"},
                {"type": "PLANNER_RESPONSE", "content": "Here are points...", "tool_calls": []}
            ]
        })
        res = evaluate_post_invocation_payload(payload)
        self.assertEqual(res.get("terminationBehavior"), "force_continue")
        self.assertTrue(len(res.get("injectSteps", [])) > 0)
        self.assertIn(QUERY_TRAVERSAL_FORCE_PROMPT, res["injectSteps"][0]["userMessage"])

    def test_post_invocation_guard_sticky_thread_memory(self) -> None:
        """Verify sticky thread memory forces continue on follow-up turn without repeating -query."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "review agenda -query"},
                {"type": "PLANNER_RESPONSE", "content": "Queried graph", "tool_calls": [{"name": "nee_query"}]},
                {"type": "USER_INPUT", "content": "yes more specific"},
                {"type": "PLANNER_RESPONSE", "content": "Second ungrounded answer", "tool_calls": []}
            ]
        })
        res = evaluate_post_invocation_payload(payload)
        self.assertEqual(res.get("terminationBehavior"), "force_continue")
        self.assertTrue(len(res.get("injectSteps", [])) > 0)

    def test_post_invocation_guard_permits_when_nee_query_called(self) -> None:
        """Verify -query turn succeeds when nee_query tool was executed."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "review agenda -query"},
                {"type": "PLANNER_RESPONSE", "content": "Found graph nodes", "tool_calls": [{"name": "nee_query", "args": {"question": "agenda"}}]},
                {"type": "PLANNER_RESPONSE", "content": "Final grounded agenda", "tool_calls": []}
            ]
        })
        res = evaluate_post_invocation_payload(payload)
        self.assertIsNone(res.get("terminationBehavior"))
        self.assertEqual(res.get("injectSteps"), [])

    def test_post_invocation_guard_skips_command_prompts(self) -> None:
        """Verify pure command / workflow inputs do not trigger graph traversal force-continue."""
        payload = json.dumps({
            "steps": [
                {"type": "USER_INPUT", "content": "continue W1 only"},
                {"type": "PLANNER_RESPONSE", "content": "Continuing Wave 1.", "tool_calls": []}
            ]
        })
        res = evaluate_post_invocation_payload(payload)
        self.assertIsNone(res.get("terminationBehavior"))
        self.assertEqual(res.get("injectSteps"), [])

    def test_route_executive_briefing_rejects_memory_connection(self) -> None:
        """Verify route_executive_briefing rejects :memory: SQLite connection."""
        import sqlite3
        from dual_pipeline.query_router import route_executive_briefing

        mem_conn = sqlite3.connect(":memory:")
        try:
            with self.assertRaises(GraphDBRequiredError) as ctx:
                route_executive_briefing("Amit Ayer", conn=mem_conn)
            self.assertIn("In-memory SQLite connections (:memory:) are strictly prohibited", str(ctx.exception))
        finally:
            mem_conn.close()

    def test_lineage_formatter_missing_ids_unverified(self) -> None:
        """Verify lineage_formatter marks missing token IDs as UNVERIFIED (0%) without fabrication."""
        from dual_pipeline.query_core.lineage_formatter import _resolve_confidence_map

        conf_map = _resolve_confidence_map(["ast_missing_999", "workstream_nonexistent"], graph_path=None)
        self.assertEqual(conf_map.get("ast_missing_999"), "UNVERIFIED (0%)")
        self.assertEqual(conf_map.get("workstream_nonexistent"), "UNVERIFIED (0%)")

    def test_answer_ledger_zero_citations_unverified(self) -> None:
        """Verify _extract_query_metadata defaults rigor to UNVERIFIED on empty citations or missing graph hash."""
        from dual_pipeline.answer_ledger import _extract_query_metadata

        meta = _extract_query_metadata({})
        self.assertEqual(meta[2], "UNVERIFIED")
        self.assertEqual(meta[3], "UNVERIFIED")
        cited, text, rigor, g_sha, gen, lat, mid = meta
        self.assertEqual(rigor, "UNVERIFIED")
        self.assertIsNone(g_sha)

    def test_verify_answer_graph_traversal_rejections(self) -> None:
        """Verify verify_answer_graph_traversal rejects empty citations, missing hash, or mismatched active pair."""
        import sqlite3
        from dual_pipeline.answer_ledger import AnswerRecord, verify_answer_graph_traversal

        # 1. Zero citations
        r1 = AnswerRecord(
            pipeline="query",
            intent_or_type="exact_lookup",
            context_digest="d1",
            retrieval_seeds=["q1"],
            context_snapshot=[],
            cited_assertion_ids=[],
            rigor="UNVERIFIED",
            response_text="resp",
            graph_sha256="abc123",
        )
        with self.assertRaises(GraphDBRequiredError) as ctx:
            verify_answer_graph_traversal(r1)
        self.assertIn("ZERO_GRAPH_TRAVERSAL", str(ctx.exception))

        # 2. Missing graph_sha256
        r2 = AnswerRecord(
            pipeline="query",
            intent_or_type="exact_lookup",
            context_digest="d2",
            retrieval_seeds=["q2"],
            context_snapshot=[],
            cited_assertion_ids=["ast_1"],
            rigor="VERIFIED",
            response_text="resp",
            graph_sha256=None,
        )
        with self.assertRaises(GraphDBRequiredError) as ctx:
            verify_answer_graph_traversal(r2)
        self.assertIn("MISSING_GRAPH_SHA256", str(ctx.exception))

        # 3. Mismatched active pair hash
        r3 = AnswerRecord(
            pipeline="query",
            intent_or_type="exact_lookup",
            context_digest="d3",
            retrieval_seeds=["q3"],
            context_snapshot=[],
            cited_assertion_ids=["ast_1"],
            rigor="VERIFIED",
            response_text="resp",
            graph_sha256="hash_local",
        )
        with self.assertRaises(GraphDBRequiredError) as ctx:
            verify_answer_graph_traversal(r3, active_pair={"graph_sha256": "hash_canonical"})
        self.assertIn("GRAPH_SHA256_MISMATCH", str(ctx.exception))

        # 4. Non-existent assertion in SQLite
        db_conn = sqlite3.connect(":memory:")
        db_conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY)")
        db_conn.execute("INSERT INTO assertion VALUES ('ast_existing')")
        try:
            r4 = AnswerRecord(
                pipeline="query",
                intent_or_type="exact_lookup",
                context_digest="d4",
                retrieval_seeds=["q4"],
                context_snapshot=[],
                cited_assertion_ids=["ast_nonexistent"],
                rigor="VERIFIED",
                response_text="resp",
                graph_sha256="hash_ok",
            )
            with self.assertRaises(GraphDBRequiredError) as ctx:
                verify_answer_graph_traversal(r4, active_pair={"graph_sha256": "hash_ok"}, conn=db_conn)
            self.assertIn("NON_EXISTENT_ASSERTIONS", str(ctx.exception))

            # 5. Success case
            r5 = AnswerRecord(
                pipeline="query",
                intent_or_type="exact_lookup",
                context_digest="d5",
                retrieval_seeds=["q5"],
                context_snapshot=[],
                cited_assertion_ids=["ast_existing"],
                rigor="VERIFIED",
                response_text="resp",
                graph_sha256="hash_ok",
            )
            self.assertTrue(verify_answer_graph_traversal(r5, active_pair={"graph_sha256": "hash_ok"}, conn=db_conn))
        finally:
            db_conn.close()

    def test_policy_registry_sync_scanner_passes(self) -> None:
        """Verify scan_policy_registry_sync reports zero critical errors on canonical repo."""
        from tools.audit_governance_harmonization_core.common import (
            HarmonizationReport,
            discover_governance_files,
        )
        from tools.audit_governance_harmonization_core.structural_scanners import (
            scan_policy_registry_sync,
        )

        files = discover_governance_files()
        report = HarmonizationReport(
            scan_timestamp="2026-10-21T00:00:00Z",
            repo_root=str(Path(__file__).resolve().parents[1]),
        )
        scan_policy_registry_sync(files, report)
        policy_findings = [f for f in report.findings if f.category == "policy_sync"]
        self.assertEqual(len(policy_findings), 0, f"Unexpected policy_sync findings: {policy_findings}")

    def test_policy_registry_sync_detects_unregistered_invariant(self) -> None:
        """Verify scan_policy_registry_sync detects unregistered INV-* token in rule file."""
        import tempfile
        from tools.audit_governance_harmonization_core.common import HarmonizationReport
        from tools.audit_governance_harmonization_core.structural_scanners import (
            scan_policy_registry_sync,
        )

        with tempfile.TemporaryDirectory() as td:
            rules_dir = Path(td) / ".agents" / "rules"
            rules_dir.mkdir(parents=True)
            fake_rule = rules_dir / "fake_rule.md"
            fake_rule.write_text("This rule defines `INV-UNKNOWN-FAKE-TOKEN` which is not in registry.")

            report = HarmonizationReport(scan_timestamp="2026-10-21T00:00:00Z", repo_root=td)
            scan_policy_registry_sync([fake_rule], report)
            unregistered = [f for f in report.findings if "INV-UNKNOWN-FAKE-TOKEN" in f.title]
            self.assertEqual(len(unregistered), 1)
            self.assertEqual(unregistered[0].severity, "CRITICAL")


if __name__ == "__main__":
    unittest.main()
