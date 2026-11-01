#!/usr/bin/env python3
"""Governance rules and mechanical enforcement test suite.

Verifies the implementation and enforcement of:
1. Pytest execution limits (pyproject.toml timeout, Class A/B protocol, compliance validator).
2. HITL decision rules (Planning-Mode Override, 25% margin threshold, persistent ledger).
3. Hook definitions (.agents/hooks.json guard completeness and match triggers).
4. Git pre-commit enforcement integration.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance import HITLDecisionStore
from tools.check_pytest_compliance import (
    check_pyproject_timeout_configured,
    verify_pytest_command,
)

ROOT = Path(__file__).resolve().parents[1]


class GovernanceRulesEnforcementTests(unittest.TestCase):
    def test_pyproject_toml_pytest_timeout_configuration(self) -> None:
        """Verify that pyproject.toml enforces a hard per-test timeout defaulting to signal preemption on POSIX."""
        is_configured, timeout_val = check_pyproject_timeout_configured(ROOT)
        self.assertTrue(
            is_configured,
            "pyproject.toml must configure [tool.pytest.ini_options] timeout.",
        )
        self.assertEqual(
            timeout_val,
            60,
            f"Expected timeout = 60 in pyproject.toml, found {timeout_val}.",
        )

        pyproject_content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("pytest-timeout>=2.3", pyproject_content)
        # timeout_method = "thread" is barred to prevent GIL-starvation hangs during ReDoS/native calls
        self.assertNotIn('timeout_method = "thread"', pyproject_content)

    def test_hooks_json_guard_definitions(self) -> None:
        """Verify that all required governance guards are declared in .agents/hooks.json."""
        hooks_file = ROOT / ".agents" / "hooks.json"
        self.assertTrue(hooks_file.is_file(), ".agents/hooks.json must exist.")

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks = {h.get("id"): h for h in data.get("hooks", [])}

        required_guards = [
            "nee-pre-mutation-guard",
            "nee-post-ingestion-receipt",
            "nee-classification-check",
            "nee-retrieval-release-eval-guard",
            "nee-hitl-atomic-guard",
            "nee-implementation-plan-guard",
            "nee-implementation-plan-postwrite-validation",
            "nee-transcript-synthesis-guard",
            "nee-wave-progression-guard",
            "nee-terminal-outcome-guard",
            "nee-provider-egress-guard",
            "nee-pytest-scope-guard",
            "nee-background-task-kill-guard",
            "nee-architecture-lifecycle-guard",
            "nee-subagent-delegation-guard",
            "nee-hook-extension-guard",
            "nee-model-neutral-artifact-guard",
            "nee-scratchpad-transparency-guard",
        ]
        for guard_id in required_guards:
            self.assertIn(guard_id, hooks, f"Missing required guard: {guard_id}")

        # Verify nee-model-neutral-artifact-guard details
        model_guard = hooks["nee-model-neutral-artifact-guard"]
        self.assertEqual(model_guard.get("severity"), "blocking")
        self.assertEqual(model_guard.get("timing"), "PRE")

        # Verify nee-implementation-plan-guard details
        plan_guard = hooks["nee-implementation-plan-guard"]
        self.assertEqual(plan_guard.get("severity"), "blocking")
        self.assertEqual(plan_guard.get("match", {}).get("path_pattern"), "implementation_plan.md")
        also_match = plan_guard.get("match", {}).get("also_match", [])
        self.assertTrue(any(m.get("path_pattern") == "documentation/" for m in also_match))
        self.assertIn("hard-cap it at 1,000 words", plan_guard.get("instructions", ""))
        self.assertIn("Verify independently:", plan_guard.get("instructions", ""))

        postwrite_guard = hooks["nee-implementation-plan-postwrite-validation"]
        self.assertEqual(postwrite_guard.get("event"), "post_tool_call")
        self.assertEqual(postwrite_guard.get("severity"), "blocking")

        # Verify nee-pytest-scope-guard details
        pytest_guard = hooks["nee-pytest-scope-guard"]
        self.assertEqual(pytest_guard.get("severity"), "blocking")
        self.assertIn("pytest", pytest_guard.get("match", {}).get("command_patterns", []))
        self.assertIn("-m pytest", pytest_guard.get("match", {}).get("command_patterns", []))

        # Verify nee-background-task-kill-guard details
        bg_guard = hooks["nee-background-task-kill-guard"]
        self.assertEqual(bg_guard.get("severity"), "blocking")
        self.assertEqual(bg_guard.get("event"), "post_tool_call")

        # Verify nee-hitl-atomic-guard intercepts implementation_plan.md writes
        hitl_guard = hooks["nee-hitl-atomic-guard"]
        also_match = hitl_guard.get("match", {}).get("also_match", [])
        plan_matched = any(
            m.get("path_pattern") == "implementation_plan.md" for m in also_match
        )
        self.assertTrue(
            plan_matched,
            "nee-hitl-atomic-guard must match implementation_plan.md writes.",
        )

        # Verify nee-wave-progression-guard broadened patterns
        wave_guard = hooks["nee-wave-progression-guard"]
        wave_cmds = wave_guard.get("match", {}).get("command_patterns", [])
        self.assertIn("pytest", wave_cmds)
        self.assertIn("-m pytest", wave_cmds)

    def test_wave_progression_rules_contain_pytest_protocol(self) -> None:
        """Verify that .agents/rules/wave-progression.md defines the Class A/B protocol and wave plan standard."""
        rules_file = ROOT / ".agents" / "rules" / "wave-progression.md"
        self.assertTrue(rules_file.is_file(), "wave-progression.md must exist.")

        text = rules_file.read_text(encoding="utf-8")
        expected_sections = [
            "Execution-Evidence-Gated Progression",
            "Wave-Based Implementation Plan Standard",
            "Sequential Wave Progression Gate Lock",
            "pytest Run Protocol",
            "Class A — Debug run",
            "Class B — Receipt run",
            "Prohibited patterns",
            "Background forbidden",
            "Full suite runs require explicit user authorization",
        ]
        for section in expected_sections:
            self.assertIn(section, text, f"Missing section '{section}' in wave-progression.md")

    def test_agents_md_contains_wave_plan_architecture(self) -> None:
        """Verify that AGENTS.md mandates wave-based implementation plans and sequential gate lock."""
        agents_file = ROOT / "AGENTS.md"
        self.assertTrue(agents_file.is_file(), "AGENTS.md must exist.")

        text = agents_file.read_text(encoding="utf-8")
        self.assertTrue(
            "Wave-Based Execution Gating" in text or "Wave Progression" in text,
            "AGENTS.md must declare Wave-Based Execution Gating or Wave Progression.",
        )
        self.assertIn("wave-progression.md", text)

    def test_hitl_decisions_rules_contain_planning_mode_override(self) -> None:
        """Verify that .agents/rules/hitl-decisions.md contains the Planning-Mode Override."""
        rules_file = ROOT / ".agents" / "rules" / "hitl-decisions.md"
        self.assertTrue(rules_file.is_file(), "hitl-decisions.md must exist.")

        text = rules_file.read_text(encoding="utf-8")
        expected_sections = [
            "Planning-Mode HITL Override",
            "blocking design decision",
            "deferrable clarification",
            "needs_operator_decision",
        ]
        for section in expected_sections:
            self.assertIn(section, text, f"Missing section '{section}' in hitl-decisions.md")

    def test_hitl_decision_ledger_persistence_and_calibration(self) -> None:
        """Verify that HITLDecisionStore persists Wave 4 decisions and enables calibration."""
        import tempfile
        from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path
        ledger_path = _find_default_ledger_path()
        if not ledger_path.is_file():
            tmpdir = tempfile.mkdtemp()
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            store = HITLDecisionStore(ledger_path)
            seed_opts = [
                {"label": "Byte-identical mirror sync", "confidence_score": 0.80, "key": "byte_identical_mirror"},
                {"label": "Pointer to stage prompts", "confidence_score": 0.65, "key": "pointer_file"},
            ]
            for did in (
                "dec_wave4_mirror_file_sync",
                "dec_wave4_roster_hitl_surface",
                "dec_wave4_supplementary_sources",
            ):
                store.record_decision(
                    decision_id=did,
                    category="prompt_governance",
                    prompt="Seeded Wave 4 decision check?",
                    options=seed_opts,
                    selected_option=seed_opts[0],
                    operator_id="amit.ayer",
                )
            self.assertTrue(ledger_path.is_file(), "hitl_decision_ledger.json must exist.")
            history = store.get_history()
        else:
            store = HITLDecisionStore(ledger_path)
            history = store.get_history()
        self.assertGreaterEqual(len(history), 3, "Ledger must contain at least 3 seeded decisions.")

        decision_ids = {r.get("decision_id") for r in history}
        expected_ids = {
            "dec_wave4_mirror_file_sync",
            "dec_wave4_roster_hitl_surface",
            "dec_wave4_supplementary_sources",
        }
        self.assertTrue(
            expected_ids.issubset(decision_ids),
            f"Missing expected decision IDs. Found {decision_ids}, expected {expected_ids}",
        )

        for record in history:
            self.assertTrue(record.get("entry_digest"), "Record must have entry_digest.")
            if record.get("decision_id") in expected_ids:
                self.assertIn(record.get("operator_id"), {"amit.ayer", "amit_ayer"})
                self.assertTrue(record.get("selected_option"))
            elif record.get("operator_id"):
                self.assertIsInstance(record.get("operator_id"), str)

        # Verify feedback calibration uses historical answers
        options = [
            {"label": "Byte-identical mirror sync", "confidence_score": 0.80, "key": "byte_identical_mirror"},
            {"label": "Pointer to stage prompts", "confidence_score": 0.65, "key": "pointer_file"},
        ]
        calibrated = store.calibrate_options(options, category="prompt_governance")
        top_opt = next(o for o in calibrated if o.get("key") == "byte_identical_mirror")
        runner_opt = next(o for o in calibrated if o.get("key") == "pointer_file")

        self.assertGreater(
            top_opt["confidence_score"],
            0.80,
            "Historically affirmed option should receive a confidence boost.",
        )
        self.assertLess(
            runner_opt["confidence_score"],
            0.65,
            "Historically rejected option should receive a confidence penalty.",
        )

    def test_check_pytest_compliance_class_a_debug(self) -> None:
        """Verify that single test target commands qualify as compliant Class A debug runs."""
        valid_commands = [
            "pytest tests/test_governance_rules.py -v",
            "pytest tests/test_foo.py::TestBar::test_baz -v",
            ".venv/Scripts/python -m pytest tests/test_audio.py",
            'cmd /c ".venv\\Scripts\\python.exe -m pytest tests/test_stages.py -v --tb=short"',
        ]
        for cmd in valid_commands:
            is_valid, run_class, msg = verify_pytest_command(cmd, repo_root=ROOT)
            self.assertTrue(is_valid, f"Command '{cmd}' should be compliant: {msg}")
            self.assertEqual(run_class, "CLASS_A_DEBUG")

    def test_check_pytest_compliance_class_b_receipt(self) -> None:
        """Verify that multi-file or flagged runs qualify as compliant Class B receipt runs."""
        multi_file_cmd = "pytest tests/test_foo.py tests/test_bar.py -v --tb=short"
        is_valid, run_class, msg = verify_pytest_command(multi_file_cmd, repo_root=ROOT)
        self.assertTrue(is_valid, f"Multi-file command should be compliant: {msg}")
        self.assertEqual(run_class, "CLASS_B_RECEIPT")

        flagged_cmd = "pytest tests/test_foo.py --receipt-run"
        is_valid, run_class, msg = verify_pytest_command(flagged_cmd, repo_root=ROOT)
        self.assertTrue(is_valid, f"Flagged receipt command should be compliant: {msg}")
        self.assertEqual(run_class, "CLASS_B_RECEIPT")

    def test_check_pytest_compliance_rejects_unbounded_violations(self) -> None:
        """Verify that unbounded or broad-scope commands are rejected by the validator."""
        violating_commands = [
            "pytest",
            "pytest -v",
            "pytest tests/",
            "pytest tests",
            "pytest -k filter_name",
        ]
        for cmd in violating_commands:
            is_valid, run_class, msg = verify_pytest_command(cmd, repo_root=ROOT)
            self.assertFalse(is_valid, f"Command '{cmd}' should be rejected: {msg}")
            self.assertEqual(run_class, "REJECTED")

    def test_check_pytest_compliance_allows_explicit_full_suite(self) -> None:
        """Verify that --allow-full-suite permits full suite runs when explicitly authorized."""
        cmd = "pytest --allow-full-suite -v"
        is_valid, run_class, msg = verify_pytest_command(cmd, repo_root=ROOT)
        self.assertTrue(is_valid, f"Explicitly authorized full-suite should pass: {msg}")
        self.assertEqual(run_class, "AUTHORIZED_FULL_SUITE")

    def test_check_pytest_compliance_parallel_flags(self) -> None:
        """Verify that parallel flags (-n, --dist, -p, etc.) are recognized without corrupting target item scoping."""
        # Class A debug runs with parallel workers
        class_a_parallel = [
            "pytest tests/test_ssot_env.py -n 4 --timeout=60",
            "pytest tests/test_ssot_env.py -n=4 --timeout=60",
            "pytest tests/test_ssot_env.py --numprocesses 4 --timeout=60",
            "pytest tests/test_ssot_env.py --dist loadscope -n auto --timeout=60",
            "pytest tests/test_ssot_env.py --dist=loadscope -n=8 --timeout=60",
            "pytest tests/test_ssot_env.py -p dual_pipeline.testing.pytest_progress --timeout=60",
            "pytest tests/test_ssot_env.py --tb short --durations 10 --timeout=60",
        ]
        for cmd in class_a_parallel:
            is_valid, run_class, msg = verify_pytest_command(cmd, repo_root=ROOT)
            self.assertTrue(is_valid, f"Parallel Class A command '{cmd}' should be compliant: {msg}")
            self.assertEqual(run_class, "CLASS_A_DEBUG")

        # Class B receipt runs with parallel workers
        class_b_parallel = [
            "pytest tests/test_foo.py tests/test_bar.py -n 4 --timeout=60",
            "pytest tests/test_foo.py tests/test_bar.py --dist=loadscope -n auto --timeout=60",
            "pytest tests/test_foo.py tests/test_bar.py -n 8 --receipt-run --timeout=60",
        ]
        for cmd in class_b_parallel:
            is_valid, run_class, msg = verify_pytest_command(cmd, repo_root=ROOT)
            self.assertTrue(is_valid, f"Parallel Class B command '{cmd}' should be compliant: {msg}")
            self.assertEqual(run_class, "CLASS_B_RECEIPT")

        # Authorized full suite with parallel workers
        full_suite_parallel = "pytest --allow-full-suite -n 16 --dist=loadscope --timeout=60"
        is_valid, run_class, msg = verify_pytest_command(full_suite_parallel, repo_root=ROOT)
        self.assertTrue(is_valid, f"Parallel full-suite command should be compliant: {msg}")
        self.assertEqual(run_class, "AUTHORIZED_FULL_SUITE")

        # Unbounded parallel run without --allow-full-suite must be rejected
        unbounded_parallel = "pytest -n 4 --timeout=60"
        is_valid, run_class, msg = verify_pytest_command(unbounded_parallel, repo_root=ROOT)
        self.assertFalse(is_valid, f"Unbounded parallel command should be rejected: {msg}")
        self.assertEqual(run_class, "REJECTED")

    def test_pre_commit_hook_mechanically_enforces_governance(self) -> None:
        """Verify that .githooks/pre-commit executes tools/pre_commit_orchestrator.py."""
        pre_commit_path = ROOT / ".githooks" / "pre-commit"
        self.assertTrue(pre_commit_path.is_file(), ".githooks/pre-commit must exist.")

        content = pre_commit_path.read_text(encoding="utf-8")
        self.assertIn(
            "tools/pre_commit_orchestrator.py",
            content,
            ".githooks/pre-commit must execute tools/pre_commit_orchestrator.py.",
        )

    def test_commit_msg_hook_enforces_conventional_commits(self) -> None:
        """Verify that .githooks/commit-msg exists, is executable, and invokes tools/lint_commit_message.py."""
        commit_msg_path = ROOT / ".githooks" / "commit-msg"
        self.assertTrue(commit_msg_path.is_file(), ".githooks/commit-msg must exist.")
        self.assertTrue(os.access(commit_msg_path, os.X_OK), ".githooks/commit-msg must be executable.")

        content = commit_msg_path.read_text(encoding="utf-8")
        self.assertIn(
            "tools/lint_commit_message.py",
            content,
            ".githooks/commit-msg must execute tools/lint_commit_message.py.",
        )

    def test_conformance_map_integrity(self) -> None:
        """Verify that conformance-map.md accurately tracks all AGENTS.md controls without line drift."""
        conformance_file = ROOT / ".agents" / "rules" / "conformance-map.md"
        self.assertTrue(conformance_file.is_file(), "conformance-map.md must exist.")

        text = conformance_file.read_text(encoding="utf-8")
        # Verify no stale hardcoded line-number headers remain
        self.assertNotIn(
            "(AGENTS.md L",
            text,
            "conformance-map.md section headers must not contain hardcoded line-number ranges.",
        )
        # Verify GraphDB mutation breakdown and test coverage is tracked
        self.assertIn(
            "test_graph_mutation_summary.py",
            text,
            "conformance-map.md must map test_graph_mutation_summary.py.",
        )
        self.assertIn(
            "dual_pipeline/graph_delta.py",
            text,
            "conformance-map.md must map dual_pipeline/graph_delta.py.",
        )
        self.assertIn(
            "| Wave progression execution-evidence-gated | ✅ | ✅ | ✅ | ✅ | ✅ |",
            text,
            "conformance-map.md must reflect test and git hook coverage for Wave progression.",
        )
        self.assertIn(
            "test_unified_agent_architecture.py",
            text,
            "conformance-map.md must map test_unified_agent_architecture.py.",
        )
        self.assertIn(
            "| Skills, agents, sub-agents, hooks & rules unified architecture | ✅ | ✅ | ✅ | ✅ | ✅ |",
            text,
            "conformance-map.md must reflect coverage for unified agent architecture.",
        )
        self.assertIn(
            "model-neutral-artifact-naming.md",
            text,
            "conformance-map.md must map model-neutral-artifact-naming.md.",
        )

    def test_agents_md_contains_model_neutral_naming_invariant(self) -> None:
        """Verify that AGENTS.md defines/links model-neutral-artifact-naming.md."""
        agents_file = ROOT / "AGENTS.md"
        self.assertTrue(agents_file.is_file(), "AGENTS.md must exist.")

        text = agents_file.read_text(encoding="utf-8")
        self.assertIn(
            "model-neutral-artifact-naming.md",
            text,
            "AGENTS.md must index model-neutral-artifact-naming.md.",
        )

    def test_model_neutral_rules_file_exists_and_declares_tokens(self) -> None:
        """Verify that .agents/rules/model-neutral-artifact-naming.md defines prohibited tokens."""
        rules_file = ROOT / ".agents" / "rules" / "model-neutral-artifact-naming.md"
        self.assertTrue(rules_file.is_file(), "model-neutral-artifact-naming.md must exist.")

        text = rules_file.read_text(encoding="utf-8")
        self.assertIn("CRITICAL INVARIANT: MODEL-AGNOSTIC FILE & ARTIFACT NAMING", text)
        self.assertIn("Strict LLM Model Name Prohibition in Filesystem Paths", text)

    def test_scratchpad_reasoning_transparency_rules(self) -> None:
        """Verify that scratchpad-reasoning-transparency.md exists, defines 4 pillars and prompt directive, and is wired into AGENTS.md and conformance map."""
        # 1. Rule file existence & contents
        rule_file = ROOT / ".agents" / "rules" / "scratchpad-reasoning-transparency.md"
        self.assertTrue(rule_file.is_file(), "scratchpad-reasoning-transparency.md must exist.")
        rule_text = rule_file.read_text(encoding="utf-8")

        # Four mandatory pillars
        self.assertIn("Cognitive Breakdown & Thinking Deliberation", rule_text)
        self.assertIn("Reasoning Steps", rule_text)
        self.assertIn("API & Tool Invocations", rule_text)
        self.assertIn("LLM & Model Telemetry", rule_text)

        # Prompt to implement directive
        self.assertIn("CRITICAL GOVERNANCE DIRECTIVE: AGENT REASONING & SCRATCHPAD TRANSPARENCY", rule_text)
        self.assertIn("ACTIVE MODEL & API TELEMETRY", rule_text)
        self.assertIn("COGNITIVE THINKING & DELIBERATION", rule_text)
        self.assertIn("API & TOOL CALL AUDITING", rule_text)

        # Invariant 7 preservation
        self.assertIn("Persistent Filename Neutrality (AGENTS.md Invariant 7)", rule_text)

        # 2. Root AGENTS.md wire-in
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("scratchpad-reasoning-transparency.md", agents_text)
        self.assertIn("engineering-output.md", agents_text)

        # 3. Conformance map tracking
        conf_text = (ROOT / ".agents" / "rules" / "conformance-map.md").read_text(encoding="utf-8")
        self.assertIn("scratchpad-reasoning-transparency.md", conf_text)
        self.assertIn("Reasoning & Telemetry Transparency", conf_text)
        self.assertIn("engineering-output.md", conf_text)


if __name__ == "__main__":
    unittest.main()
