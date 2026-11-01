"""Integration tests for Antigravity Agent SDK lifecycle hooks and governance guards.

Covers Wave 3 acceptance criteria:
1. Pre-tool security policy decisions (DENY, APPROVE, ASK_USER) and blocking invariants.
2. Concurrent tool call tracking and correlation by call ID / name.
3. Stop hook liveness guard and receipt verification:
   - Missing receipt rejection
   - Malformed JSON rejection
   - Non-dict JSON rejection
   - Invalid outcome rejection (FAILED, BLOCKED, etc.)
   - Missing/invalid cryptographic digest rejection
   - Mismatched run ID rejection
   - Mismatched plan path rejection
   - Stale receipt rejection
   - Valid receipt acceptance
   - Custom receipt validator integration
4. Declarative hook registry (.agents/hooks.json) validation:
   - Zero corrupted/placeholder matchers (no "Terminal Outcomes Enum")
   - Path patterns include "docs/plans/" for plan guards
   - Blocking severity invariant enforcement
   - Command matching and negative controls
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from google.antigravity import types
from google.antigravity.hooks.policy import Decision
from google.antigravity.types import StopArgs, StopDecision

from dual_pipeline.agent_sdk import (
    HITL_REQUIRED_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    STRICTLY_DENIED_TOOLS,
    NeeLifecycleHooks,
    NeeSecurityPolicy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAgentSdkHooksLifecycle(unittest.TestCase):
    """Test suite covering lifecycle hooks, correlation, receipts, and declarative guards."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Pre-Tool Security Decisions & Invariants
    # -------------------------------------------------------------------------

    def test_pre_tool_decide_enforces_security_policies(self) -> None:
        """Pre-tool hook strictly enforces DENY, APPROVE, and records status."""
        hooks = NeeLifecycleHooks(run_id="sec_test_001")
        pre_tool_fn = hooks.build_pre_tool_call_decide()

        # Denied tool
        denied_call = types.ToolCall(name="drop_graph_database", args={})
        res_denied = asyncio.run(pre_tool_fn(denied_call))
        self.assertFalse(res_denied.allow)
        self.assertEqual(hooks.tool_call_history[-1]["decision"], Decision.DENY.value)
        self.assertEqual(hooks.tool_call_history[-1]["status"], "DENIED")

        # Approved safe tool
        safe_call = types.ToolCall(name="query_knowledge_graph", args={"query": "test"})
        res_safe = asyncio.run(pre_tool_fn(safe_call))
        self.assertTrue(res_safe.allow)
        self.assertEqual(hooks.tool_call_history[-1]["decision"], Decision.APPROVE.value)
        self.assertEqual(hooks.tool_call_history[-1]["status"], "APPROVED")

    # -------------------------------------------------------------------------
    # 2. Tool Call Correlation & Concurrency Tracking
    # -------------------------------------------------------------------------

    def test_tool_call_correlation_with_explicit_ids(self) -> None:
        """Concurrent or interleaved tool calls correlate cleanly to their specific call ID."""
        hooks = NeeLifecycleHooks(run_id="corr_test_001")
        pre_tool_fn = hooks.build_pre_tool_call_decide()
        post_tool_fn = hooks.build_post_tool_call()

        # Simulate two interleaved calls
        call_1 = types.ToolCall(id="call_alpha_123", name="query_knowledge_graph", args={})
        call_2 = types.ToolCall(id="call_beta_456", name="read_resource", args={})

        asyncio.run(pre_tool_fn(call_1))
        asyncio.run(pre_tool_fn(call_2))

        self.assertEqual(len(hooks.tool_call_history), 2)
        self.assertIsNone(hooks.tool_call_history[0]["completed_at"])
        self.assertIsNone(hooks.tool_call_history[1]["completed_at"])

        # Call 2 completes first (out-of-order)
        res_2 = {"id": "call_beta_456", "name": "read_resource", "result": "ok"}
        asyncio.run(post_tool_fn(res_2))

        self.assertIsNone(hooks.tool_call_history[0]["completed_at"])
        self.assertIsNotNone(hooks.tool_call_history[1]["completed_at"])
        self.assertEqual(hooks.tool_call_history[1]["status"], "COMPLETED")

        # Call 1 completes with error
        res_1 = {"id": "call_alpha_123", "name": "query_knowledge_graph", "error": "timeout"}
        asyncio.run(post_tool_fn(res_1))

        self.assertIsNotNone(hooks.tool_call_history[0]["completed_at"])
        self.assertEqual(hooks.tool_call_history[0]["status"], "FAILED")
        self.assertEqual(hooks.tool_call_history[0]["error"], "timeout")

    def test_tool_call_correlation_by_name_fallback(self) -> None:
        """Tool completions without IDs correlate to matching tool name."""
        hooks = NeeLifecycleHooks(run_id="corr_test_002")
        pre_tool_fn = hooks.build_pre_tool_call_decide()
        post_tool_fn = hooks.build_post_tool_call()

        call = types.ToolCall(name="query_knowledge_graph", args={})
        asyncio.run(pre_tool_fn(call))

        res = types.ToolResult(name="query_knowledge_graph", result={"count": 5})
        asyncio.run(post_tool_fn(res))

        self.assertEqual(len(hooks.tool_call_history), 1)
        self.assertIsNotNone(hooks.tool_call_history[0]["completed_at"])
        self.assertEqual(hooks.tool_call_history[0]["status"], "COMPLETED")

    # -------------------------------------------------------------------------
    # 3. Stop Hook & Receipt Verification Gates (F06)
    # -------------------------------------------------------------------------

    def test_stop_hook_liveness_guard(self) -> None:
        """Stop hook rejects idle trajectories without domain activity."""
        hooks = NeeLifecycleHooks(run_id="stop_test_001")
        stop_fn = hooks.build_stop_hook(enforce_receipt_verification=True)

        idle_args = StopArgs(response_text="Done nothing.", continuation_count=0)
        res = asyncio.run(stop_fn(idle_args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("without executing required domain tools", res.reason)

        # Once a tool executes, liveness passes
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})
        res_active = asyncio.run(stop_fn(idle_args))
        self.assertEqual(res_active.decision, StopDecision.ALLOW_STOP)

    def test_receipt_verification_rejects_missing_file(self) -> None:
        """Stop hook rejects missing receipt file."""
        hooks = NeeLifecycleHooks(run_id="stop_test_002")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        missing_path = self.test_dir / "non_existent_receipt.json"
        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=missing_path,
        )

        args = StopArgs(response_text="Completed.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("missing or not a file", res.reason)

    def test_receipt_verification_rejects_malformed_json(self) -> None:
        """Stop hook rejects malformed JSON receipt."""
        hooks = NeeLifecycleHooks(run_id="stop_test_003")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "malformed_receipt.json"
        receipt_file.write_text("{ incomplete json ...", encoding="utf-8")

        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
        )

        args = StopArgs(response_text="Completed.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("malformed JSON", res.reason)

    def test_receipt_verification_rejects_non_dict_json(self) -> None:
        """Stop hook rejects JSON receipts that are not objects."""
        hooks = NeeLifecycleHooks(run_id="stop_test_004")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "list_receipt.json"
        receipt_file.write_text("[\"item1\", \"item2\"]", encoding="utf-8")

        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
        )

        args = StopArgs(response_text="Completed.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("must be a JSON object", res.reason)

    def test_receipt_verification_rejects_non_terminal_outcomes(self) -> None:
        """Stop hook rejects receipts with non-passing outcomes."""
        hooks = NeeLifecycleHooks(run_id="stop_test_005")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        for bad_status in ["BLOCKED", "FAILED", "IN_PROGRESS", "CANCELLED"]:
            receipt_file = self.test_dir / f"receipt_{bad_status}.json"
            receipt_file.write_text(
                json.dumps({
                    "status": bad_status,
                    "receipt_digest": "abcdef1234567890abcdef1234567890",
                }),
                encoding="utf-8",
            )

            stop_fn = hooks.build_stop_hook(
                enforce_receipt_verification=True,
                required_receipt_path=receipt_file,
            )

            args = StopArgs(response_text="Completed.", continuation_count=1)
            res = asyncio.run(stop_fn(args))
            self.assertEqual(res.decision, StopDecision.CONTINUE)
            self.assertIn("does not indicate completion", res.reason)

    def test_receipt_verification_rejects_missing_cryptographic_digest(self) -> None:
        """Stop hook rejects receipts lacking a valid cryptographic digest."""
        hooks = NeeLifecycleHooks(run_id="stop_test_006")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "receipt_no_digest.json"
        receipt_file.write_text(
            json.dumps({"status": "PASS"}),
            encoding="utf-8",
        )

        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
        )

        args = StopArgs(response_text="Completed.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("missing a valid cryptographic digest", res.reason)

    def test_receipt_verification_rejects_mismatched_scope(self) -> None:
        """Stop hook rejects receipts with mismatched run ID or plan path."""
        hooks = NeeLifecycleHooks(run_id="run_expected_123")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "receipt_mismatch.json"
        receipt_file.write_text(
            json.dumps({
                "status": "COMPLETE",
                "receipt_digest": "a" * 64,
                "run_id": "run_other_999",
                "plan_path": "docs/plans/other_plan.md",
            }),
            encoding="utf-8",
        )

        # Test run_id mismatch
        stop_fn_run = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            expected_run_id="run_expected_123",
        )
        res_run = asyncio.run(stop_fn_run(StopArgs(response_text="Done.", continuation_count=1)))
        self.assertEqual(res_run.decision, StopDecision.CONTINUE)
        self.assertIn("Receipt run ID mismatch", res_run.reason)

        # Test plan_path mismatch
        stop_fn_plan = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            expected_plan_path="docs/plans/my_expected_plan.md",
        )
        res_plan = asyncio.run(stop_fn_plan(StopArgs(response_text="Done.", continuation_count=1)))
        self.assertEqual(res_plan.decision, StopDecision.CONTINUE)
        self.assertIn("Receipt plan path mismatch", res_plan.reason)

    def test_receipt_verification_rejects_stale_receipt(self) -> None:
        """Stop hook rejects receipts modified prior to the session timestamp threshold."""
        hooks = NeeLifecycleHooks(run_id="stop_test_007")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "stale_receipt.json"
        receipt_file.write_text(
            json.dumps({
                "status": "PASS",
                "receipt_digest": "b" * 64,
            }),
            encoding="utf-8",
        )

        future_threshold = time.time() + 100.0
        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            min_receipt_timestamp=future_threshold,
        )

        args = StopArgs(response_text="Done.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.CONTINUE)
        self.assertIn("Receipt is stale", res.reason)

    def test_receipt_verification_accepts_valid_bound_receipt(self) -> None:
        """Stop hook accepts properly bound, fresh, cryptographically signed receipts."""
        hooks = NeeLifecycleHooks(run_id="run_target_456")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "valid_receipt.json"
        receipt_file.write_text(
            json.dumps({
                "status": "COMPLETE",
                "receipt_digest": "c" * 64,
                "run_id": "run_target_456",
                "plan_path": "docs/plans/target_plan.md",
            }),
            encoding="utf-8",
        )

        stop_fn = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            expected_run_id="run_target_456",
            expected_plan_path="docs/plans/target_plan.md",
            min_receipt_timestamp=time.time() - 10.0,
        )

        args = StopArgs(response_text="Done.", continuation_count=1)
        res = asyncio.run(stop_fn(args))
        self.assertEqual(res.decision, StopDecision.ALLOW_STOP)

    def test_receipt_verification_with_custom_validator(self) -> None:
        """Custom receipt validator callable is respected."""
        hooks = NeeLifecycleHooks(run_id="stop_test_008")
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})

        receipt_file = self.test_dir / "custom_receipt.json"
        receipt_file.write_text("{}", encoding="utf-8")

        # Failing custom validator
        def custom_fail(path: Path) -> tuple[bool, str]:
            return False, "Custom cryptographic check failed."

        stop_fn_fail = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            receipt_validator=custom_fail,
        )
        res_fail = asyncio.run(stop_fn_fail(StopArgs(response_text="Done.", continuation_count=1)))
        self.assertEqual(res_fail.decision, StopDecision.CONTINUE)
        self.assertIn("Custom cryptographic check failed", res_fail.reason)

        # Passing custom validator
        def custom_pass(path: Path) -> tuple[bool, str]:
            return True, ""

        stop_fn_pass = hooks.build_stop_hook(
            enforce_receipt_verification=True,
            required_receipt_path=receipt_file,
            receipt_validator=custom_pass,
        )
        res_pass = asyncio.run(stop_fn_pass(StopArgs(response_text="Done.", continuation_count=1)))
        self.assertEqual(res_pass.decision, StopDecision.ALLOW_STOP)

    # -------------------------------------------------------------------------
    # 4. Declarative Hook Registry (.agents/hooks.json) Integrity (F03/F04)
    # -------------------------------------------------------------------------

    def test_hooks_json_contains_no_placeholder_or_corrupted_tokens(self) -> None:
        """Validates that .agents/hooks.json has zero corrupted or placeholder strings."""
        hooks_file = REPO_ROOT / ".agents" / "hooks.json"
        self.assertTrue(hooks_file.is_file(), ".agents/hooks.json must exist.")

        raw_text = hooks_file.read_text(encoding="utf-8")
        self.assertNotIn("Terminal Outcomes Enum", raw_text)
        self.assertNotIn("wave-open", raw_text)

        hooks_data = json.loads(raw_text)
        self.assertIn("hooks", hooks_data)
        hooks_list = hooks_data["hooks"]
        hooks_dict = {h["id"]: h for h in hooks_list}

        # Validate nee-post-ingestion-receipt matches real commands
        post_ingest = hooks_dict["nee-post-ingestion-receipt"]
        patterns = post_ingest.get("match", {}).get("command_patterns", [])
        self.assertTrue(any("intake-publish" in p for p in patterns))
        self.assertTrue(any("ingest-meeting" in p for p in patterns))

    def test_implementation_plan_guard_covers_docs_plans(self) -> None:
        """Validates that nee-implementation-plan-guard matches docs/plans/ directory."""
        hooks_file = REPO_ROOT / ".agents" / "hooks.json"
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks_dict = {h["id"]: h for h in hooks_data["hooks"]}
        plan_guard = hooks_dict["nee-implementation-plan-guard"]

        also_match = plan_guard.get("match", {}).get("also_match", [])
        matched_patterns = [m.get("path_pattern") for m in also_match]

        self.assertIn("docs/plans/", matched_patterns)
        self.assertIn("documentation/", matched_patterns)
        self.assertEqual(plan_guard.get("severity"), "blocking")

    def test_blocking_guards_maintain_blocking_severity(self) -> None:
        """Validates that critical governance guards are strictly marked blocking."""
        hooks_file = REPO_ROOT / ".agents" / "hooks.json"
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks_dict = {h["id"]: h for h in hooks_data["hooks"]}

        critical_guards = [
            "nee-implementation-plan-guard",
            "nee-wave-progression-guard",
            "nee-terminal-outcome-guard",
            "nee-pytest-scope-guard",
            "nee-hitl-atomic-guard",
            "nee-background-task-kill-guard",
        ]

        for guard_name in critical_guards:
            self.assertIn(guard_name, hooks_dict, f"Guard '{guard_name}' missing from hooks.json")
            severity = hooks_dict[guard_name].get("severity")
            self.assertEqual(
                severity,
                "blocking",
                f"Guard '{guard_name}' must have severity 'blocking', found '{severity}'",
            )


if __name__ == "__main__":
    unittest.main()
