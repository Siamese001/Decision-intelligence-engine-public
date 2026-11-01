"""tests/adversarial/test_live_dead_code_regression_adversarial.py

Adversarial AST and import isolation regression test suite for Wave 3.
Verifies that:
1. Purged Category 2 dead modules cannot be imported and do not exist on disk.
2. AST scan across production codebase (dual_pipeline/, tools/, pipeline_a_intake/, pipeline_b_extraction/)
   guarantees zero imports of deleted modules.
3. Lazy loading in dual_pipeline/live_meeting_recorder_core/__init__.py prevents heavy dependencies
   (copilot, whisper, mlx, etc.) from loading when importing lightweight submodules.
4. Attempting to bypass lazy loader with invalid or purged attribute names raises clean AttributeError.
5. Decommissioned entry points (tombstones) fail closed.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

PURGED_MODULE_NAMES = (
    "dual_pipeline.live_capture_engine",
    "dual_pipeline.live_meeting_recorder_core.parallel_initializer",
    "dual_pipeline.live_meeting_recorder_core.local_ingress",
    "dual_pipeline.live_meeting_recorder_core.timeline_reconciler",
    "dual_pipeline.live_meeting_recorder_core.dynamic_slicer",
    "dual_pipeline.live_meeting_recorder_core.hot_reload",
    "dual_pipeline.live_meeting_recorder_core.context_assembler",
    "dual_pipeline.live_meeting_recorder_core.copilot_graph_context",
    "dual_pipeline.live_meeting_recorder_core.grounding_contract",
    "dual_pipeline.live_meeting_recorder_core.delta_extractor",
    "dual_pipeline.live_meeting_recorder_core.chat_collateral_bridge",
    "dual_pipeline.live_meeting_recorder_core.hitl_chat_confirmation",
    "dual_pipeline.transcript_reconciler",
    "dual_pipeline.audio_merger",
    "dual_pipeline.audio_merger.audio_orchestrator",
    "dual_pipeline.audio.dropout_annotator",
    "dual_pipeline.audio.bluetooth_harness",
    "dual_pipeline.audio.canonical_normalizer",
    "tools.recover_live_session",
)

PURGED_FILENAMES = (
    "dual_pipeline/live_capture_engine.py",
    "dual_pipeline/live_meeting_recorder_core/parallel_initializer.py",
    "dual_pipeline/live_meeting_recorder_core/local_ingress.py",
    "dual_pipeline/live_meeting_recorder_core/timeline_reconciler.py",
    "dual_pipeline/live_meeting_recorder_core/dynamic_slicer.py",
    "dual_pipeline/live_meeting_recorder_core/hot_reload.py",
    "dual_pipeline/live_meeting_recorder_core/context_assembler.py",
    "dual_pipeline/live_meeting_recorder_core/copilot_graph_context.py",
    "dual_pipeline/live_meeting_recorder_core/grounding_contract.py",
    "dual_pipeline/live_meeting_recorder_core/delta_extractor.py",
    "dual_pipeline/live_meeting_recorder_core/chat_collateral_bridge.py",
    "dual_pipeline/live_meeting_recorder_core/hitl_chat_confirmation.py",
    "dual_pipeline/transcript_reconciler.py",
    "dual_pipeline/audio_merger/audio_orchestrator.py",
    "dual_pipeline/audio/dropout_annotator.py",
    "dual_pipeline/audio/bluetooth_harness.py",
    "dual_pipeline/audio/canonical_normalizer.py",
    "tools/recover_live_session.py",
)


class TestLiveDeadCodeRegressionAdversarial(unittest.TestCase):
    """Adversarial security and regression suite enforcing dead-code elimination."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_purged_files_do_not_exist_on_disk(self):
        """Invariant: Zero purged dead-code files remain on disk."""
        extant_files = []
        for rel_path in PURGED_FILENAMES:
            full_path = self.repo_root / rel_path
            if full_path.exists():
                extant_files.append(rel_path)
        self.assertEqual(
            extant_files,
            [],
            f"Purged dead code files still found on disk: {extant_files}",
        )

    def test_purged_modules_fail_to_import(self):
        """Invariant: Direct imports of purged modules raise ModuleNotFoundError."""
        for mod_name in PURGED_MODULE_NAMES:
            with self.subTest(module=mod_name), self.assertRaises((ModuleNotFoundError, ImportError)):
                importlib.import_module(mod_name)

    @staticmethod
    def _find_purged_imports_in_tree(tree: ast.AST, rel_path: Path, purged_leaves: set[str]) -> list[str]:
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for purged in PURGED_MODULE_NAMES:
                        if alias.name == purged or alias.name.startswith(purged + "."):
                            violations.append(f"{rel_path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for purged in PURGED_MODULE_NAMES:
                    if mod == purged or mod.startswith(purged + "."):
                        violations.append(f"{rel_path}:{node.lineno}: from {mod} import ...")
                if mod in ("dual_pipeline", "dual_pipeline.live_meeting_recorder_core", "dual_pipeline.audio"):
                    for alias in node.names:
                        if alias.name in purged_leaves:
                            violations.append(f"{rel_path}:{node.lineno}: from {mod} import {alias.name}")
        return violations

    def test_ast_scan_production_code_zero_purged_imports(self):
        """Invariant: AST scan of production code confirms zero imports of purged modules."""
        scanned_dirs = [
            self.repo_root / "dual_pipeline",
            self.repo_root / "tools",
            self.repo_root / "pipeline_a_intake",
            self.repo_root / "pipeline_b_extraction",
        ]
        purged_leaves = {m.split(".")[-1] for m in PURGED_MODULE_NAMES}
        violations = []

        for sdir in scanned_dirs:
            if not sdir.exists():
                continue
            for root, _, files in os.walk(sdir):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = Path(root) / fname
                    rel_path = fpath.relative_to(self.repo_root)
                    try:
                        tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(rel_path))
                        violations.extend(self._find_purged_imports_in_tree(tree, rel_path, purged_leaves))
                    except SyntaxError:
                        continue

        self.assertEqual(
            violations,
            [],
            "Production codebase contains imports of purged dead-code modules:\n" + "\n".join(violations),
        )

    def test_lazy_core_package_prevents_eager_copilot_load(self):
        """Adversarial check: importing instant_spooler must NOT load copilot from core."""
        code = (
            "import sys\n"
            "from dual_pipeline.live_meeting_recorder_core import instant_spooler\n"
            "copilot_loaded = any('live_meeting_recorder_core.copilot' in k for k in sys.modules)\n"
            "assert not copilot_loaded, f'Copilot leaked into modules: {[k for k in sys.modules if \"copilot\" in k]}'\n"
            "print('SUCCESS_LAZY_ISOLATION')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Lazy import failed: {res.stderr}")
        self.assertIn("SUCCESS_LAZY_ISOLATION", res.stdout)

    def test_lazy_core_package_attribute_error_on_purged_access(self):
        """Invariant: Accessing purged attributes on live_meeting_recorder_core raises AttributeError."""
        from dual_pipeline import live_meeting_recorder_core

        purged_attrs = [
            "parallel_initializer",
            "ParallelRecordingInitializer",
            "local_ingress",
            "timeline_reconciler",
            "TimelineReconciler",
            "dynamic_slicer",
            "hot_reload",
            "context_assembler",
            "copilot_graph_context",
            "grounding_contract",
            "delta_extractor",
            "chat_collateral_bridge",
            "hitl_chat_confirmation",
            "non_existent_random_symbol_xyz",
        ]

        for attr in purged_attrs:
            with self.subTest(attribute=attr), self.assertRaises(AttributeError):
                getattr(live_meeting_recorder_core, attr)

    def test_tombstone_live_transcript_engine_fails_closed(self):
        """Invariant: Attempting to invoke decommissioned live_transcript_engine.py fails closed with exit code 1."""
        script_path = self.repo_root / "dual_pipeline" / "live_transcript_engine.py"
        self.assertTrue(script_path.exists())

        res = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr)


if __name__ == "__main__":
    unittest.main()
