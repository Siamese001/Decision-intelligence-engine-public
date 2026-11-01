"""Unit tests for dual_pipeline.internal_operational.

Verifies InternalOperationalManager initialization, input validation, and directive checks.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.internal_operational import (
    InternalOperationalManager,
    _verify_directive_package_bytes,
)


class TestInternalOperationalUnit(unittest.TestCase):
    def test_verify_directive_package_bytes_missing_file_raises(self) -> None:
        directive = {
            "packages": [
                {
                    "path": "/nonexistent/path/package.tar.gz",
                    "sha256": "abc",
                    "bytes": 123,
                }
            ]
        }
        with self.assertRaises(FileNotFoundError):
            _verify_directive_package_bytes(directive)

    def test_verify_directive_package_bytes_tampered_bytes_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"real content")
            f_path = f.name

        try:
            directive = {
                "packages": [
                    {
                        "path": f_path,
                        "sha256": "fake_sha256_hash",
                        "bytes": len(b"real content"),
                    }
                ]
            }
            with self.assertRaises(RuntimeError):
                _verify_directive_package_bytes(directive)
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_manager_empty_reviewer_or_rationale_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            seed_graph = runtime_root / "seed.sqlite"
            seed_graph.touch()

            manager = InternalOperationalManager(runtime_root, seed_graph)
            backend_mock = MagicMock()

            with self.assertRaises(ValueError) as ctx:
                manager.qualify(
                    directive_path=runtime_root / "directive.json",
                    source_evaluation_path=runtime_root / "eval.json",
                    suite_path=runtime_root / "suite.json",
                    backend=backend_mock,
                    reviewer="",
                    rationale="Some valid rationale",
                )
            self.assertIn("reviewer and rationale are required", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
