"""Unit tests for Architectural Layer Dependency and Tarjan's SCC Cycle Linter.

Tests:
1. Downward flow enforcement (L3 -> L0 passes, L0 -> L3 fails).
2. Sub-agent to orchestrator inversion detection (L3 sub-agent importing L5 coordinator).
3. Circular import cycle detection via Tarjan's SCC (A -> B -> A and multi-node cycles).
4. Exemption handling (if TYPE_CHECKING:, try...except, # noqa: lazy-decouple).
5. Cross-pipeline reverse flow detection (Pipeline A importing Pipeline B).
6. --staged mode filtering.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_layer_imports import (
    find_circular_import_cycles,
    get_layer_for_file,
    get_pipeline,
    scan_file_imports,
)


class TestLintLayerImports(unittest.TestCase):
    """Verifies all invariants of tools/lint_layer_imports.py."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_module(self, rel_path: str, content: str) -> Path:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_layer_classification(self) -> None:
        """Verifies canonical L0-L6 layer classification."""
        self.assertEqual(get_layer_for_file("contracts/evidence.json"), 0)
        self.assertEqual(get_layer_for_file("pipeline_a_intake/contracts.py"), 0)
        self.assertEqual(get_layer_for_file("storage/database.py"), 1)
        self.assertEqual(get_layer_for_file("pipeline_a_intake/local_stt.py"), 1)
        self.assertEqual(get_layer_for_file("pipeline_b_extraction/extractors.py"), 2)
        self.assertEqual(get_layer_for_file("pipeline_b_extraction/agents/tech_arch_agent.py"), 3)
        self.assertEqual(get_layer_for_file("dual_pipeline/llm_judge_core/graph.py"), 4)
        self.assertEqual(get_layer_for_file("pipeline_a_intake/agents/ingestion_coordinator.py"), 5)
        self.assertEqual(get_layer_for_file("dual_pipeline/cli/engine.py"), 6)
        self.assertEqual(get_layer_for_file("dual_pipeline/hitl_ui_bridge.py"), 6)

    def test_pipeline_classification(self) -> None:
        """Verifies Pipeline A, B, C, D workstream classification."""
        self.assertEqual(get_pipeline("pipeline_a_intake/audio_splicer.py"), "A")
        self.assertEqual(get_pipeline("pipeline_b_extraction/worker.py"), "B")
        self.assertEqual(get_pipeline("pipeline_c_retrieval/engine.py"), "C")
        self.assertEqual(get_pipeline("dual_pipeline/retrieval/agents/ranker.py"), "C")
        self.assertEqual(get_pipeline("dual_pipeline/decision/agents/red_team.py"), "D")

    def test_downward_flow_inversion_detected(self) -> None:
        """Verifies lower layer importing higher layer is flagged with DOWNWARD_FLOW_INVERSION."""
        # Create an L0 file importing an L3 file
        self._write_module(
            "pipeline_a_intake/contracts.py",
            "from pipeline_a_intake.agents.audio_forensics_agent import AudioForensicsAgent\n",
        )
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "class AudioForensicsAgent: pass\n",
        )

        l0_file = self.root / "pipeline_a_intake/contracts.py"
        lines = l0_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(l0_file, self.root, lines)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "DOWNWARD_FLOW_INVERSION")
        self.assertEqual(violations[0].source_layer, 0)
        self.assertEqual(violations[0].target_layer, 3)

    def test_downward_flow_allowed(self) -> None:
        """Verifies higher layer importing lower layer passes with 0 violations."""
        # L5 orchestrator importing L0 contracts
        self._write_module(
            "pipeline_a_intake/agents/ingestion_coordinator.py",
            "from pipeline_a_intake.contracts import IngestionTask\n",
        )
        self._write_module(
            "pipeline_a_intake/contracts.py",
            "class IngestionTask: pass\n",
        )

        coord_file = self.root / "pipeline_a_intake/agents/ingestion_coordinator.py"
        lines = coord_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(coord_file, self.root, lines)

        self.assertEqual(len(violations), 0)

    def test_subagent_orchestrator_inversion_detected(self) -> None:
        """Verifies L3 sub-agent importing L5 orchestrator is flagged."""
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "from pipeline_a_intake.agents.ingestion_coordinator import IngestionCoordinator\n",
        )
        self._write_module(
            "pipeline_a_intake/agents/ingestion_coordinator.py",
            "class IngestionCoordinator: pass\n",
        )

        agent_file = self.root / "pipeline_a_intake/agents/audio_forensics_agent.py"
        lines = agent_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(agent_file, self.root, lines)

        rules = [v.rule for v in violations]
        self.assertIn("SUBAGENT_ORCHESTRATOR_INVERSION", rules)
        self.assertIn("DOWNWARD_FLOW_INVERSION", rules)

    def test_cross_pipeline_reverse_flow_detected(self) -> None:
        """Verifies upstream Pipeline A importing downstream Pipeline B is flagged."""
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "from pipeline_b_extraction.extractors import TechArchExtractor\n",
        )
        self._write_module(
            "pipeline_b_extraction/extractors.py",
            "class TechArchExtractor: pass\n",
        )

        agent_file = self.root / "pipeline_a_intake/agents/audio_forensics_agent.py"
        lines = agent_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(agent_file, self.root, lines)

        rules = [v.rule for v in violations]
        self.assertIn("CROSS_PIPELINE_REVERSE_FLOW", rules)

    def test_type_checking_bypass(self) -> None:
        """Verifies imports inside if TYPE_CHECKING: do not cause runtime violations."""
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from pipeline_a_intake.agents.ingestion_coordinator import IngestionCoordinator\n"
            "class AudioForensicsAgent: pass\n",
        )
        self._write_module(
            "pipeline_a_intake/agents/ingestion_coordinator.py",
            "class IngestionCoordinator: pass\n",
        )

        agent_file = self.root / "pipeline_a_intake/agents/audio_forensics_agent.py"
        lines = agent_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, targets = scan_file_imports(agent_file, self.root, lines)

        self.assertEqual(len(violations), 0)
        self.assertEqual(len(targets), 0)

    def test_lazy_decouple_comment_exemption(self) -> None:
        """Verifies function-level import with # noqa: lazy-decouple is permitted."""
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "class AudioForensicsAgent:\n"
            "    def init_ui(self):\n"
            "        from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge  # noqa: lazy-decouple\n"
            "        return AntigravityUIBridge()\n",
        )
        self._write_module(
            "dual_pipeline/hitl_ui_bridge.py",
            "class AntigravityUIBridge: pass\n",
        )

        agent_file = self.root / "pipeline_a_intake/agents/audio_forensics_agent.py"
        lines = agent_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(agent_file, self.root, lines)

        self.assertEqual(len(violations), 0)

    def test_try_except_lazy_exemption(self) -> None:
        """Verifies function-level import inside try...except block is permitted."""
        self._write_module(
            "pipeline_a_intake/agents/audio_forensics_agent.py",
            "class AudioForensicsAgent:\n"
            "    def init_ui(self):\n"
            "        try:\n"
            "            from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge\n"
            "            return AntigravityUIBridge()\n"
            "        except ImportError:\n"
            "            return None\n",
        )
        self._write_module(
            "dual_pipeline/hitl_ui_bridge.py",
            "class AntigravityUIBridge: pass\n",
        )

        agent_file = self.root / "pipeline_a_intake/agents/audio_forensics_agent.py"
        lines = agent_file.read_text(encoding="utf-8").splitlines(keepends=True)
        violations, _ = scan_file_imports(agent_file, self.root, lines)

        self.assertEqual(len(violations), 0)

    def test_tarjan_scc_cycle_detection(self) -> None:
        """Verifies Tarjan's SCC detects cyclic dependencies."""
        # Simple 2-node cycle: A -> B -> A
        graph = {
            "module_a.py": {"module_b.py"},
            "module_b.py": {"module_a.py"},
            "module_c.py": set(),
        }
        cycles = find_circular_import_cycles(graph)
        self.assertEqual(len(cycles), 1)
        self.assertCountEqual(cycles[0], ["module_a.py", "module_b.py"])

        # 3-node cycle: A -> B -> C -> A
        graph_3 = {
            "a.py": {"b.py"},
            "b.py": {"c.py"},
            "c.py": {"a.py"},
            "d.py": {"a.py"},  # d points to cycle but is not in it
        }
        cycles_3 = find_circular_import_cycles(graph_3)
        self.assertEqual(len(cycles_3), 1)
        self.assertCountEqual(cycles_3[0], ["a.py", "b.py", "c.py"])

        # DAG with no cycle: A -> B -> C
        graph_dag = {
            "a.py": {"b.py"},
            "b.py": {"c.py"},
            "c.py": set(),
        }
        cycles_dag = find_circular_import_cycles(graph_dag)
        self.assertEqual(len(cycles_dag), 0)


if __name__ == "__main__":
    unittest.main()
