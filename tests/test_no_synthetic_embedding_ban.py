from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.validate_no_synthetic_embeddings import _resolve_ripgrep, scan


class SyntheticEmbeddingBanTests(unittest.TestCase):
    def test_clean_scope_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ordinary.txt").write_text("BGE-M3 only", encoding="utf-8")
            self.assertEqual("PASS", scan(root)["status"])

    def test_retired_identifier_fails_with_exact_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = "deterministic" + "-test"
            target = root / "runtime" / "active_pair.json"
            target.parent.mkdir()
            target.write_text(retired, encoding="utf-8")
            result = scan(root)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(
                [str(Path("runtime/active_pair.json"))],
                result["matches"][retired],
            )

    def test_excluded_virtual_environment_does_not_mask_workspace_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = "deterministic" + "-test"
            ignored = root / ".venv" / "package.txt"
            ignored.parent.mkdir()
            ignored.write_text(retired, encoding="utf-8")
            self.assertEqual("PASS", scan(root)["status"])

    def test_current_local_runtime_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = "deterministic" + "-test"
            target = root / ".local" / "runtime" / "active_pair.json"
            target.parent.mkdir(parents=True)
            target.write_text(retired, encoding="utf-8")
            result = scan(root)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(
                [str(Path(".local/runtime/active_pair.json"))],
                result["matches"][retired],
            )

    def test_current_local_binary_cache_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = "deterministic" + "-test"
            target = root / ".local" / "runtime" / "cache" / "semantic_cache.sqlite"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"\x00cache-binding=" + retired.encode("utf-8"))
            result = scan(root)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(
                [str(Path(".local/runtime/cache/semantic_cache.sqlite"))],
                result["matches"][retired],
            )

    def test_registered_nested_worktree_is_excluded_from_current_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = "deterministic" + "-test"
            worktree = root / ".local" / "worktrees" / "other-checkout"
            target = worktree / "historical.txt"
            target.parent.mkdir(parents=True)
            target.write_text(retired, encoding="utf-8")
            with mock.patch(
                "tools.validate_no_synthetic_embeddings._registered_nested_worktree_roots",
                return_value=(worktree.resolve(),),
            ):
                result = scan(root)
            self.assertEqual("PASS", result["status"])
            self.assertEqual([str(Path(".local/worktrees/other-checkout"))], result["excluded_registered_worktrees"])

    def test_resolve_ripgrep_finds_executable(self) -> None:
        resolved = _resolve_ripgrep()
        self.assertTrue(Path(resolved).is_file() or resolved == "rg")

    def test_resolve_ripgrep_respects_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_rg = Path(directory) / "fake_rg.exe"
            fake_rg.write_text("", encoding="utf-8")
            with mock.patch.dict("os.environ", {"RIPGREP_PATH": str(fake_rg)}):
                self.assertEqual(str(fake_rg), _resolve_ripgrep())

    def test_resolve_ripgrep_fallback_to_bare_command(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("pathlib.Path.is_file", return_value=False):
            self.assertEqual("rg", _resolve_ripgrep())


if __name__ == "__main__":
    unittest.main(verbosity=2)
