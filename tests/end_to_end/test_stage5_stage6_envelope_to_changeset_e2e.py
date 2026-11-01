#!/usr/bin/env python3
"""End-to-end tests for Stage 5 & Stage 6: Evidence Envelope to GraphChangeSet & Fail-Closed HITL/Resume."""
from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ROOT, digest, read_json, utc_now, write_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    resume_ingestion_with_approval,
)
from dual_pipeline.intake import IntakeEngine
from tools.prepare_evidence_envelope_intake import prepare_package


class Stage5Stage6EnvelopeToChangesetE2ETests(unittest.TestCase):
    def test_end_to_end_envelope_to_changeset_and_hitl_review_lifecycle(self) -> None:
        """Full E2E: draft -> prepare_package -> IntakeEngine.ingest -> HITL review -> GraphChangeSet -> active approval."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            graph_path = tmp_path / "nee_graph.sqlite"
            graph_path.write_bytes(graph_src.read_bytes())
            runtime_root = tmp_path / "runtime"

            source_path = tmp_path / "architecture_charter.md"
            source_text = (
                "# Apex Global Analytics Q4 Architecture Charter\n\n"
                "## Executive Summary\n"
                "Alex Rivera presented the Q4 architecture charter for the Atlas workstream. "
                "The team decided to standardize on governed SQLite WAL sidecars for deterministic staging. "
                "Cross-region replication latency above 150ms is tracked as a primary deployment risk. "
                + (" ".join(f"context_token_{i}" for i in range(60)))
            )
            source_path.write_text(source_text, encoding="utf-8")

            draft = {
                "meeting_id": "meeting-20261025-charter",
                "title": "Apex Global Analytics Q4 Architecture Charter",
                "date": "2026-10-25",
                "classification": "internal",
                "audiences": ["nee-engineering"],
                "source_location": "archive://charters/2026-10-25-charter.md",
                "evidence_index": {
                    "E-01": {
                        "speaker": "Alex Rivera",
                        "role_or_org": "Atlas Workstream Lead",
                        "source_classification": "decision",
                        "exact_text": "The team decided to standardize on governed SQLite WAL sidecars for deterministic staging.",
                        "locator": {"kind": "section", "value": "## Executive Summary"},
                    },
                    "E-02": {
                        "speaker": "Alex Rivera",
                        "role_or_org": "Atlas Workstream Lead",
                        "source_classification": "risk",
                        "exact_text": "Cross-region replication latency above 150ms is tracked as a primary deployment risk.",
                        "locator": {"kind": "section", "value": "## Executive Summary"},
                    },
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "workstream:atlas_charter",
                            "name": "Atlas Charter Workstream",
                            "type": "workstream",
                            "description": "Governed Q4 architecture charter workstream",
                            "evidence_ids": ["E-01"],
                        },
                        {
                            "id": "system:wal_sidecar",
                            "name": "SQLite WAL Sidecar",
                            "type": "system",
                            "description": "Deterministic local storage sidecar",
                            "evidence_ids": ["E-01"],
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "workstream:atlas_charter",
                            "to_id": "system:wal_sidecar",
                            "predicate": "DEPENDS_ON",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                    "decision_updates": [
                        {
                            "decision_id": "decision:dec-01",
                            "decision_text": "Standardize on governed SQLite WAL sidecars for deterministic staging.",
                            "entity_id": "workstream:atlas_charter",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                    "risk_updates": [
                        {
                            "risk_id": "risk:risk-01",
                            "risk_text": "Cross-region replication latency above 150ms is a deployment risk.",
                            "entity_id": "workstream:atlas_charter",
                            "evidence_ids": ["E-02"],
                        }
                    ],
                },
            }
            draft_path = tmp_path / "draft.json"
            envelope_path = tmp_path / "envelope.json"
            write_json(draft_path, draft)

            envelope = prepare_package(draft_path, source_path, envelope_path, overwrite=True)
            self.assertIn("com.partner_advisory.nee.projection", envelope["extensions"])

            engine = IntakeEngine(graph_path, runtime_root)
            summary = engine.ingest(envelope_path)
            self.assertEqual("COMPLETED", summary["status"])
            run_id = summary["run_id"]
            run_dir = runtime_root / "runs" / run_id

            # Verify L4 decision ledger was recorded after X3 exit gates (DEF-S6-11)
            events = [
                read_json(p)
                for p in sorted((run_dir / "learning_events").glob("*.json"))
            ]
            stages = [e["stage"] for e in events]
            self.assertIn("X3", stages)
            self.assertIn("L4", stages)
            last_x3_idx = max(i for i, s in enumerate(stages) if s == "X3")
            last_l4_idx = max(i for i, s in enumerate(stages) if s == "L4")
            self.assertGreater(last_l4_idx, last_x3_idx)

            # Verify DEF-S6-06: empty decisions list must fail closed when review items exist
            hitl_batches = read_json(run_dir / "hitl_batches.json")
            if hitl_batches["review_batches"]:
                empty_decisions_path = tmp_path / "empty_decisions.json"
                write_json(empty_decisions_path, {"decisions": []})
                with self.assertRaisesRegex(ValueError, "at least one decision"):
                    engine.review(run_id, empty_decisions_path)

            # Verify GraphChangeSet and planned delta were emitted
            change_set = read_json(run_dir / "graph_change_set.json")
            self.assertGreaterEqual(len(change_set["operations"]), 1)
            delta_summary = read_json(run_dir / "graph_delta_summary.json")
            delta_digest = digest(delta_summary)

            # Verify Stage 6 Active Ingestion Approval lifecycle & scope enforcement (DEF-S6-01, DEF-S6-07)
            ledger_path = tmp_path / "hitl_decision_ledger.json"
            req = build_active_ingestion_approval_request(
                run_id=run_id,
                directive_id="directive-e2e-01",
                project="nea",
                namespace="ns",
                source_package_digest=envelope["canonical_digest"]["value"],
                proposed_delta_digest=delta_digest,
                baseline_generation="gen-01",
                table_deltas={"node": 2, "assertion": 3},
                runtime_root=str(runtime_root),
            )
            req_dir = runtime_root / "approval_requests"
            req_dir.mkdir(parents=True, exist_ok=True)
            req_file = req_dir / f"{req['request_id']}.json"
            write_json(req_file, req)

            approved_ok, _, _ = resume_ingestion_with_approval(
                request_id=req["request_id"],
                operator_response={
                    "selected_option": req["question_payload"]["options"][0]["label"],
                    "operator_id": "amit.ayer@consulting.example.com",
                    "origin": "interactive_hitl_cli",
                },
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertTrue(approved_ok)

            cleared, _, _ = check_active_ingestion_clearance(
                run_id=run_id,
                directive_id="directive-e2e-01",
                delta_digest=delta_digest,
                ledger_path=ledger_path,
            )
            self.assertTrue(cleared)

            # Scope drift (different delta_digest with same directive_id) must fail closed
            cleared_drift, _, _ = check_active_ingestion_clearance(
                run_id=run_id,
                directive_id="directive-e2e-01",
                delta_digest="f" * 64,
                ledger_path=ledger_path,
            )
            self.assertFalse(cleared_drift)


if __name__ == "__main__":
    unittest.main()
