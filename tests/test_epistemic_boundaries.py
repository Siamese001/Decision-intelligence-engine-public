import unittest
from pathlib import Path

from dual_pipeline.completion import IngestionCompletionGuard
from tools.lint_epistemic_boundaries import lint_epistemic_boundaries

ROOT = Path(__file__).resolve().parent.parent


class TestEpistemicBoundaries(unittest.TestCase):
    def test_active_runtime_lint_passes(self):
        report = lint_epistemic_boundaries(
            runtime_root=ROOT / ".local" / "dual_pipeline",
            repo_root=ROOT,
            check_intake_packages=True,
            strict_legacy=False,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["error_count"], 0)

    def test_package_binding_blocks_card_markdown(self):
        card_file = ROOT / "cards" / "NEE_01_Executive_Account_Thesis.md"
        self.assertTrue(card_file.is_file())
        with self.assertRaises(ValueError) as ctx:
            IngestionCompletionGuard._package_binding(card_file)
        self.assertIn("ERR_EPISTEMIC_CIRCULARITY_BLOCKED", str(ctx.exception))

    def test_strict_legacy_surfaces_v17_warnings_as_errors(self):
        report = lint_epistemic_boundaries(
            runtime_root=ROOT / ".local" / "dual_pipeline",
            repo_root=ROOT,
            strict_legacy=True,
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
