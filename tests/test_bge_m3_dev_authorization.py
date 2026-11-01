#!/usr/bin/env python3
"""Controls for explicit, permanently non-production BGE-M3 development use."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.cli import build_parser  # noqa: E402
from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    DevelopmentFlagEmbeddingBackend,
    HybridRetriever,
    get_backend,
)
from dual_pipeline.query import QueryEngine  # noqa: E402
from dual_pipeline.release import (  # noqa: E402
    ReleaseManager,
    require_approved_bge_m3_pair,
)
from tests.embedding_fixture import (  # noqa: E402
    BgeM3TestBackend,
    MetadataOnlyBgeM3Backend,
)


class DevelopmentBgeStub(BgeM3TestBackend):
    def __init__(self) -> None:
        self.snapshot_checks = 0

    def verify_snapshot(self):
        self.snapshot_checks += 1
        return super().verify_snapshot()


class BgeM3DevelopmentAuthorizationTests(unittest.TestCase):
    def test_00_release_rejects_metadata_only_encoder_before_indexing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-metadata-only-bge-") as directory:
            manager = ReleaseManager(
                Path(directory) / "runtime", ROOT / "graph" / "nee_graph.sqlite"
            )
            with self.assertRaisesRegex(RuntimeError, "real BGE-M3 retrieval encoder"):
                manager.bootstrap(MetadataOnlyBgeM3Backend())

    def test_01_active_pair_rejects_non_bge_or_unbound_metadata(self) -> None:
        pair = {
            "embedding_backend": "metadata-only",
            "model_id": "metadata-only",
            "model_revision": "a" * 40,
            "model_manifest_digest": "b" * 64,
        }
        with self.assertRaisesRegex(RuntimeError, "approved BGE-M3"):
            require_approved_bge_m3_pair(pair)
        pair.update(
            {
                "embedding_backend": "flagembedding-bge-m3-dev",
                "model_id": "BAAI/bge-m3",
            }
        )
        require_approved_bge_m3_pair(pair)

    def test_02_dev_backend_requires_both_explicit_controls(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--allow-dev-bge-m3"):
            get_backend("bge-m3-dev")

        retired_backend = "-".join(("deterministic", "test"))
        with self.assertRaisesRegex(ValueError, "unsupported embedding backend"):
            get_backend(retired_backend, allow_dev_backend=True)

        with mock.patch.object(
            DevelopmentFlagEmbeddingBackend, "__init__", return_value=None
        ):
            backend = get_backend("bge-m3-dev", allow_dev_backend=True)
        self.assertEqual("flagembedding-bge-m3-dev", backend.backend_id)
        self.assertFalse(backend.production_qualified)

    def test_03_cli_parses_explicit_dev_authorization(self) -> None:
        args = build_parser().parse_args(
            [
                "demo",
                "--package",
                "package.json",
                "--embedding-backend",
                "bge-m3-dev",
                "--allow-dev-bge-m3",
            ]
        )
        self.assertEqual("bge-m3-dev", args.embedding_backend)
        self.assertTrue(args.allow_dev_bge_m3)

    def test_04_real_model_dev_identity_stays_non_production_end_to_end(self) -> None:
        backend = DevelopmentBgeStub()
        with tempfile.TemporaryDirectory(prefix="nee-bge-m3-dev-") as directory:
            runtime = Path(directory) / "runtime"
            graph = ROOT / "graph" / "nee_graph.sqlite"
            manager = ReleaseManager(runtime, graph)
            pair = manager.bootstrap(backend)

            self.assertEqual("flagembedding-bge-m3-dev", pair["embedding_backend"])
            self.assertEqual(backend.model_manifest_digest, pair["model_manifest_digest"])
            self.assertFalse(pair["production_qualified"])
            sidecar = manager._resolve(pair["sidecar_path"])
            retriever = HybridRetriever(sidecar, backend)
            self.assertEqual(
                "false", retriever.validation["metadata"]["production_qualified"]
            )

            result = QueryEngine(runtime, graph, backend).ask(
                "Which exact governed PDLC records are available?",
                query_type="exact_lookup",
                audience="internal_consulting,nee-engineering",
            )
            self.assertEqual("flagembedding-bge-m3-dev", result["embedding_backend"])
            self.assertFalse(result["production_qualified"])
            validated_request = read_json(
                runtime / "runs" / result["run_id"] / "validated_request.json"
            )
            self.assertIsNone(validated_request["authorization_context_digest"])
            self.assertIsNone(validated_request["authorization_token_id"])
            self.assertGreaterEqual(backend.snapshot_checks, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
