#!/usr/bin/env python3
"""Focused unit tests for ReleaseManager._prepare_stage_audit_components invariants.

Verifies fail-closed gating on:
1. Candidate vs disposition count mismatches
2. Mid-intake graph hash mutations
3. Unauthenticated production release attempts
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
from dual_pipeline.release import ReleaseManager, _PrepareManyState

FIXTURE_BUNDLE = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "normalized-candidate-bundle.json"


class ReleasePreparationInvariantsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee-release-invariants-"))
        self.runtime_root = self.temp_dir / "runtime"
        self.runs_root = self.runtime_root / "runs"
        self.run_id = "run-intake-nea-update-2026-07-21"
        self.run_dir = self.runs_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Copy valid normalized candidate bundle fixture verbatim
        shutil.copy(FIXTURE_BUNDLE, self.run_dir / "candidate_bundle.json")

        self.graph_path = ROOT / "graph" / "nee_graph.sqlite"
        self.manager = ReleaseManager(self.runtime_root, self.graph_path)

        # Non-production backend stub
        self.dev_backend = MagicMock()
        self.dev_backend.production_qualified = False
        self.dev_backend.backend_id = "bge-m3-dev"
        self.dev_backend.model_id = "BAAI/bge-m3"
        self.dev_backend.model_revision = "dev-rev"
        self.dev_backend.model_manifest_digest = "a" * 64

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_state(self, backend=None) -> _PrepareManyState:
        state = _PrepareManyState(
            run_ids=[self.run_id],
            approval_id="appr-001",
            backend=backend or self.dev_backend,
        )
        state.runs_root = self.runs_root
        state.components = []
        return state

    def test_prepare_many_rejects_candidate_disposition_count_mismatch(self) -> None:
        """Intake run with candidate_count != disposition_count must fail closed."""
        summary = {
            "status": "COMPLETED",
            "run_id": self.run_id,
            "trace_id": "0123456789abcdef0123456789abcdef",
            "source_id": "source-nea-update-2026-07-21",
            "candidate_count": 2,
            "disposition_count": 1,  # MISMATCH: 2 candidates but only 1 disposition
            "graph_sha256_before": "hash_before_123",
            "graph_sha256_after": "hash_before_123",
            "learning_event_count": 0,
        }
        (self.run_dir / "intake_summary.json").write_text(json.dumps(summary), encoding="utf-8")

        state = self._make_state()
        with self.assertRaisesRegex(RuntimeError, "release preparation requires a completed intake run"):
            self.manager._prepare_stage_audit_components(state)

    def test_prepare_many_rejects_mutated_graph_hash_during_intake(self) -> None:
        """Intake run with graph_sha256_before != graph_sha256_after must fail closed."""
        summary = {
            "status": "COMPLETED",
            "run_id": self.run_id,
            "trace_id": "0123456789abcdef0123456789abcdef",
            "source_id": "source-nea-update-2026-07-21",
            "candidate_count": 2,
            "disposition_count": 2,
            "graph_sha256_before": "hash_before_123",
            "graph_sha256_after": "hash_MUTATED_456",  # DRIFT: uncontrolled mid-intake graph mutation
            "learning_event_count": 0,
        }
        (self.run_dir / "intake_summary.json").write_text(json.dumps(summary), encoding="utf-8")

        state = self._make_state()
        with self.assertRaisesRegex(RuntimeError, "release preparation requires a completed intake run"):
            self.manager._prepare_stage_audit_components(state)

    def test_prepare_many_rejects_unauthenticated_production_intake(self) -> None:
        """Production release without valid production authorization binding must raise PermissionError."""
        prod_backend = MagicMock()
        prod_backend.production_qualified = True
        prod_backend.backend_id = "flagembedding-bge-m3"
        prod_backend.model_id = "BAAI/bge-m3"
        prod_backend.model_revision = "prod-rev-1"
        prod_backend.model_manifest_digest = "b" * 64

        # Summary missing required production model manifest binding and authorization
        summary = {
            "status": "COMPLETED",
            "run_id": self.run_id,
            "trace_id": "0123456789abcdef0123456789abcdef",
            "source_id": "source-nea-update-2026-07-21",
            "candidate_count": 2,
            "disposition_count": 2,
            "graph_sha256_before": "hash_before_123",
            "graph_sha256_after": "hash_before_123",
            "learning_event_count": 0,
            # No intake_authorization dict provided
        }
        (self.run_dir / "intake_summary.json").write_text(json.dumps(summary), encoding="utf-8")

        state = self._make_state(backend=prod_backend)
        with self.assertRaisesRegex(
            PermissionError,
            "production release requires a production-authenticated intake bound to the BGE-M3 generation",
        ):
            self.manager._prepare_stage_audit_components(state)


if __name__ == "__main__":
    unittest.main()
