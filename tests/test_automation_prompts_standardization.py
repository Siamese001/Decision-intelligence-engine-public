"""tests/test_automation_prompts_standardization.py

Comprehensive verification and regression test suite for standardized Antigravity
automation prompts, sidecar SSOT dereferencing, and fail-closed link governance.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from tools.task_restoration_core.ssot_pointers import (
    CONSOLIDATED_TASK_ALIASES,
    MAX_POINTER_WORDS,
    POINTER_REGEX,
    TASK_SIDECAR_MAP,
    is_valid_pointer_prompt,
    resolve_canonical_task_id,
)
from tools.lint_automation_ssot_links import audit_host_sidecars, audit_reconciliation_manifest
from tools.lint_prompt_ssot import run_prompt_ssot_lint

REPO_ROOT = Path(__file__).resolve().parents[1]


class AutomationPromptsStandardizationTests(unittest.TestCase):
    def test_task_sidecar_map_has_eight_canonical_tasks_and_six_aliases(self) -> None:
        """Verify that TASK_SIDECAR_MAP contains the 17 canonical tasks and CONSOLIDATED_TASK_ALIASES maps the 7 absorbed tasks."""
        self.assertEqual(len(TASK_SIDECAR_MAP), 17)
        expected_tasks = [
            "knowledge_graph_hygiene",
            "ssot_drift_remediation",
            "pipeline_outcome_assurance",
            "storage_repository_hygiene",
            "weekly_market_intelligence",
            "pr_queue_observer",
            "testing_gaps_assurance",
            "e2e_critical_functionality_assurance",
            "corner_foresight_blindspot_scout",
            "hotspot_value_enhancement",
            "tokenomics_cost_optimization",
            "agentic_repo_best_practices_scout",
            "retrieval_surface_assurance",
            "system_learning_assurance",
            "evals_surface_assurance",
            "nea_charter_portfolio_roster",
            "google_partner_advisory_weekly_sync",
        ]
        for task_id in expected_tasks:
            self.assertIn(task_id, TASK_SIDECAR_MAP)

        self.assertEqual(
            TASK_SIDECAR_MAP["weekly_market_intelligence"]["title"],
            "Energy Intelligence Agent",
        )

        self.assertEqual(len(CONSOLIDATED_TASK_ALIASES), 7)
        self.assertEqual(resolve_canonical_task_id("worktree_coverage_cleanup"), "pr_queue_observer")
        self.assertEqual(resolve_canonical_task_id("repository_antipattern_audit"), "ssot_drift_remediation")
        self.assertEqual(resolve_canonical_task_id("technical_debt_elimination"), "ssot_drift_remediation")
        self.assertEqual(resolve_canonical_task_id("third_party_tool_consolidation"), "ssot_drift_remediation")
        self.assertEqual(resolve_canonical_task_id("cost_quality_review"), "pipeline_outcome_assurance")
        self.assertEqual(resolve_canonical_task_id("timeout_lock_watchdog"), "pipeline_outcome_assurance")
        self.assertEqual(resolve_canonical_task_id("frontier_llm_research_scout"), "agentic_repo_best_practices_scout")

    def test_registration_reconciliation_has_eight_tasks(self) -> None:
        """Verify that registration_reconciliation.json declares 17 canonical tasks with strict anti-inlining."""
        manifest_path = REPO_ROOT / "config" / "scheduled_tasks" / "registration_reconciliation.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        summary = data.get("reconciliation_summary", {})
        self.assertEqual(summary.get("total_defined_tasks"), 17)
        self.assertEqual(summary.get("total_reconciled"), 17)
        self.assertTrue(summary.get("inline_prompts_prohibited"))

        tasks = {t["task_id"]: t for t in data.get("tasks", [])}
        self.assertEqual(len(tasks), 17)
        expected_tasks = [
            "knowledge_graph_hygiene",
            "ssot_drift_remediation",
            "pipeline_outcome_assurance",
            "storage_repository_hygiene",
            "weekly_market_intelligence",
            "pr_queue_observer",
            "testing_gaps_assurance",
            "e2e_critical_functionality_assurance",
            "corner_foresight_blindspot_scout",
            "hotspot_value_enhancement",
            "tokenomics_cost_optimization",
            "agentic_repo_best_practices_scout",
            "retrieval_surface_assurance",
            "system_learning_assurance",
            "evals_surface_assurance",
            "nea_charter_portfolio_roster",
            "google_partner_advisory_weekly_sync",
        ]
        for task_id in expected_tasks:
            self.assertIn(task_id, tasks)

        self.assertEqual(
            tasks["weekly_market_intelligence"]["title"],
            "Energy Intelligence Agent",
        )
        self.assertEqual(
            tasks["agentic_repo_best_practices_scout"]["title"],
            "Agentic Frontier Architecture Agent",
        )
        self.assertEqual(
            tasks["knowledge_graph_hygiene"]["title"],
            "Graph Hygiene Agent",
        )
        self.assertEqual(
            tasks["pipeline_outcome_assurance"]["title"],
            "Pipeline Assurance Agent",
        )
        self.assertEqual(
            tasks["storage_repository_hygiene"]["title"],
            "Storage Hygiene Agent",
        )

        for tid, t in tasks.items():
            words = t["title"].split()
            self.assertEqual(words[-1], "Agent", f"Task {tid} title must end with Agent")
            max_words = 7 if tid in ("nea_charter_portfolio_roster", "google_partner_advisory_weekly_sync") else 4
            self.assertLessEqual(len(words), max_words, f"Task {tid} title exceeds word limit: {t['title']}")
            self.assertFalse(t.get("allow_inline_prompt_body"), f"Task {tid} must prohibit inline prompt bodies")
            self.assertLessEqual(len(t.get("dereferencer_prompt", "").split()), MAX_POINTER_WORDS)

    def test_canonical_prompts_governance_and_query_headers(self) -> None:
        """Verify all 6 canonical prompts exist, have C01-C12 headers and D01-D03 domain dispositions."""
        for task_id, info in TASK_SIDECAR_MAP.items():
            prompt_file = REPO_ROOT / info["canonical_prompt"]
            self.assertTrue(prompt_file.is_file(), f"Missing prompt file: {prompt_file}")
            content = prompt_file.read_text(encoding="utf-8")

            # Must contain Governed Common Contract
            self.assertIn("GOVERNED COMMON CONTRACT: urn:nee:scheduled-task-common-control-matrix:1.0.0", content)
            self.assertIn("C01: Trusted Launch & Scope", content)
            if not info["canonical_prompt"].startswith("automations/prompts/sidecars/"):
                self.assertIn("DOMAIN DISPOSITIONS:", content)
                self.assertIn("D03:", content, f"Task {task_id} missing D03 disposition")

    def test_pipeline_and_cost_consolidated_prompt_query_baseline_checks(self) -> None:
        """Verify consolidated pipeline and cost prompt includes graphdb_read_query and GraphDB mutation checks."""
        pipeline_prompt = (REPO_ROOT / "automations" / "prompts" / "pipeline_outcome_assurance_agent.md").read_text(encoding="utf-8")

        self.assertIn("graphdb_read_query", pipeline_prompt)
        self.assertIn("PRAGMA integrity_check", pipeline_prompt)
        self.assertIn("PRAGMA foreign_key_check", pipeline_prompt)
        self.assertIn("Zero Dry-Run / Zero-Mock Ingestion", pipeline_prompt)
        self.assertIn("Mandatory GraphDB Mutation Invariant", pipeline_prompt)

    def test_mece_cli_linter_disjointness_across_six_prompts(self) -> None:
        """Verify non-overlap of primary CLI linters across the 6 canonical prompts and standalone PR Queue Observer."""
        ssot_prompt = (REPO_ROOT / "automations" / "prompts" / "ssot_drift_remediation_agent.md").read_text(encoding="utf-8")
        pr_prompt = (REPO_ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md").read_text(encoding="utf-8")
        srh_prompt = (REPO_ROOT / "automations" / "prompts" / "storage_hygiene_agent.md").read_text(encoding="utf-8")
        poa_prompt = (REPO_ROOT / "automations" / "prompts" / "pipeline_outcome_assurance_agent.md").read_text(encoding="utf-8")
        gh_prompt = (REPO_ROOT / "automations" / "prompts" / "graph_hygiene_agent.md").read_text(encoding="utf-8")
        wmi_prompt = (REPO_ROOT / "automations" / "prompts" / "weekly_market_intelligence_agent.md").read_text(encoding="utf-8")

        # Codebase linters belong exclusively in ssot_drift_remediation_agent.md
        for tool_name in ("lint_anti_hardcoding.py", "audit_agentic_boundaries.py", "run_governance_linters.py"):
            self.assertIn(tool_name, ssot_prompt)
            for other_prompt in (pr_prompt, srh_prompt, poa_prompt, gh_prompt, wmi_prompt):
                self.assertNotIn(tool_name, other_prompt, f"Tool {tool_name} leaked outside ssot_drift_remediation_agent.md")

        # Storage candidate dry-run belongs exclusively in storage_hygiene_agent.md (not pr_queue_observer_agent.md)
        self.assertIn("storage_hygiene_core/apply.py --dry-run", srh_prompt)
        for other_prompt in (ssot_prompt, pr_prompt, poa_prompt, gh_prompt, wmi_prompt):
            self.assertNotIn("storage_hygiene_core/apply.py --dry-run", other_prompt)

    def test_live_host_sidecars_conform_to_pointer_governance(self) -> None:
        """Verify all live host sidecars in ~/.gemini/config/sidecars/ pass link governance."""
        sidecars_dir = Path.home() / ".gemini" / "config" / "sidecars"
        if not sidecars_dir.is_dir():
            self.skipTest("Host sidecars directory not present")

        errors = audit_host_sidecars(REPO_ROOT, sidecars_dir)
        self.assertEqual(errors, [], f"Live host sidecar audit defects: {errors}")

    def test_audit_host_sidecars_fails_closed_on_unmapped_directory(self) -> None:
        """Adversarial check: Unmapped rogue directory in sidecars_dir must fail closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create a valid sidecar structure for a rogue task
            rogue_dir = tmppath / "rogue-unmapped-task"
            rogue_dir.mkdir(parents=True)
            rogue_json = rogue_dir / "sidecar.json"
            rogue_json.write_text(json.dumps({
                "builtin": "schedule",
                "args": [
                    "0 0 * * *", "agentapi", "new-conversation", "--",
                    "Read and execute the task specification defined at file:///some/path.md following all rules and invariants within."
                ]
            }))

            errors = audit_host_sidecars(REPO_ROOT, tmppath)
            self.assertTrue(any("Unmapped/unmanaged sidecar detected" in e for e in errors))

    def test_audit_host_sidecars_fails_closed_on_inline_prompt_body(self) -> None:
        """Adversarial check: Sidecar with inline markdown body must fail closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target_dir = tmppath / TASK_SIDECAR_MAP["pipeline_outcome_assurance"]["sidecar_dir"]
            target_dir.mkdir(parents=True)
            inline_json = target_dir / "sidecar.json"
            inline_json.write_text(json.dumps({
                "builtin": "schedule",
                "args": [
                    "0 1 * * 0", "agentapi", "new-conversation", "--",
                    "# INLINE PROMPT TITLE\n\nThis is a long inline prompt that violates SSOT link pointer governance..." * 5
                ]
            }))

            errors = audit_host_sidecars(REPO_ROOT, tmppath)
            self.assertTrue(any("inlines prompt body" in e or "contains raw markdown headers" in e for e in errors))

    def test_testing_gaps_assurance_adapter_emits_validated_plan(self) -> None:
        """Verify run_testing_gaps_assurance mechanically generates and validates docs/plans/<branch-slug>.md when gaps exist."""
        from tools.scheduled_task_core.adapters import run_testing_gaps_assurance
        from tools.scheduled_task_core.authority import AuthorityContext
        from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
        from tools.validate_implementation_plan import validate_plan_content

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "untested_hotspot.py").write_text("def hotspot():\n    return 42\n", encoding="utf-8")
            run_dir = tmppath / "reports" / "testing_gaps" / "run_001"
            ctx = AuthorityContext(
                task_id="testing_gaps_assurance",
                run_id="run_001",
                trigger_origin=TriggerOrigin.SCHEDULER_CRON,
                execution_mode=ExecutionMode.SCHEDULED,
                actor="scheduler",
                is_scheduled=True,
            )
            res = run_testing_gaps_assurance(ctx, tmppath, run_dir)
            self.assertEqual(res.terminal_outcome, "HITL_REQUIRED")
            self.assertIsNotNone(res.implementation_plan_path)
            self.assertTrue(res.implementation_plan_path.is_file())
            self.assertIsNotNone(res.implementation_plan_sha256)
            plan_text = res.implementation_plan_path.read_text(encoding="utf-8")
            ok, errs = validate_plan_content(plan_text, str(res.implementation_plan_path))
            self.assertTrue(ok, f"Generated testing gaps remediation plan failed validation: {errs}")
            worktree_plan = tmppath / "docs" / "plans" / "test_testing_gaps_assurance_run_001.md"
            self.assertTrue(worktree_plan.is_file())

    def test_testing_gaps_prompt_enforces_worktree_first_and_hitl_format(self) -> None:
        """Verify testing_gaps_assurance_agent.md passes INV-PROMPT-SSOT-07 procedural gate lock checks."""
        ok, errors = run_prompt_ssot_lint(REPO_ROOT)
        self.assertTrue(ok, f"Prompt SSOT lint failed: {errors}")

    def test_all_nine_canonical_prompts_pass_scheduled_spoof_guard_and_dispatch(self) -> None:
        """Verify all core canonical prompts pass resolve_trusted_launch_context in scheduled mode and dispatch cleanly."""
        from tools.scheduled_task_core.contracts import assert_registration_resolved, resolve_trusted_launch_context
        from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter

        adapter = AntigravityHostLaunchAdapter(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for task_id, info in TASK_SIDECAR_MAP.items():
                self.assertTrue(info["cron"].startswith("CRON_TZ=America/New_York "))
                prompt_text = (REPO_ROOT / info["canonical_prompt"]).read_text(encoding="utf-8")
                origin, mode = resolve_trusted_launch_context(
                    trigger_origin="scheduler_cron",
                    requested_mode="scheduled",
                    prompt_content=prompt_text,
                    strict_spoof_detection=True,
                )
                self.assertEqual(origin.value, "scheduler_cron")
                self.assertEqual(mode.value, "scheduled")
                assert_registration_resolved(task_id)
                if info["canonical_prompt"].startswith("automations/prompts/sidecars/"):
                    continue
                res = adapter.launch_task(task_id, "scheduled", tmppath, reject_empty_callback=False)
                self.assertIsNotNone(res.terminal_outcome)

    def test_all_twelve_canonical_prompts_enforce_five_point_runtime_proof_gate(self) -> None:
        """Verify all 15 core canonical prompts include the 5-Point Counterfactual Runtime Proof Gate (INV-RUNTIME-PROOF-5POINT)."""
        self.assertEqual(len(TASK_SIDECAR_MAP), 17)
        for task_id, info in TASK_SIDECAR_MAP.items():
            if info["canonical_prompt"].startswith("automations/prompts/sidecars/"):
                continue
            prompt_text = (REPO_ROOT / info["canonical_prompt"]).read_text(encoding="utf-8")
            self.assertIn("INV-RUNTIME-PROOF-5POINT", prompt_text, f"{task_id} missing INV-RUNTIME-PROOF-5POINT")
            self.assertIn("PRE_MUTATION_RUN", prompt_text, f"{task_id} missing PRE_MUTATION_RUN")
            self.assertIn("POST_MUTATION_RUN", prompt_text, f"{task_id} missing POST_MUTATION_RUN")
            self.assertIn("DIFF_LINE_COVERAGE == 100%", prompt_text, f"{task_id} missing DIFF_LINE_COVERAGE == 100%")
            self.assertIn(
                "| Target File:Lines | Runtime Command | Pre-Fix Observed Behavior | Post-Fix Observed Behavior | Independent State Readback |",
                prompt_text,
                f"{task_id} missing 5-column runtime proof table",
            )

    def test_sync_host_config_sidecars_binds_project_id_and_prunes_retired(self) -> None:
        """Verify sync_host_config_sidecars binds all 17 canonical sidecars to projectId and prunes retired dirs."""
        from tools.reconcile_automation_sidecars import resolve_project_id_for_repo, sync_host_config_sidecars
        from tools.task_restoration_core.ssot_pointers import resolve_primary_repo_root

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            projects_dir = config_dir / "projects"
            projects_dir.mkdir(parents=True)
            primary_root = resolve_primary_repo_root(REPO_ROOT)
            proj_id = "df5c8b21-2423-44ae-ace7-c590f41e1fdd"
            (projects_dir / f"{proj_id}.json").write_text(
                json.dumps({
                    "id": proj_id,
                    "name": "decision-intelligence-engine",
                    "projectResources": {
                        "resources": [{"gitFolder": {"folderUri": f"file://{primary_root}"}}]
                    },
                }),
                encoding="utf-8",
            )
            config_path = config_dir / "config.json"
            config_path.write_text(
                json.dumps({
                    "sidecars": {
                        "local-third-party-tool-consolidation": {"enabled": False, "projectId": "old-id"},
                        "energy-utilities-best-practices": {"enabled": True, "projectId": "wrong-id"},
                    }
                }),
                encoding="utf-8",
            )
            resolved_id = resolve_project_id_for_repo(config_dir, REPO_ROOT)
            self.assertEqual(resolved_id, proj_id)
            res = sync_host_config_sidecars(config_path, resolved_id, dry_run=False)
            self.assertTrue(res["changed"])
            self.assertEqual(res["pruned"], 1)
            self.assertEqual(res["synced"], 17)

            updated = json.loads(config_path.read_text(encoding="utf-8"))["sidecars"]
            self.assertNotIn("local-third-party-tool-consolidation", updated)
            for info in TASK_SIDECAR_MAP.values():
                sdir = info["sidecar_dir"]
                self.assertIn(sdir, updated)
                self.assertEqual(updated[sdir]["projectId"], proj_id)
            self.assertTrue(updated["energy-utilities-best-practices"]["enabled"])

    def test_audit_host_sidecars_fails_closed_on_display_name_drift(self) -> None:
        """Adversarial check: Sidecar with stale display_name must fail closed."""
        from tools.task_restoration_core.ssot_pointers import generate_ssot_pointer_prompt

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for task_id, info in TASK_SIDECAR_MAP.items():
                tdir = tmppath / info["sidecar_dir"]
                tdir.mkdir(parents=True)
                title = "Market Agent" if task_id == "weekly_market_intelligence" else info["title"]
                (tdir / "sidecar.json").write_text(
                    json.dumps({
                        "builtin": "schedule",
                        "display_name": title,
                        "args": [
                            info["cron"],
                            "agentapi",
                            "new-conversation",
                            "--",
                            generate_ssot_pointer_prompt(task_id, REPO_ROOT),
                        ],
                    }),
                    encoding="utf-8",
                )
            errors = audit_host_sidecars(REPO_ROOT, tmppath)
            self.assertTrue(
                any("display_name 'Market Agent' does not match canonical title 'Energy Intelligence Agent'" in e for e in errors),
                f"Expected display_name drift error, got: {errors}",
            )

    def test_deoverlapped_agent_boundaries_and_master_sequence(self) -> None:
        """Verify shared governance contract, 1..17 master sequence order, and de-overlap invariants across all 15 core prompts."""
        shared_contract = REPO_ROOT / "docs" / "governance" / "shared_automation_governance_contract.md"
        self.assertTrue(shared_contract.is_file(), "Missing shared_automation_governance_contract.md")
        contract_text = shared_contract.read_text(encoding="utf-8")
        for inv in (
            "INV-DEOVERLAP-SHARED-CONTRACT",
            "INV-DEOVERLAP-TESTING-VS-E2E",
            "INV-DEOVERLAP-RETRIEVAL-VS-EVALS",
            "INV-DEOVERLAP-FRONTIER-VS-ARCHITECTURE",
            "INV-DEOVERLAP-STORAGE-VS-GRAPH",
        ):
            self.assertIn(inv, contract_text)

        orders = sorted(info["master_sequence_order"] for info in TASK_SIDECAR_MAP.values())
        self.assertEqual(orders, list(range(1, 18)))

        for task_id, info in TASK_SIDECAR_MAP.items():
            if info["canonical_prompt"].startswith("automations/prompts/sidecars/"):
                continue
            prompt_text = (REPO_ROOT / info["canonical_prompt"]).read_text(encoding="utf-8")
            self.assertIn("docs/governance/shared_automation_governance_contract.md", prompt_text, f"{task_id} missing shared contract link")
            self.assertIn("INV-DEOVERLAP-SHARED-CONTRACT", prompt_text, f"{task_id} missing INV-DEOVERLAP-SHARED-CONTRACT")
            self.assertIn("Master Sequence: Stage", prompt_text, f"{task_id} missing Master Sequence header")

        pair_invariants = {
            "INV-DEOVERLAP-TESTING-VS-E2E": (
                "testing_gaps_assurance_agent.md",
                "e2e_critical_functionality_assurance_agent.md",
            ),
            "INV-DEOVERLAP-RETRIEVAL-VS-EVALS": (
                "retrieval_surface_agent.md",
                "evals_surface_agent.md",
            ),
            "INV-DEOVERLAP-FRONTIER-VS-ARCHITECTURE": (
                "agentic_repo_best_practices_agent.md",
            ),
            "INV-DEOVERLAP-STORAGE-VS-GRAPH": (
                "storage_hygiene_agent.md",
                "graph_hygiene_agent.md",
            ),
        }
        for inv_token, fnames in pair_invariants.items():
            for fname in fnames:
                txt = (REPO_ROOT / "automations" / "prompts" / fname).read_text(encoding="utf-8")
                self.assertIn(inv_token, txt, f"{fname} missing {inv_token}")


if __name__ == "__main__":
    unittest.main()
