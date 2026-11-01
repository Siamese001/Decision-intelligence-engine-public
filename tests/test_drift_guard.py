"""Regression tests for exact runtime binding and source-policy drift."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import sha256_file, write_json  # noqa: E402
from dual_pipeline.drift_guard import (  # noqa: E402
    DriftError,
    assert_file_unchanged,
    assert_replay_binding,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from dual_pipeline.l2 import load_l2_config  # noqa: E402
from dual_pipeline.query_router import load_query_routing_policy  # noqa: E402
from dual_pipeline.x1_quality import load_x1_quality_config  # noqa: E402


class _Backend:
    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = "1" * 40
    model_manifest_digest = "2" * 64
    production_qualified = False
    assurance_tier = "development"

    def verify_snapshot(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.model_manifest_digest,
        }


class _Manager:
    def __init__(self, root: Path, pair: dict[str, object]) -> None:
        self.runtime_root = root
        self.lock_path = root / "active_pair.lock"
        self.pair = pair

    def _resolve(self, value: str) -> Path:
        return (self.runtime_root / value).resolve()

    def active_pair(self) -> dict[str, object]:
        return copy.deepcopy(self.pair)


def _pair(root: Path, backend: _Backend) -> dict[str, object]:
    graph = root / "graph.sqlite"
    sidecar = root / "sidecar.sqlite"
    graph.write_bytes(b"graph-v1")
    sidecar.write_bytes(b"sidecar-v1")
    return {
        "generation_id": "generation-1",
        "graph_snapshot": "snapshot-1",
        "graph_sha256": sha256_file(graph),
        "sidecar_sha256": sha256_file(sidecar),
        "graph_path": "graph.sqlite",
        "sidecar_path": "sidecar.sqlite",
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "production_qualified": backend.production_qualified,
        "assurance_tier": backend.assurance_tier,
        "internal_operational_policy_digest": None,
    }


class DriftGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-drift-guard-")
        self.root = Path(self.temp.name)
        self.backend = _Backend()
        self.manager = _Manager(self.root, _pair(self.root, self.backend))
        self.policies = {
            "routing": "3" * 64,
            "l2": "4" * 64,
            "x1": "5" * 64,
        }
        self.binding = capture_runtime_binding(
            manager=self.manager,
            active_pair=self.manager.pair,
            backend=self.backend,
            policy_digests=self.policies,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_binding_revalidates(self) -> None:
        refreshed = assert_runtime_binding_unchanged(
            manager=self.manager,
            expected_binding=self.binding,
            backend=self.backend,
            policy_digests=self.policies,
        )
        self.assertEqual(self.binding, refreshed)

    def test_graph_byte_drift_is_not_hidden_by_pair_metadata(self) -> None:
        (self.root / "graph.sqlite").write_bytes(b"graph-v2")
        with self.assertRaises(DriftError) as context:
            assert_runtime_binding_unchanged(
                manager=self.manager,
                expected_binding=self.binding,
                backend=self.backend,
                policy_digests=self.policies,
            )
        self.assertEqual("GRAPH_DRIFT", context.exception.code)

    def test_active_pair_drift_is_distinct_from_file_drift(self) -> None:
        self.manager.pair["generation_id"] = "generation-2"
        with self.assertRaises(DriftError) as context:
            assert_runtime_binding_unchanged(
                manager=self.manager,
                expected_binding=self.binding,
                backend=self.backend,
                policy_digests=self.policies,
            )
        self.assertEqual("ACTIVE_PAIR_DRIFT", context.exception.code)

    def test_backend_and_policy_drift_are_fail_closed(self) -> None:
        self.backend.model_revision = "6" * 40
        with self.assertRaises(DriftError) as context:
            assert_runtime_binding_unchanged(
                manager=self.manager,
                expected_binding=self.binding,
                backend=self.backend,
                policy_digests=self.policies,
            )
        self.assertEqual("BACKEND_BINDING_DRIFT", context.exception.code)

        self.backend.model_revision = "1" * 40
        changed = dict(self.policies)
        changed["l2"] = "7" * 64
        with self.assertRaises(DriftError) as context:
            assert_runtime_binding_unchanged(
                manager=self.manager,
                expected_binding=self.binding,
                backend=self.backend,
                policy_digests=changed,
            )
        self.assertEqual("POLICY_DRIFT", context.exception.code)

    def test_replay_requires_a_valid_exact_binding(self) -> None:
        with self.assertRaises(DriftError) as context:
            assert_replay_binding({"status": "COMPLETED"}, self.binding)
        self.assertEqual("REPLAY_BINDING_MISSING", context.exception.code)

        result = {"runtime_binding": copy.deepcopy(self.binding)}
        assert_replay_binding(result, self.binding)
        result["runtime_binding"]["pair"]["generation_id"] = "changed"
        with self.assertRaises(DriftError) as context:
            assert_replay_binding(result, self.binding)
        self.assertEqual("REPLAY_BINDING_INVALID", context.exception.code)

    def test_policy_loaders_observe_same_process_source_changes(self) -> None:
        sources = (
            (
                "routing.json",
                ROOT / "requirements" / "query-routing-policy.json",
                load_query_routing_policy,
            ),
            ("l2.json", ROOT / "requirements" / "l2-policy.json", load_l2_config),
            (
                "x1.json",
                ROOT / "requirements" / "x1-quality-policy.json",
                load_x1_quality_config,
            ),
        )
        for name, source, loader in sources:
            target = self.root / name
            raw = json.loads(source.read_text(encoding="utf-8"))
            write_json(target, raw)
            first = loader(target)
            if name == "routing.json":
                raw["default_artifact_type"] = (
                    "evidence_export"
                    if raw["default_artifact_type"] != "evidence_export"
                    else "answer"
                )
            elif name == "l2.json":
                raw["provider_egress_allowed"] = not raw["provider_egress_allowed"]
            else:
                raw["provider_egress_allowed"] = not raw["provider_egress_allowed"]
            write_json(target, raw)
            second = loader(target)
            self.assertNotEqual(
                first.policy_digest if name == "routing.json" else first.config_digest,
                second.policy_digest
                if name == "routing.json"
                else second.config_digest,
            )

    def test_file_digest_helper_rejects_changed_bytes(self) -> None:
        path = self.root / "input.json"
        path.write_bytes(b"one")
        expected = sha256_file(path)
        assert_file_unchanged(path, expected, label="test input")
        path.write_bytes(b"two")
        with self.assertRaises(DriftError) as context:
            assert_file_unchanged(path, expected, label="test input")
        self.assertEqual("INPUT_FILE_DRIFT", context.exception.code)

if __name__ == "__main__":
    unittest.main()
