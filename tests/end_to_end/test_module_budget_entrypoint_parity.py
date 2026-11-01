"""
tests/end_to_end/test_module_budget_entrypoint_parity.py

End-to-End Test Suite for Wave 3 Module Size Policy Entrypoint Parity.
Proves that equivalent inputs receive equivalent size verdicts across:
- Direct evaluator (tools.file_budget_core.policy)
- Staged / shift-left check (tools.shift_left_core.analyze_shift_left_file)
- Scheduled audit (tools.scheduled_task_core.adapters_core.audits.run_repository_antipattern_audit)
- Wave gate (tools.run_wave_gate.WaveEvidenceVerifier.audit_l0_line_budgets)
- Ingestion acceptance (tools.active_ingestion_acceptance_core.helpers.check_line_budgets)
"""

from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from tools.active_ingestion_acceptance_core.helpers import check_line_budgets
from tools.file_budget_core import evaluate_module_file, evaluate_module_source, load_candidate_policy
from tools.run_wave_gate import WaveEvidenceVerifier
from tools.scheduled_task_core.adapters_core.audits import run_repository_antipattern_audit
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.shift_left_core import analyze_shift_left_file


def _make_scheduled_context() -> AuthorityContext:
    return AuthorityContext(
        task_id="repository_antipattern_audit",
        run_id="run-parity-test-01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="system_scheduler",
        allowed_capabilities={"read_only_graph_inspection"},
        is_scheduled=True,
    )


class TestModuleBudgetEntrypointParityE2E(unittest.TestCase):
    def test_01_clean_module_passes_all_entrypoints(self) -> None:
        """Clean module (<400 lines, CC <= 15) passes across all 5 entry points."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            l0_dir = repo_root / "dual_pipeline" / "l0"
            l0_dir.mkdir(parents=True, exist_ok=True)
            mod_file = l0_dir / "clean_module.py"
            code = (
                '"""Clean module docstring."""\n'
                "def compute(a: int) -> int:\n"
                "    if a > 0:\n"
                "        return a * 2\n"
                "    return 0\n"
            )
            mod_file.write_text(code, encoding="utf-8")
            rel_path = "dual_pipeline/l0/clean_module.py"

            # 1. Direct evaluator
            verdict = evaluate_module_file(mod_file, repo_root=repo_root)
            self.assertEqual(verdict.verdict, "PASS")
            self.assertFalse(verdict.is_blocking)
            self.assertFalse(verdict.warning)

            # 2. Staged shift-left check
            ok_shift, violations_shift = analyze_shift_left_file(
                repo_root=repo_root,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules={},
            )
            self.assertTrue(ok_shift)
            self.assertEqual(len(violations_shift), 0)

            # 3. Scheduled antipattern audit
            run_dir = repo_root / "run_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            res_audit = run_repository_antipattern_audit(
                context=_make_scheduled_context(),
                fixture_root=repo_root,
                run_dir=run_dir,
            )
            self.assertEqual(res_audit.terminal_outcome, "COMPLETE")
            self.assertEqual(res_audit.findings_count, 0)

            # 4. Wave gate l0 audit
            l0_ok, l0_viols = WaveEvidenceVerifier.audit_l0_line_budgets(repo_root)
            self.assertTrue(l0_ok)
            self.assertEqual(len(l0_viols), 0)

            # 5. Ingestion acceptance line budget
            check_line_budgets(repo_root, (rel_path,))

    def test_02_standard_warning_module_parity(self) -> None:
        """Module in 401-650 range with compliant CC yields warning without blocking or HITL debt."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            l0_dir = repo_root / "dual_pipeline" / "l0"
            l0_dir.mkdir(parents=True, exist_ok=True)
            mod_file = l0_dir / "service_core.py"
            header = '"""Service core module."""\ndef run(x):\n    if x > 0:\n        return x + 1\n    return 0\n'
            filler = "\n".join([f"# filler comment line {i}" for i in range(450)])
            mod_file.write_text(header + filler, encoding="utf-8")
            rel_path = "dual_pipeline/l0/service_core.py"

            # 1. Direct evaluator
            verdict = evaluate_module_file(mod_file, repo_root=repo_root)
            self.assertEqual(verdict.verdict, "WARNING")
            self.assertFalse(verdict.is_blocking)
            self.assertTrue(verdict.warning)
            self.assertEqual(verdict.classification, "warning_standard")

            # 2. Staged shift-left check (nonblocking warning does NOT fail stage gate)
            ok_shift, violations_shift = analyze_shift_left_file(
                repo_root=repo_root,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules={},
            )
            self.assertTrue(ok_shift)
            self.assertEqual(len(violations_shift), 0)

            # 3. Scheduled antipattern audit (does NOT trigger HITL_REQUIRED debt)
            run_dir = repo_root / "run_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            res_audit = run_repository_antipattern_audit(
                context=_make_scheduled_context(),
                fixture_root=repo_root,
                run_dir=run_dir,
            )
            self.assertEqual(res_audit.terminal_outcome, "COMPLETE")
            self.assertEqual(res_audit.findings_count, 0)

            # 4. Wave gate l0 audit (nonblocking warning does not fail wave gate)
            l0_ok, l0_viols = WaveEvidenceVerifier.audit_l0_line_budgets(repo_root)
            self.assertTrue(l0_ok)
            self.assertEqual(len(l0_viols), 0)

            # 5. Ingestion acceptance line budget (passes without raising)
            check_line_budgets(repo_root, (rel_path,))

    def test_03_declarative_warning_module_parity(self) -> None:
        """Pure declarative module in 651-800 range (zero branches, CC <= 5) yields nonblocking warning."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            l0_dir = repo_root / "dual_pipeline" / "l0"
            l0_dir.mkdir(parents=True, exist_ok=True)
            mod_file = l0_dir / "declarative_table.py"
            # Pure declarative structure: dictionary table with 700 lines
            entries = "\n".join([f'    "item_{i}": {{"id": {i}, "name": "val_{i}"}},' for i in range(700)])
            code = f'"""Declarative mapping table."""\nTABLE_MAP = {{\n{entries}\n}}\n'
            mod_file.write_text(code, encoding="utf-8")
            rel_path = "dual_pipeline/l0/declarative_table.py"

            # 1. Direct evaluator
            verdict = evaluate_module_file(mod_file, repo_root=repo_root)
            self.assertEqual(verdict.verdict, "WARNING")
            self.assertFalse(verdict.is_blocking)
            self.assertTrue(verdict.warning)
            self.assertEqual(verdict.classification, "warning_declarative")

            # 2. Staged shift-left check
            ok_shift, violations_shift = analyze_shift_left_file(
                repo_root=repo_root,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules={},
            )
            self.assertTrue(ok_shift)
            self.assertEqual(len(violations_shift), 0)

            # 3. Scheduled antipattern audit
            run_dir = repo_root / "run_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            res_audit = run_repository_antipattern_audit(
                context=_make_scheduled_context(),
                fixture_root=repo_root,
                run_dir=run_dir,
            )
            self.assertEqual(res_audit.terminal_outcome, "COMPLETE")
            self.assertEqual(res_audit.findings_count, 0)

            # 4. Wave gate l0 audit
            l0_ok, l0_viols = WaveEvidenceVerifier.audit_l0_line_budgets(repo_root)
            self.assertTrue(l0_ok)
            self.assertEqual(len(l0_viols), 0)

            # 5. Ingestion acceptance line budget
            check_line_budgets(repo_root, (rel_path,))

    def test_04_oversized_ordinary_module_blocks_all_entrypoints(self) -> None:
        """Ordinary implementation module in 651-800 range with branch logic is blocked across all entry points."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            l0_dir = repo_root / "dual_pipeline" / "l0"
            l0_dir.mkdir(parents=True, exist_ok=True)
            mod_file = l0_dir / "ordinary_oversized.py"
            # 700 lines with branch logic
            func = (
                "def handler(x):\n"
                "    if x > 10:\n"
                "        return 1\n"
                "    elif x > 5:\n"
                "        return 2\n"
                "    return 0\n"
            )
            filler = "\n".join([f"# logic filler {i}" for i in range(700)])
            mod_file.write_text(func + filler, encoding="utf-8")
            rel_path = "dual_pipeline/l0/ordinary_oversized.py"

            # 1. Direct evaluator
            verdict = evaluate_module_file(mod_file, repo_root=repo_root)
            self.assertEqual(verdict.verdict, "BLOCK")
            self.assertTrue(verdict.is_blocking)
            self.assertEqual(verdict.classification, "exceeds_ordinary_warning")

            # 2. Staged shift-left check
            ok_shift, violations_shift = analyze_shift_left_file(
                repo_root=repo_root,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules={},
            )
            self.assertFalse(ok_shift)
            self.assertTrue(any("exceeds 650-line ordinary ceiling" in v for v in violations_shift))

            # 3. Scheduled antipattern audit
            run_dir = repo_root / "run_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            res_audit = run_repository_antipattern_audit(
                context=_make_scheduled_context(),
                fixture_root=repo_root,
                run_dir=run_dir,
            )
            self.assertEqual(res_audit.terminal_outcome, "HITL_REQUIRED")
            self.assertEqual(res_audit.findings_count, 1)

            # 4. Wave gate l0 audit
            l0_ok, l0_viols = WaveEvidenceVerifier.audit_l0_line_budgets(repo_root)
            self.assertFalse(l0_ok)
            self.assertEqual(len(l0_viols), 1)

            # 5. Ingestion acceptance line budget
            with self.assertRaises(RuntimeError) as ctx:
                check_line_budgets(repo_root, (rel_path,))
            self.assertIn("violated module size policy", str(ctx.exception))

    def test_05_structural_complexity_violation_blocks_even_under_400_lines(self) -> None:
        """Small module (50 lines) with function CC > 15 is blocked across all entry points."""
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            l0_dir = repo_root / "dual_pipeline" / "l0"
            l0_dir.mkdir(parents=True, exist_ok=True)
            mod_file = l0_dir / "complex_small.py"
            # High CC nested branches in small file
            complex_func = (
                "def deeply_nested(a, b, c, d, e, f, g, h, i, j):\n"
                "    if a:\n"
                "        if b:\n"
                "            if c:\n"
                "                if d:\n"
                "                    if e:\n"
                "                        if f:\n"
                "                            if g:\n"
                "                                if h:\n"
                "                                    if i:\n"
                "                                        if j:\n"
                "                                            return 1\n"
                "    return 0\n"
            )
            mod_file.write_text(complex_func, encoding="utf-8")
            rel_path = "dual_pipeline/l0/complex_small.py"

            # 1. Direct evaluator
            verdict = evaluate_module_file(mod_file, repo_root=repo_root)
            self.assertEqual(verdict.verdict, "BLOCK")
            self.assertTrue(verdict.is_blocking)
            self.assertEqual(verdict.classification, "complexity_violation")

            # 2. Staged shift-left check
            ok_shift, violations_shift = analyze_shift_left_file(
                repo_root=repo_root,
                file_rel_path=rel_path,
                diff_scoped=False,
                baseline_modules={},
            )
            self.assertFalse(ok_shift)

            # 3. Scheduled antipattern audit
            run_dir = repo_root / "run_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            res_audit = run_repository_antipattern_audit(
                context=_make_scheduled_context(),
                fixture_root=repo_root,
                run_dir=run_dir,
            )
            self.assertEqual(res_audit.terminal_outcome, "HITL_REQUIRED")
            self.assertEqual(res_audit.findings_count, 1)

            # 4. Wave gate l0 audit
            l0_ok, l0_viols = WaveEvidenceVerifier.audit_l0_line_budgets(repo_root)
            self.assertFalse(l0_ok)

            # 5. Ingestion acceptance line budget
            with self.assertRaises(RuntimeError) as ctx:
                check_line_budgets(repo_root, (rel_path,))
            self.assertIn("violated module size policy", str(ctx.exception))

    def test_06_pipeline_substring_does_not_bypass_eval_module_budget_and_declarative_rejects_functions(self) -> None:
        """Verify _eval_module_budget blocks non-exempted dual_pipeline violations and functions are not declarative."""
        from tools.file_budget_core.policy import is_ast_pure_declarative
        from tools.lint_file_budgets import _eval_module_budget

        # Functions, calls, and comprehensions must not qualify as pure declarative
        for snippet in ("def f(x):\n    return x + 1\n", "X = [i for i in range(10)]\n", "L = lambda x: x\n"):
            self.assertFalse(is_ast_pure_declarative(ast.parse(snippet)))

        # Missing policy file path falls back to defaults without FileNotFoundError
        fallback_policy = load_candidate_policy(Path("/nonexistent/missing_policy.json"))
        self.assertEqual(fallback_policy.clean_ceiling, 400)
        self.assertEqual(fallback_policy.warning_ceiling_standard, 650)

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            mod_file = repo_root / "dual_pipeline" / "l0" / "non_declarative_700.py"
            mod_file.parent.mkdir(parents=True, exist_ok=True)
            # 700 lines of functions (even without if/for) must block in 651-800 range
            funcs = "\n".join([f"def helper_{i}(x: int) -> int:\n    return x + {i}\n" for i in range(240)])
            mod_file.write_text(funcs, encoding="utf-8")
            lines = funcs.count("\n")

            reason, is_grad, amber = _eval_module_budget(
                rel="dual_pipeline/l0/non_declarative_700.py",
                fp=mod_file,
                lines=lines,
                max_lines=400,
                ratchet=True,
                baseline_modules={},
            )
            self.assertIsNotNone(reason)
            self.assertFalse(is_grad)
            self.assertIsNone(amber)


if __name__ == "__main__":
    unittest.main()
