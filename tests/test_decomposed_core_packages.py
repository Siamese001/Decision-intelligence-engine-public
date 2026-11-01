"""Invariant and contract parity test suite for Wave 2 decomposed core packages."""
from __future__ import annotations

import importlib
import unittest

from dual_pipeline.core import ROOT

DECOMPOSED_PAIRS = [
    ("dual_pipeline.l1_control", "dual_pipeline.l1_control_core", "dual_pipeline/l1_control.py", "dual_pipeline/l1_control_core"),
    ("dual_pipeline.internal_operational", "dual_pipeline.internal_operational_core", "dual_pipeline/internal_operational.py", "dual_pipeline/internal_operational_core"),
    ("dual_pipeline.strategic_waves", "dual_pipeline.strategic_waves_core", "dual_pipeline/strategic_waves.py", "dual_pipeline/strategic_waves_core"),
    ("dual_pipeline.graph_scout", "dual_pipeline.graph_scout_core", "dual_pipeline/graph_scout.py", "dual_pipeline/graph_scout_core"),
    ("dual_pipeline.audio_ingestion", "dual_pipeline.audio_ingestion_core", "dual_pipeline/audio_ingestion.py", "dual_pipeline/audio_ingestion_core"),
    ("dual_pipeline.runtime_decision_adapter", "dual_pipeline.decision_adapter_core", "dual_pipeline/runtime_decision_adapter.py", "dual_pipeline/decision_adapter_core"),
    ("dual_pipeline.x1_quality", "dual_pipeline.x1_quality_core", "dual_pipeline/x1_quality.py", "dual_pipeline/x1_quality_core"),
    ("dual_pipeline.daily_intelligence", "dual_pipeline.daily_intelligence_core", "dual_pipeline/daily_intelligence.py", "dual_pipeline/daily_intelligence_core"),
]


class TestDecomposedCorePackages(unittest.TestCase):
    """Asserts that all decomposed packages adhere to size ceilings and export parity."""

    def test_all_facades_reexport_core_symbols(self) -> None:
        """Every facade must export exactly the symbols defined in its core package."""
        for facade_mod_name, core_mod_name, _, _ in DECOMPOSED_PAIRS:
            with self.subTest(facade=facade_mod_name, core=core_mod_name):
                facade_mod = importlib.import_module(facade_mod_name)
                core_mod = importlib.import_module(core_mod_name)

                facade_all = getattr(facade_mod, "__all__", None)
                core_all = getattr(core_mod, "__all__", None)

                self.assertIsNotNone(facade_all, f"{facade_mod_name} must declare __all__")
                self.assertIsNotNone(core_all, f"{core_mod_name} must declare __all__")

                self.assertEqual(
                    sorted(facade_all),
                    sorted(core_all),
                    f"Symbol mismatch between {facade_mod_name} and {core_mod_name}",
                )

                for symbol in facade_all:
                    facade_obj = getattr(facade_mod, symbol)
                    core_obj = getattr(core_mod, symbol)
                    self.assertIs(
                        facade_obj,
                        core_obj,
                        f"Object identity mismatch for symbol '{symbol}' in {facade_mod_name}",
                    )

    def test_facades_under_70_lines(self) -> None:
        """Every backward-compatibility facade must be under 70 lines."""
        for _, _, facade_path_str, _ in DECOMPOSED_PAIRS:
            facade_path = ROOT / facade_path_str
            with open(facade_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertLessEqual(
                len(lines),
                70,
                f"Facade {facade_path_str} exceeds 70 lines (has {len(lines)})",
            )

    def test_submodules_under_300_lines(self) -> None:
        """Every sub-module in each decomposed core package must be under 300 lines."""
        for _, _, _, core_dir_str in DECOMPOSED_PAIRS:
            core_dir = ROOT / core_dir_str
            self.assertTrue(core_dir.is_dir(), f"{core_dir_str} is not a directory")
            for py_file in core_dir.glob("*.py"):
                with open(py_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                self.assertLessEqual(
                    len(lines),
                    300,
                    f"Sub-module {py_file.relative_to(ROOT)} exceeds 300 lines (has {len(lines)})",
                )

    def test_meeting_paths_under_400_lines(self) -> None:
        """Remediated meeting_paths.py must be under 400 lines."""
        meeting_paths = ROOT / "dual_pipeline" / "meeting_paths.py"
        with open(meeting_paths, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertLessEqual(
            len(lines),
            400,
            f"dual_pipeline/meeting_paths.py exceeds 400 lines (has {len(lines)})",
        )


if __name__ == "__main__":
    unittest.main()
