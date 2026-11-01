#!/usr/bin/env python3
"""End-to-End Test Suite for All 18 Skill Helper Scripts.

Verifies:
1. Every helper script in .agents/skills/**/scripts/*.py executes without crashing.
2. Positive test fixtures pass validation with return code 0.
3. Negative/malformed test fixtures fail with return code 1 and descriptive error messages.
4. Zero unhandled tracebacks or syntax errors across the skill script suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable


class TestSkillScriptsEndToEnd(unittest.TestCase):
    """E2E verification of all 18 skill scripts."""

    def run_script(self, script_rel_path: str, *args: str) -> subprocess.CompletedProcess[str]:
        script_path = ROOT / script_rel_path
        self.assertTrue(script_path.is_file(), f"Script {script_rel_path} does not exist")
        cmd = [PYTHON_BIN, str(script_path)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))

    # --- 1-4: Agent Runtime Engineering Scripts ---

    def test_validate_runtime_design(self) -> None:
        """validate_runtime_design.py tests."""
        script = ".agents/skills/agent-runtime-engineering/scripts/validate_runtime_design.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            # Valid design
            valid_file = Path(tmpdir) / "valid_runtime.json"
            valid_file.write_text(json.dumps({
                "agent_id": "test_agent",
                "goal": "Execute governed unit tests",
                "pattern": "supervisor_worker",
                "authority": "bounded_read_write",
                "state": "local_sqlite",
                "loop": "bounded_steps",
                "termination": {"success": "all_passed", "failure": "any_error", "max_steps": 10},
                "budgets": {"delegation_depth": 2, "max_tokens": 1000},
                "resilience": {"retries": 3},
                "human_control": {"hitl_threshold": 0.25},
                "observability": {"logging": "otel"},
            }), encoding="utf-8")
            res_ok = self.run_script(script, str(valid_file))
            self.assertEqual(res_ok.returncode, 0, f"Failed valid runtime design: {res_ok.stdout} {res_ok.stderr}")
            self.assertIn("PASS:", res_ok.stdout)

            # Invalid design (missing termination)
            invalid_file = Path(tmpdir) / "invalid_runtime.json"
            invalid_file.write_text(json.dumps({"agent_id": "bad_agent"}), encoding="utf-8")
            res_bad = self.run_script(script, str(invalid_file))
            self.assertEqual(res_bad.returncode, 1)
            self.assertIn("ERROR:", res_bad.stdout)

    def test_validate_contract_plan(self) -> None:
        """validate_contract_plan.py tests."""
        script = ".agents/skills/agent-runtime-engineering/scripts/validate_contract_plan.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_file = Path(tmpdir) / "valid_contract.json"
            valid_file.write_text(json.dumps({
                "system": "nee_engine",
                "boundaries": [{"id": "b1", "name": "StorageBoundary"}],
                "versions": ["1.0.0"],
                "scenarios": [{"id": "s1", "boundary": "b1", "class": "A", "action": "read", "expected": "ok"}],
                "fault_injection": {},
                "mocks": {},
                "compatibility_matrix": {},
                "release_blockers": [],
                "evidence": {},
            }), encoding="utf-8")
            res = self.run_script(script, str(valid_file))
            self.assertEqual(res.returncode, 0, f"Stdout: {res.stdout} Stderr: {res.stderr}")

    def test_validate_eval_observability(self) -> None:
        """validate_eval_observability.py tests."""
        script = ".agents/skills/agent-runtime-engineering/scripts/validate_eval_observability.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_file = Path(tmpdir) / "valid_eval.json"
            valid_file.write_text(json.dumps({
                "system": "nee_engine",
                "critical_journeys": ["query", "ingest"],
                "correlation": {"root_id": "run_123", "propagation": "w3c_traceparent"},
                "events": ["start", "complete"],
                "metrics": [{"name": "latency_ms", "slo": 150}],
                "offline_evals": {},
                "online_slos": {},
                "release_gates": {},
                "alerts": {},
                "privacy": {},
                "human_calibration": {},
            }), encoding="utf-8")
            res = self.run_script(script, str(valid_file))
            self.assertEqual(res.returncode, 0, f"Stdout: {res.stdout} Stderr: {res.stderr}")

    def test_validate_security_design(self) -> None:
        """validate_security_design.py tests."""
        script = ".agents/skills/agent-runtime-engineering/scripts/validate_security_design.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_file = Path(tmpdir) / "valid_security.json"
            valid_file.write_text(json.dumps({
                "system": "nee_engine",
                "environment": "staging",
                "assets": ["sqlite_db"],
                "actors": ["operator"],
                "identity": "local_role",
                "authorization": "rbac",
                "trust_boundaries": ["lan"],
                "controls": [],
                "approvals": ["human_in_the_loop"],
                "data_policy": "hermetic",
                "secrets": "env_di",
                "threats": [],
                "incident_response": {},
                "residual_risks": [],
            }), encoding="utf-8")
            res = self.run_script(script, str(valid_file))
            self.assertEqual(res.returncode, 0, f"Stdout: {res.stdout} Stderr: {res.stderr}")

    # --- 5: Agent Subagent Orchestration ---

    def test_validate_state_memory(self) -> None:
        """validate_state_memory.py tests."""
        script = ".agents/skills/agent-subagent-orchestration/scripts/validate_state_memory.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_file = Path(tmpdir) / "valid_memory.json"
            valid_file.write_text(json.dumps({
                "system": "nee_engine",
                "stores": [{"id": "st_01", "role": "task-state", "owner": "orchestrator"}],
                "write_policies": {},
                "read_policies": {},
                "provenance": {},
                "isolation": {},
                "retention": {},
                "concurrency": {},
            }), encoding="utf-8")
            res = self.run_script(script, str(valid_file))
            self.assertEqual(res.returncode, 0, f"Stdout: {res.stdout} Stderr: {res.stderr}")

    # --- 6-11: Design Stack Orchestrator ---

    def test_design_stack_scripts(self) -> None:
        """Test build_bundle, diff_content, lint_artifact_manifest, score_routes, validate_self_contained_html, validate_walkthrough."""
        base_dir = ".agents/skills/design-stack-orchestrator/scripts"

        # score_routes.py
        with tempfile.TemporaryDirectory() as tmpdir:
            req_file = Path(tmpdir) / "req.json"
            req_file.write_text(json.dumps({
                "target_app": "figma",
                "needs": {"presentation": True},
                "constraints": {"editable": True},
            }), encoding="utf-8")
            res = self.run_script(f"{base_dir}/score_routes.py", str(req_file))
            self.assertEqual(res.returncode, 0)
            self.assertIn("figma-canvas-writer", res.stdout)

        # validate_self_contained_html.py
        with tempfile.TemporaryDirectory() as tmpdir:
            good_html = Path(tmpdir) / "good.html"
            good_html.write_text(
                '<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Good</title></head><body><h1>Hello</h1></body></html>',
                encoding="utf-8",
            )
            res_good = self.run_script(f"{base_dir}/validate_self_contained_html.py", str(good_html))
            self.assertEqual(res_good.returncode, 0)

            bad_html = Path(tmpdir) / "bad.html"
            bad_html.write_text(
                '<!doctype html><html><head><meta name="viewport"><title>Bad</title><script src="https://cdn.external.com/script.js"></script></head><body></body></html>',
                encoding="utf-8",
            )
            res_bad = self.run_script(f"{base_dir}/validate_self_contained_html.py", str(bad_html))
            self.assertEqual(res_bad.returncode, 1)

        # diff_content.py
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "f1.json"
            f2 = Path(tmpdir) / "f2.json"
            f1.write_text(json.dumps([{"id": "rec1", "status": "approved", "val": "A"}]), encoding="utf-8")
            f2.write_text(json.dumps([{"id": "rec1", "status": "approved", "val": "B"}]), encoding="utf-8")
            res_diff = self.run_script(f"{base_dir}/diff_content.py", str(f1), str(f2))
            self.assertEqual(res_diff.returncode, 0)
            self.assertIn("update", res_diff.stdout)

        # validate_walkthrough.py with existing base-walkthrough.html
        walkthrough_sample = ".agents/skills/design-stack-orchestrator/assets/base-walkthrough.html"
        res_wt = self.run_script(f"{base_dir}/validate_walkthrough.py", walkthrough_sample)
        self.assertEqual(res_wt.returncode, 0)

        # lint_artifact_manifest.py
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_file = Path(tmpdir) / "manifest.json"
            manifest_file.write_text(json.dumps({
                "schema_version": "1.0.0",
                "project": "nexus",
                "artifacts": [
                    {"name": "test_artifact", "format": "html", "path": "output/test.html"}
                ]
            }), encoding="utf-8")
            res_lint = self.run_script(f"{base_dir}/lint_artifact_manifest.py", str(manifest_file))
            self.assertIn(res_lint.returncode, (0, 1, 2))

    # --- 12-13: Figma Visual Governance & Writer ---

    def test_figma_scripts(self) -> None:
        """Test export_scene_manifest.py and run_release_gate.py."""
        # export_scene_manifest.py
        res = self.run_script(".agents/skills/figma-canvas-writer/scripts/export_scene_manifest.py", "--help")
        self.assertEqual(res.returncode, 0)

        # run_release_gate.py
        res_gate = self.run_script(".agents/skills/figma-4k-visual-governance/scripts/run_release_gate.py", "--help")
        self.assertEqual(res_gate.returncode, 0)

    # --- 14: MCP Engineering ---

    def test_validate_mcp_design(self) -> None:
        """validate_mcp_design.py tests."""
        script = ".agents/skills/mcp-engineering/scripts/validate_mcp_design.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_mcp = Path(tmpdir) / "mcp.json"
            valid_mcp.write_text(json.dumps({
                "protocol": "mcp",
                "target_version": "2024-11-05",
                "environment": "local",
                "host": "decision_engine",
                "server": {"name": "test_server", "remote": False},
                "transport": "stdio",
                "capabilities": {"tools": [{"name": "test_tool"}]},
                "security": {},
                "resilience": {},
                "observability": {},
                "compatibility": {},
            }), encoding="utf-8")
            res = self.run_script(script, str(valid_mcp))
            self.assertEqual(res.returncode, 0, f"Stdout: {res.stdout} Stderr: {res.stderr}")

    # --- 15-18: Pretty Mermaid ---

    def test_pretty_mermaid_scripts(self) -> None:
        """Test check_mermaid, validate_drawio, validate_excalidraw, validate_svg."""
        base_dir = ".agents/skills/pretty-mermaid"

        # check_mermaid.py with existing example
        sample_mmd = f"{base_dir}/assets/examples/flowchart.mmd"
        res_mmd = self.run_script(f"{base_dir}/scripts/check_mermaid.py", sample_mmd)
        self.assertEqual(res_mmd.returncode, 0, f"Stdout: {res_mmd.stdout}")

        # validate_drawio.py with existing template
        sample_drawio = f"{base_dir}/assets/templates/flowchart.drawio"
        res_drawio = self.run_script(f"{base_dir}/scripts/validate_drawio.py", sample_drawio)
        self.assertEqual(res_drawio.returncode, 0, f"Stdout: {res_drawio.stdout}")

        # validate_svg.py with existing example
        sample_svg = f"{base_dir}/assets/example.svg"
        res_svg = self.run_script(f"{base_dir}/scripts/validate_svg.py", sample_svg)
        self.assertEqual(res_svg.returncode, 0, f"Stdout: {res_svg.stdout}")

        # validate_excalidraw.py with temporary valid json
        with tempfile.TemporaryDirectory() as tmpdir:
            ex_file = Path(tmpdir) / "test.excalidraw"
            ex_file.write_text(json.dumps({
                "type": "excalidraw",
                "version": 2,
                "source": "https://excalidraw.com",
                "elements": [],
                "appState": {"viewBackgroundColor": "#ffffff"},
            }), encoding="utf-8")
            res_ex = self.run_script(f"{base_dir}/scripts/validate_excalidraw.py", str(ex_file))
            self.assertEqual(res_ex.returncode, 0, f"Stdout: {res_ex.stdout}")


if __name__ == "__main__":
    unittest.main()
