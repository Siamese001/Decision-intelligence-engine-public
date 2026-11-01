"""Governance test verifying dual_pipeline terminology deprecation and unified_pipeline SSOT."""

import unittest
from pathlib import Path


class TestDualPipelineTerminologyDeprecation(unittest.TestCase):
    """Ensure repository governance codifies unified_pipeline as canonical SSOT."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent

    def test_agents_md_contains_invariant_11(self) -> None:
        """AGENTS.md codifies Invariant 11: Unified Pipeline Architecture & Dual-Pipeline Deprecation."""
        agents_md = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("11. **Unified Pipeline Architecture & Dual-Pipeline Deprecation Invariant**:", agents_md)
        self.assertIn("The terminology 'Dual Pipeline' is formally retired", agents_md)
        self.assertIn("The package `unified_pipeline` is the canonical namespace", agents_md)

    def test_unified_agent_architecture_rule_codifies_ssot(self) -> None:
        """unified-agent-architecture.md declares Single Pipeline SSOT."""
        rule_path = self.repo_root / ".agents" / "rules" / "unified-agent-architecture.md"
        content = rule_path.read_text(encoding="utf-8")
        self.assertIn("Single Pipeline SSOT:", content)
        self.assertIn("Unified Agentic Pipeline (`U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS`)", content)
        self.assertIn("`unified_pipeline` is the canonical SSOT namespace", content)

    def test_pyproject_toml_includes_unified_pipeline(self) -> None:
        """pyproject.toml declares unified_pipeline* in package discovery."""
        pyproject_path = self.repo_root / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        self.assertIn('"unified_pipeline*"', content)

    def test_unified_pipeline_package_exists_and_importable(self) -> None:
        """unified_pipeline root package exists with canonical exports."""
        import unified_pipeline as up

        self.assertTrue(hasattr(up, "UnifiedAgenticPipeline"))
        self.assertTrue(hasattr(up, "RawTranscriptPipeline"))
        self.assertTrue(hasattr(up, "DocumentPipeline"))
        self.assertTrue(hasattr(up, "PipelineStage"))


if __name__ == "__main__":
    unittest.main()
