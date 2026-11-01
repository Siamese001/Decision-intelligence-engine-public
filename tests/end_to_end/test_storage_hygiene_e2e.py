"""End-to-end lifecycle test for storage hygiene pin resolution and report generation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.storage_hygiene_core.pins import resolve_pins
from tools.storage_hygiene_core.policy import load_storage_retention_policy
from tools.storage_hygiene_core.receipt import create_hygiene_report, verify_report_digest


class TestStorageHygieneE2E(unittest.TestCase):
    def test_full_pipeline_pin_resolution_and_schema_validation(self):
        """Exercise full pin resolution and verify output matches contract schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            runtime_root = repo_root / ".local" / "dual_pipeline"
            runtime_root.mkdir(parents=True)

            # 1. Setup mock active generation
            gen1 = runtime_root / "generations" / "generation-live-100"
            gen1.mkdir(parents=True)
            graph1 = gen1 / "graph.sqlite"
            graph1.write_text("mock sqlite graph", encoding="utf-8")
            manifest1 = gen1 / "pair_manifest.json"
            manifest1.write_text(
                json.dumps({"generation_id": "generation-live-100", "previous_generation_id": "generation-prev-99"}),
                encoding="utf-8",
            )

            # 2. Setup mock rollback generation
            gen0 = runtime_root / "generations" / "generation-prev-99"
            gen0.mkdir(parents=True)
            graph0 = gen0 / "graph.sqlite"
            graph0.write_text("mock sqlite graph 0", encoding="utf-8")
            manifest0 = gen0 / "pair_manifest.json"
            manifest0.write_text(
                json.dumps({"generation_id": "generation-prev-99", "previous_generation_id": None}),
                encoding="utf-8",
            )

            # 3. Setup active_pair.json
            active_pair = runtime_root / "active_pair.json"
            active_pair.write_text(
                json.dumps({
                    "generation_id": "generation-live-100",
                    "previous_generation_id": "generation-prev-99",
                    "graph_path": "generations/generation-live-100/graph.sqlite",
                }),
                encoding="utf-8",
            )

            # 4. Setup mock release citing generation-historical-88
            releases_dir = runtime_root / "releases"
            releases_dir.mkdir(parents=True)
            rel_file = releases_dir / "release_1.json"
            rel_file.write_text(
                json.dumps({"generation_id": "generation-historical-88", "status": "activated"}),
                encoding="utf-8",
            )

            # 5. Setup invalid active pair with missing graph
            invalid_dir = runtime_root / "runtime"
            invalid_dir.mkdir(parents=True)
            invalid_pair = invalid_dir / "active_pair.json"
            invalid_pair.write_text(
                json.dumps({"generation_id": "generation-leaked-pytest", "graph_path": "/nonexistent/graph.sqlite"}),
                encoding="utf-8",
            )

            # Run pin resolution
            pol = load_storage_retention_policy()
            result = resolve_pins(repo_root=repo_root, runtime_root=runtime_root, policy=pol)

            self.assertFalse(result.fail_closed)
            self.assertIn("generation-live-100", result.active_generations)
            self.assertIn("generation-prev-99", result.rollback_generations)
            self.assertIn("generation-historical-88", result.receipt_cited_generations)
            self.assertEqual(len(result.invalid_sources), 1)
            self.assertIn("generation-leaked-pytest", result.invalid_sources[0].reason or result.invalid_sources[0].path)

            pins_dict = result.to_dict()
            summary = {
                "total_candidates": 0,
                "total_pinned": len(result.all_pinned),
                "reclaimable_bytes": 0,
            }

            # Generate receipt
            report = create_hygiene_report(
                mode="plan",
                scheduled=False,
                policy_digest=pol.digest,
                pins=pins_dict,
                summary=summary,
            )

            self.assertTrue(verify_report_digest(report))

            # Validate against schema contract
            schema_path = (
                Path(__file__).resolve().parents[2]
                / "contracts"
                / "storage-hygiene-report.schema.json"
            )
            self.assertTrue(schema_path.exists())
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

            # Basic validation of required properties from schema
            for req in schema["required"]:
                self.assertIn(req, report, f"Missing required property {req} in report")


if __name__ == "__main__":
    unittest.main()
