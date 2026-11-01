"""Unit and adversarial tests for three-way SSOT value parity (Wave 1)."""
from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

from tools.audit_governance_harmonization_core.common import HarmonizationReport
from tools.audit_governance_harmonization_core.structural_scanners import (
    _resolve_dotted,
    _scan_unregistered_inv_tokens,
    _validate_three_way_value_parity,
    scan_policy_registry_sync,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSSOTThreeWayParity(unittest.TestCase):
    """Verify 3-way parity across policy_registry.yaml, parameters.json, and pyproject.toml."""

    def test_all_bound_policies_match_runtime_parameters_and_pyproject(self) -> None:
        reg_data = yaml.safe_load((REPO_ROOT / "governance" / "policy_registry.yaml").read_text(encoding="utf-8"))
        policies = reg_data.get("policies", [])
        params = json.loads((REPO_ROOT / "config" / "runtime" / "parameters.json").read_text(encoding="utf-8"))["parameters"]
        pyproj = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        checked_runtime = 0
        checked_pyproject = 0
        for pol in policies:
            pid = pol["id"]
            expected = pol["threshold"]
            rp = pol.get("runtime_parameter")
            if rp and "->" in str(rp):
                dotted = str(rp).split("->", 1)[1].strip()
                found, actual = _resolve_dotted(params, dotted)
                self.assertTrue(found, f"{pid}: missing runtime_parameter '{dotted}' in parameters.json")
                self.assertAlmostEqual(float(expected), float(actual), places=6, msg=f"{pid}: {expected} != {actual}")
                checked_runtime += 1

            pb = pol.get("pyproject_binding")
            if pb:
                dotted = str(pb).strip()
                found, actual = _resolve_dotted(pyproj, dotted)
                self.assertTrue(found, f"{pid}: missing pyproject_binding '{dotted}' in pyproject.toml")
                self.assertAlmostEqual(float(expected), float(actual), places=6, msg=f"{pid}: {expected} != {actual}")
                checked_pyproject += 1

        self.assertGreaterEqual(checked_runtime, 11)
        self.assertGreaterEqual(checked_pyproject, 8)

    def test_scan_policy_registry_sync_clean_on_repo(self) -> None:
        report = HarmonizationReport(scan_timestamp="2026-10-30T10:30:00Z", repo_root=str(REPO_ROOT))
        scan_policy_registry_sync([], report, repo_root=REPO_ROOT)
        criticals = [f for f in report.findings if f.severity == "CRITICAL"]
        self.assertEqual(criticals, [])

    def test_adversarial_runtime_parameter_drift_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            root = Path(tmp)
            (root / "config" / "runtime").mkdir(parents=True)
            (root / "config" / "runtime" / "parameters.json").write_text(
                json.dumps({"parameters": {"hitl": {"ambiguity_margin_threshold": 0.20}}}),
                encoding="utf-8",
            )
            (root / "pyproject.toml").write_text(
                "[tool.nee.budgets]\nhitl_ambiguity_margin_threshold = 0.20\n",
                encoding="utf-8",
            )
            drifted_policies = {
                "INV-HITL-AMBIGUITY-MARGIN": {
                    "id": "INV-HITL-AMBIGUITY-MARGIN",
                    "threshold": 0.30,
                    "runtime_parameter": "config/runtime/parameters.json -> hitl.ambiguity_margin_threshold",
                    "pyproject_binding": "tool.nee.budgets.hitl_ambiguity_margin_threshold",
                }
            }
            report = HarmonizationReport(scan_timestamp="2026-10-30T10:30:00Z", repo_root=str(root))
            _validate_three_way_value_parity(drifted_policies, report, repo_root=root)
            titles = [f.title for f in report.findings if f.severity == "CRITICAL"]
            self.assertIn("Policy Registry vs Runtime Parameter Drift: INV-HITL-AMBIGUITY-MARGIN", titles)
            self.assertIn("Policy Registry vs pyproject.toml Drift: INV-HITL-AMBIGUITY-MARGIN", titles)

    def test_adversarial_unregistered_inv_token_in_hooks_or_prompts_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            root = Path(tmp)
            hooks_file = root / ".agents" / "hooks.json"
            hooks_file.parent.mkdir(parents=True)
            hooks_file.write_text('{"instructions": "Enforce INV-FAKE-UNREGISTERED-999 limit"}', encoding="utf-8")

            report = HarmonizationReport(scan_timestamp="2026-10-30T10:30:00Z", repo_root=str(root))
            _scan_unregistered_inv_tokens([hooks_file], {"INV-OUTPUT-MAX-WORDS": {}}, report)
            titles = [f.title for f in report.findings if f.severity == "CRITICAL"]
            self.assertIn("Unregistered Policy Invariant: INV-FAKE-UNREGISTERED-999", titles)


if __name__ == "__main__":
    unittest.main()
