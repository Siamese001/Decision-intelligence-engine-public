"""Unit tests for automatic pinned BGE-M3 model path and active pair resolution.

Verifies:
1. Default CLI Namespace auto-resolves to repository pinned model path, manifest, and revision.
2. Active pair dev tier promotes embedding_backend to bge-m3-dev and allow_dev_bge_m3 to True.
3. Explicit user-provided arguments are strictly preserved without being overwritten.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.model_resolution import auto_resolve_backend_config


class TestQueryModelAutoResolution(unittest.TestCase):
    """Test suite for automatic model configuration resolution."""

    def test_default_args_resolve_to_pinned_repository_assets(self) -> None:
        args = argparse.Namespace(
            runtime_root=ROOT / ".local" / "dual_pipeline",
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3",
            allow_dev_bge_m3=False,
        )

        auto_resolve_backend_config(args)

        # Verified pinned revision from bge-m3-model.json / active_pair.json
        expected_revision = "5617a9f61b028005a4858fdac845db406aefb181"
        self.assertEqual(args.model_revision, expected_revision)

        expected_model_dir = ROOT / "models" / "bge-m3" / expected_revision
        if expected_model_dir.is_dir():
            self.assertEqual(args.model_path, str(expected_model_dir))

        expected_manifest = (
            ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{expected_revision}.json"
        )
        if expected_manifest.is_file():
            self.assertEqual(args.model_manifest, str(expected_manifest))

        # Active pair is dev tier; must resolve to bge-m3-dev and allow_dev_bge_m3=True
        self.assertEqual(args.embedding_backend, "bge-m3-dev")
        self.assertTrue(args.allow_dev_bge_m3)

    def test_explicit_arguments_are_not_overwritten(self) -> None:
        args = argparse.Namespace(
            runtime_root=ROOT / ".local" / "dual_pipeline",
            model_path="/custom/path/to/model",
            model_manifest="/custom/manifest.json",
            model_revision="custom_rev_123",
            embedding_backend="bge-m3-internal",
            allow_dev_bge_m3=False,
            allow_internal_operational=True,
        )

        auto_resolve_backend_config(args)

        self.assertEqual(args.model_path, "/custom/path/to/model")
        self.assertEqual(args.model_manifest, "/custom/manifest.json")
        self.assertEqual(args.model_revision, "custom_rev_123")
        self.assertEqual(args.embedding_backend, "bge-m3-internal")

    def test_backend_execution_invokes_get_backend_with_resolved_defaults(self) -> None:
        from unittest import mock

        from dual_pipeline.cli.engine import _backend

        args = argparse.Namespace(
            runtime_root=ROOT / ".local" / "dual_pipeline",
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3",
            allow_dev_bge_m3=False,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )

        with mock.patch("dual_pipeline.cli.engine.get_backend") as mock_get_backend:
            mock_get_backend.return_value = mock.MagicMock()
            backend = _backend(args)
            self.assertIsNotNone(backend)
            mock_get_backend.assert_called_once()
            call_args, call_kwargs = mock_get_backend.call_args
            self.assertEqual(call_args[0], "bge-m3-dev")
            self.assertTrue(call_kwargs.get("allow_dev_backend"))
            self.assertEqual(call_kwargs.get("model_revision"), "5617a9f61b028005a4858fdac845db406aefb181")


if __name__ == "__main__":
    unittest.main()

