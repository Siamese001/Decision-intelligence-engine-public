#!/usr/bin/env python3
"""Regression tests for private sidecar construction and atomic publication."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.index import (  # noqa: E402
    build_sidecar,
    validate_sidecar,
)
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class _ObservingBackend(BgeM3TestBackend):
    """Encoder that proves the public path stays absent during every encode."""

    def __init__(self, public_path: Path) -> None:
        super().__init__()
        self.public_path = public_path
        self.public_path_seen_during_encode: list[bool] = []

    def encode(self, text: str) -> dict[str, object]:
        self.public_path_seen_during_encode.append(self.public_path.exists())
        return super().encode(text)


class _FailingBackend(BgeM3TestBackend):
    """Fail after sidecar staging has begun but before publication."""

    def encode(self, text: str) -> dict[str, object]:
        raise RuntimeError("injected embedding failure")


class AtomicSidecarPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-atomic-sidecar-")
        self.root = Path(self.temp.name)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_public_sidecar_appears_only_after_all_embeddings_validate(self) -> None:
        sidecar = self.root / "generation" / "sidecar.sqlite"
        backend = _ObservingBackend(sidecar)

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=sidecar,
            generation_id="generation-atomic-publication",
            graph_snapshot="graph-snapshot-atomic-publication",
            backend=backend,
        )

        self.assertTrue(backend.public_path_seen_during_encode)
        self.assertFalse(any(backend.public_path_seen_during_encode))
        self.assertTrue(sidecar.is_file())
        self.assertFalse((sidecar.parent / ".sidecar.sqlite.partial").exists())
        self.assertEqual("PUBLISHED", receipt["publication_state"])
        validation = validate_sidecar(
            sidecar,
            expected_generation_id="generation-atomic-publication",
            expected_graph_snapshot="graph-snapshot-atomic-publication",
        )
        self.assertEqual(
            receipt["record_count"], int(validation["metadata"]["record_count"])
        )

    def test_embedding_failure_leaves_neither_public_nor_staging_sidecar(self) -> None:
        sidecar = self.root / "generation" / "sidecar.sqlite"

        with self.assertRaisesRegex(RuntimeError, "injected embedding failure"):
            build_sidecar(
                graph_path=self.graph,
                sidecar_path=sidecar,
                generation_id="generation-atomic-failure",
                graph_snapshot="graph-snapshot-atomic-failure",
                backend=_FailingBackend(),
            )

        self.assertFalse(sidecar.exists())
        self.assertFalse((sidecar.parent / ".sidecar.sqlite.partial").exists())
        self.assertFalse((sidecar.parent / ".sidecar.sqlite.partial-journal").exists())
        self.assertFalse((sidecar.parent / ".sidecar.sqlite.partial-wal").exists())
        self.assertFalse((sidecar.parent / ".sidecar.sqlite.partial-shm").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
