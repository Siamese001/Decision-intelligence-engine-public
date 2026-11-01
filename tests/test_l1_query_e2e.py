#!/usr/bin/env python3
"""Wave 6 integration-boundary tests for default-off bounded mode."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from dual_pipeline.l1_control import load_policy
from dual_pipeline.query import QueryEngine

ROOT = Path(__file__).resolve().parents[1]


class MetadataBackend:
    backend_id = "fixture-backend"
    model_id = "fixture-model"
    model_revision = "fixture-revision"
    model_manifest_digest = "f" * 64
    production_qualified = False
    assurance_tier = "development"

    def verify_snapshot(self):
        return {"model_id": self.model_id, "model_revision": self.model_revision, "manifest_digest": self.model_manifest_digest}

    def encode(self, text):
        raise AssertionError("constructor boundary must not initialize an encoder")


class L1QueryE2ETests(unittest.TestCase):
    def test_learning_event_contract_admits_the_runtime_l1_telemetry_types(self) -> None:
        schema = json.loads((ROOT / "contracts/dual_pipeline/schemas/learning-event.schema.json").read_text(encoding="utf-8"))
        event_types = schema["properties"]["event_type"]["enum"]
        self.assertIn("l1_bounded", event_types)
        self.assertIn("l1_shadow", event_types)

    def test_bounded_mode_is_default_off_and_requires_explicit_candidate_policy(self) -> None:
        policy = load_policy(json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory(prefix="nee-wave6-") as temporary:
            root = Path(temporary)
            seed = root / "seed.sqlite"
            seed.write_bytes(b"seed")
            with self.assertRaisesRegex(ValueError, "explicit bounded candidate policy"):
                QueryEngine(root / "runtime", seed, MetadataBackend(), l1_controller_mode="bounded", l1_control_policy=policy)
            engine = QueryEngine(
                root / "runtime-candidate",
                seed,
                MetadataBackend(),
                l1_controller_mode="bounded",
                l1_control_policy=replace(policy, mode="bounded"),
            )
            self.assertEqual("bounded", engine.l1_controller_mode)
            self.assertEqual("off", policy.runtime_default)


if __name__ == "__main__":
    unittest.main()
