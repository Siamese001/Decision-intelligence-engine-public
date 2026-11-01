#!/usr/bin/env python3
"""Focused checks for the X1--X3 graph-delta exit artifact."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest, read_json, write_json
from dual_pipeline.index import HybridRetriever
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.release import ReleaseManager
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend

ROOT = Path(__file__).resolve().parents[1]


class GraphDeltaExitSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-graph-delta-exit-")
        self.runtime = Path(self.temp.name) / "runtime"
        self.backend = BgeM3TestBackend()
        self.manager = ReleaseManager(self.runtime, ROOT / "graph" / "nee_graph.sqlite")
        self.manager.bootstrap(self.backend)
        self.fixture = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "copilot-meeting.json"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _ingest(self) -> dict:
        active = self.manager.active_pair()
        return IntakeEngine(
            self.manager._resolve(active["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                self.manager._resolve(active["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=active,
        ).ingest(self.fixture)

    def test_x1_x2_x3_bind_a_planned_table_and_w0_verifies_actual_counts(self) -> None:
        intake = self._ingest()
        run_dir = self.runtime / "runs" / intake["run_id"]
        planned = read_json(run_dir / "graph_delta_summary.json")
        self.assertEqual(digest(planned), intake["graph_delta_summary_digest"])
        self.assertEqual(planned["table"], intake["graph_change_table"])
        self.assertEqual(intake["graph_operation_count"], planned["operation_count"])
        self.assertEqual(
            [
                "source",
                "source_version",
                "evidence",
                "external_concept",
                "internal_entity",
                "candidate_assertion",
                "canonical_assertion",
                "superseded_assertion",
                "candidate_search_document",
                "audit_event",
            ],
            [row["component_key"] for row in planned["table"]],
        )
        events = [
            read_json(path)
            for path in sorted((run_dir / "learning_events").glob("*.json"))
            if read_json(path).get("event_type") == "exit_gate_event"
        ]
        recorder = RunRecorder(
            self.runtime,
            run_id=intake["run_id"],
            trace_id=intake["trace_id"],
            use_case="intake",
            graph_snapshot=None,
        )
        graph_delta_events = []
        for event in events:
            payload = read_json(
                recorder.payload_dir / event["payload_reference"].rsplit("/", 1)[-1]
            )
            if payload.get("exit_gate") != "graph_delta_summary":
                continue
            graph_delta_events.append(event)
            self.assertEqual(digest(planned), payload["graph_delta_summary_digest"])
        self.assertEqual({"X1", "X2", "X3"}, {event["stage"] for event in graph_delta_events})

        receipt = self.manager.prepare(
            run_id=intake["run_id"],
            approval_id="graph-delta-summary-test-approval",
            backend=self.backend,
        )
        actual = receipt["graph_delta_summary"]
        self.assertEqual("PASS", actual["status"])
        self.assertEqual(intake["run_id"], actual["run_ids"][0])
        self.assertTrue(all(row["status"] == "PASS" for row in actual["table"]))
        self.assertTrue(
            all(
                row["actual_delta"] == row["planned_delta"]
                for row in actual["table"]
            )
        )

    def test_release_fails_closed_when_the_sealed_graph_delta_table_is_tampered(self) -> None:
        intake = self._ingest()
        summary_path = self.runtime / "runs" / intake["run_id"] / "graph_delta_summary.json"
        tampered = read_json(summary_path)
        tampered["table"][0]["planned_delta"] += 1
        tampered["table"][0]["planned_after"] += 1
        tampered["table"][0]["delta"] = f"{tampered['table'][0]['planned_delta']:+d}"
        write_json(summary_path, tampered)
        with self.assertRaisesRegex(RuntimeError, "does not bind its graph delta"):
            self.manager.prepare(
                run_id=intake["run_id"],
                approval_id="graph-delta-summary-test-approval",
                backend=self.backend,
            )


if __name__ == "__main__":
    unittest.main()
