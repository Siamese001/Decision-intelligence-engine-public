#!/usr/bin/env python3
"""Wave 13 tests: Verify U0 -> L1 -> L0 stage sequencing and Macro-Routing in Intake."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import exact_file_copy, write_json
from dual_pipeline.intake import IntakeEngine


class Wave13MacroRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.graph_path = self.runtime_root / "graph.sqlite"
        exact_file_copy(Path(__file__).resolve().parents[1] / "graph" / "nee_graph.sqlite", self.graph_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _sample_package(self) -> dict[str, object]:
        fixture_path = Path(__file__).resolve().parents[1] / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_u0_l1_l0_sequencing_and_macro_routes_persisted(self) -> None:
        pkg_data = self._sample_package()
        pkg_path = self.root / "test_envelope.json"
        write_json(pkg_path, pkg_data)

        engine = IntakeEngine(self.graph_path, self.runtime_root)
        result = engine.ingest(pkg_path)

        self.assertEqual("COMPLETED", result["status"])
        run_dir = self.runtime_root / "runs" / result["run_id"]
        self.assertTrue((run_dir / "macro_routes.json").is_file())
        self.assertTrue((run_dir / "ingestion_plan_v2.json").is_file())

        macro_routes = json.loads((run_dir / "macro_routes.json").read_text(encoding="utf-8"))
        self.assertEqual(2, len(macro_routes))
        self.assertEqual(["assertion_candidate", "action_item"], [r["macro_route"] for r in macro_routes])
        for r in macro_routes:
            self.assertEqual("CANDIDATE_ADMISSION", r["intake_route"])

    def test_chronological_stage_sequence_preserves_u0_l1_l0(self) -> None:
        pkg_data = self._sample_package()
        pkg_path = self.root / "test_envelope_seq.json"
        write_json(pkg_path, pkg_data)

        engine = IntakeEngine(self.graph_path, self.runtime_root)
        result = engine.ingest(pkg_path)

        run_dir = self.runtime_root / "runs" / result["run_id"]
        event_paths = sorted((run_dir / "learning_events").glob("*.json"))
        events = [json.loads(p.read_text(encoding="utf-8")) for p in event_paths]
        stages = [e["stage"] for e in events]

        # Verify stages appear in order U0 -> L1 -> L0
        first_u0_idx = stages.index("U0")
        first_l1_idx = stages.index("L1")
        first_l0_idx = stages.index("L0")

        self.assertLess(first_u0_idx, first_l1_idx, "U0 must precede L1")
        self.assertLess(first_l1_idx, first_l0_idx, "L1 must precede L0")

        # Verify the L0 event right after L1 is indeed macro route decision
        l0_after_l1 = events[first_l0_idx]
        self.assertEqual("macro_route_decision", l0_after_l1["event_type"])
        self.assertEqual("L0", l0_after_l1["stage"])


if __name__ == "__main__":
    unittest.main()
