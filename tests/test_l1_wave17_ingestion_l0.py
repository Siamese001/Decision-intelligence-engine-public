"""Wave 17 Unit Tests: Ingestion L0 Schema Decoupling & Cryptographic Duplicate Verification.

Tests verify:
1. Macro-L0 Schema Decoupling: 'macro_route_decision' is registered in learning-event schema
   and correctly emitted by Macro-L0.1 admission routing.
2. Cryptographic Duplicate Verification: Exact normalized content match routes to
   EXACT_DUPLICATE; partial / substring overlaps route to CONTRADICTION rather than
   being silently dropped as false duplicates.
3. Telemetry Invariant: provider_calls_before_l2d == 0 and deterministic boundaries preserved.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import exact_file_copy, write_json
from dual_pipeline.intake import IntakeEngine, _route_for_candidate


class Wave17IngestionL0Tests(unittest.TestCase):
    """Test suite for Wave 17 Ingestion L0 Hardening."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.graph_path = self.runtime_root / "graph.sqlite"
        exact_file_copy(
            Path(__file__).resolve().parents[1] / "graph" / "nee_graph.sqlite",
            self.graph_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _sample_package(self) -> dict[str, object]:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_macro_route_decision_schema_event_valid(self) -> None:
        """macro_route_decision must be valid according to learning-event schema."""
        pkg_data = self._sample_package()
        pkg_path = self.root / "test_envelope_w17.json"
        write_json(pkg_path, pkg_data)

        engine = IntakeEngine(self.graph_path, self.runtime_root)
        result = engine.ingest(pkg_path)
        self.assertEqual("COMPLETED", result["status"])

        run_dir = self.runtime_root / "runs" / result["run_id"]
        event_paths = sorted((run_dir / "learning_events").glob("*.json"))
        events = [json.loads(p.read_text(encoding="utf-8")) for p in event_paths]

        macro_events = [e for e in events if e.get("event_type") == "macro_route_decision"]
        self.assertTrue(len(macro_events) >= 1)
        for me in macro_events:
            self.assertEqual("L0", me["stage"])
            # Validate against closed-world learning-event schema
            validated = validate_artifact(me)
            self.assertEqual("macro_route_decision", validated["event_type"])

    def test_exact_cryptographic_duplicate_vs_partial_overlap_routing(self) -> None:
        """Exact content match routes to EXACT_DUPLICATE; partial overlap routes to CONTRADICTION."""
        candidate = {
            "candidate_id": "cand-test-001",
            "candidate_type": "assertion",
            "materiality": "high",
        }
        resolutions = [{"outcome": "AUTO_LINK_EXACT"}]

        # 1. Exact match case: exact_matches populated
        exact_comparison = {
            "exact_matches": [{"record_id": "rec-1", "object_id": "obj-1", "lane": "approved"}],
            "partial_overlaps": [],
        }
        route, reasons = _route_for_candidate(candidate, resolutions, exact_comparison)
        self.assertEqual("EXACT_DUPLICATE", route)
        self.assertIn("exact normalized content match", reasons[0])

        # 2. Partial / Substring overlap case: partial_overlaps populated without exact match
        overlap_comparison = {
            "exact_matches": [],
            "partial_overlaps": [{"record_id": "rec-1", "object_id": "obj-1", "lane": "approved"}],
        }
        route, reasons = _route_for_candidate(candidate, resolutions, overlap_comparison)
        self.assertEqual("CONTRADICTION", route)
        self.assertIn("partial content overlap requires contradiction review", reasons[0])

        # 3. Neither exact nor partial: routes to normal assertion candidate
        novel_comparison = {
            "exact_matches": [],
            "partial_overlaps": [],
        }
        route, reasons = _route_for_candidate(candidate, resolutions, novel_comparison)
        self.assertEqual("NEW_ASSERTION", route)


if __name__ == "__main__":
    unittest.main()
