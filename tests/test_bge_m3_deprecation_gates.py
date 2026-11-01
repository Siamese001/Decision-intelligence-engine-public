"""Fast, no-encoder regressions for permanent BGE-M3-only controls."""
from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.evals.runner import (
    EvaluationFailure,
    _assert_operator_backend,
    _validated_template_pair_binding,
    run_suite,
)
from dual_pipeline.index import get_backend
from dual_pipeline.release import require_approved_bge_m3_pair
from dual_pipeline.semantic_cache import SemanticCache, configure_semantic_cache
from tests.embedding_fixture import MetadataOnlyBgeM3Backend

RETIRED_EMBEDDING_BACKEND = "deterministic" + "-test"


class NoEncodeBgeMetadata:
    """Metadata-only object used to prove controls do not create substitute vectors."""

    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = "a" * 40
    model_manifest_digest = "b" * 64
    production_qualified = False
    assurance_tier = "development"
    retrieval_encoder = "real-bge-m3"

    def __init__(self, *, snapshot_manifest: str | None = None) -> None:
        self.snapshot_manifest = snapshot_manifest or self.model_manifest_digest
        self.encode_call_count = 0

    def encode(self, _text: str) -> dict[str, object]:
        self.encode_call_count += 1
        raise AssertionError("non-retrieval test metadata must never encode text")

    def verify_snapshot(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.snapshot_manifest,
        }


def _pair() -> dict[str, object]:
    return {
        "generation_id": "generation-bge-gate",
        "graph_sha256": "c" * 64,
        "sidecar_sha256": "d" * 64,
        "embedding_backend": "flagembedding-bge-m3-dev",
        "model_id": "BAAI/bge-m3",
        "model_revision": "a" * 40,
        "model_manifest_digest": "b" * 64,
        "production_qualified": False,
    }


class BgeM3DeprecationGateTests(unittest.TestCase):
    def test_retired_backend_selection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported embedding backend"):
            get_backend(RETIRED_EMBEDDING_BACKEND, allow_dev_backend=True)

    def test_active_pair_gate_rejects_retired_backend(self) -> None:
        pair = _pair()
        pair["embedding_backend"] = RETIRED_EMBEDDING_BACKEND
        with self.assertRaisesRegex(RuntimeError, "approved BGE-M3"):
            require_approved_bge_m3_pair(pair)

    def test_eval_operator_rejects_missing_manifest_and_nonretrieval_double(self) -> None:
        metadata = MetadataOnlyBgeM3Backend()
        with mock.patch.object(metadata, "encode", side_effect=AssertionError) as encode:
            with self.assertRaisesRegex(EvaluationFailure, "real BGE-M3 retrieval encoder"):
                _assert_operator_backend(metadata)
        encode.assert_not_called()

        missing_manifest = NoEncodeBgeMetadata()
        missing_manifest.model_manifest_digest = ""
        with self.assertRaisesRegex(EvaluationFailure, "model manifest digest"):
            _assert_operator_backend(missing_manifest)
        self.assertEqual(0, missing_manifest.encode_call_count)

    def test_eval_template_pair_fails_closed_on_graph_or_binding_drift(self) -> None:
        backend = NoEncodeBgeMetadata()
        manager = mock.Mock()
        manager.validate_pair.side_effect = RuntimeError("sidecar is absent")
        with self.assertRaisesRegex(EvaluationFailure, "graph/index pair"):
            _validated_template_pair_binding(manager, _pair(), backend)

        manager.validate_pair.side_effect = None
        manager.validate_pair.return_value = {"status": "PASS"}
        wrong_manifest = deepcopy(_pair())
        wrong_manifest["model_manifest_digest"] = "e" * 64
        with self.assertRaisesRegex(EvaluationFailure, "does not match the BGE backend"):
            _validated_template_pair_binding(manager, wrong_manifest, backend)

        missing_manifest = deepcopy(_pair())
        missing_manifest["model_manifest_digest"] = None
        with self.assertRaisesRegex(EvaluationFailure, "incomplete graph/index pair binding"):
            _validated_template_pair_binding(manager, missing_manifest, backend)
        self.assertEqual(0, backend.encode_call_count)

    def test_eval_run_exits_before_creating_output_for_invalid_template_pair(self) -> None:
        backend = NoEncodeBgeMetadata()
        with tempfile.TemporaryDirectory(prefix="nee-eval-template-gate-") as directory:
            runtime = Path(directory) / "runtime"
            manager = mock.Mock()
            manager.bootstrap.return_value = _pair()
            manager.validate_pair.side_effect = RuntimeError("sidecar is absent")
            from dual_pipeline.core import sha256_file as real_sha256
            def fake_sha256(path: Path) -> str:
                if str(path).endswith("nee_graph.sqlite"):
                    return "856fe4505248ee70f9b524de23ce5defb3daafae4b881a5c5dd5260c31292c3c"
                return real_sha256(path)

            with (
                mock.patch("dual_pipeline.evals.runner_core.suite.sha256_file", side_effect=fake_sha256),
                mock.patch("dual_pipeline.evals.runner_core.suite._assert_expected_records_present", return_value=None),
                mock.patch("dual_pipeline.evals.runner.ReleaseManager", return_value=manager),
            ):
                with self.assertRaisesRegex(EvaluationFailure, "graph/index pair"):
                    run_suite(
                        suite_path=ROOT / "evals" / "suites" / "dual-deterministic-e1.json",
                        runtime_root=runtime,
                        seed_graph=ROOT / "graph" / "nee_graph.sqlite",
                        backend=backend,
                        evaluation_id="invalid-template-pair",
                    )
            self.assertFalse((runtime / "evaluations").exists())
            self.assertFalse((runtime / ".eval-work").exists())
            self.assertEqual(0, backend.encode_call_count)

    def test_cache_rebuild_purges_unbound_entries_without_an_encoder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-bge-cache-gate-") as directory:
            runtime = Path(directory) / "runtime"
            configure_semantic_cache(runtime, "write")
            pair = _pair()
            cache = SemanticCache(runtime)
            self.assertEqual("BOUND", cache.rebuild_from_validated_active_pair(pair)["status"])
            staged = cache.stage(
                context={"use_case": "query"},
                intent_digest="intent-gate",
                intent_vector=[0.25, 0.75],
                output={"answer": "stale"},
                outcome="X3D_ALLOW_FINISH",
                source_run_id="run-gate",
                source_trace_id="trace-gate",
                replay_eligible=True,
            )
            self.assertEqual("PENDING", staged.status)
            connection = cache._connection()
            try:
                connection.execute("DELETE FROM semantic_cache_embedding_binding")
                connection.commit()
            finally:
                connection.close()

            rebuilt = SemanticCache(runtime)
            result = rebuilt.rebuild_from_validated_active_pair(pair)
            self.assertEqual("REBUILT", result["status"])
            self.assertEqual(1, result["purged_entry_count"])
            self.assertEqual({"PENDING": 0, "ACTIVE": 0}, rebuilt.status()["entry_counts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
