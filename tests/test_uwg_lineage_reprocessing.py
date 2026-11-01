"""
Unit tests for Wave 3: UWG Lineage Bridge, Dual-State Reprocessing Protocol,
and Mandatory 14-Table Granular Graph DB Delta Accounting.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.uwg_core import (
    MANDATORY_DELTA_TABLES,
    AssertionReconciler,
    SourceLineageBridge,
    compute_granular_delta_table,
    format_delta_table_markdown,
)


class UWGLineageReprocessingTests(unittest.TestCase):
    def test_detect_prior_live_session(self) -> None:
        # 1. Metadata explicit detection
        meta = {
            "source_session_id": "session-live-101",
            "live_run_id": "run-live-101",
        }
        detected = SourceLineageBridge.detect_prior_live_session(meta)
        self.assertIsNotNone(detected)
        self.assertEqual(detected["parent_session_id"], "session-live-101")
        self.assertEqual(detected["supersedes_run_id"], "run-live-101")
        self.assertEqual(detected["detection_method"], "metadata_explicit")

        # 2. Sidecar file detection
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_file = Path(tmpdir) / "meeting_recording.wav"
            wav_file.write_bytes(b"RIFF" + b"\x00" * 40)

            manifest_file = Path(tmpdir) / "meeting_recording.manifest.json"
            manifest_file.write_text('{"session_id": "session-live-sidecar-202", "run_id": "run-202"}')

            detected_sidecar = SourceLineageBridge.detect_prior_live_session(
                metadata={},
                file_path=str(wav_file),
            )
            self.assertIsNotNone(detected_sidecar)
            self.assertEqual(detected_sidecar["parent_session_id"], "session-live-sidecar-202")
            self.assertEqual(detected_sidecar["supersedes_run_id"], "run-202")
            self.assertEqual(detected_sidecar["detection_method"], "sidecar_file")

        # 3. None when standalone
        self.assertIsNone(SourceLineageBridge.detect_prior_live_session({}))

    def test_build_superseding_run_plan(self) -> None:
        plan = SourceLineageBridge.build_superseding_run_plan(
            source_id="source:meeting:eng_sync",
            prior_run_id="run-live-01",
            new_run_id="run-batch-02",
            prior_version=1,
        )
        self.assertEqual(plan["source_id"], "source:meeting:eng_sync")
        self.assertEqual(plan["prior_version_number"], 1)
        self.assertEqual(plan["new_version_number"], 2)
        self.assertEqual(plan["supersedes_run_id"], "run-live-01")
        self.assertEqual(plan["authority_rank"], 8)

    def test_assertion_reconciler(self) -> None:
        prior_assertions = [
            {
                "assertion_id": "a-live-1",
                "subject_node_id": "project:nexus",
                "predicate": "has_status",
                "literal_claim": "in_progress",
            },
            {
                "assertion_id": "a-live-2",
                "subject_node_id": "person:unknown_1",
                "predicate": "assigned_role",
                "literal_claim": "lead_architect",
            },
            {
                "assertion_id": "a-live-3",
                "subject_node_id": "system:unifier",
                "predicate": "will_retire",
                "literal_claim": "Q4",
            },
        ]

        new_assertions = [
            # Exactly matches a-live-1
            {
                "assertion_id": "a-batch-1",
                "subject_node_id": "project:nexus",
                "predicate": "has_status",
                "literal_claim": "in_progress",
            },
            # Refines a-live-2 with verified speaker identity
            {
                "assertion_id": "a-batch-2",
                "subject_node_id": "person:amit_ayer",
                "predicate": "assigned_role",
                "literal_claim": "lead_architect",
            },
        ]

        res = AssertionReconciler.reconcile(prior_assertions, new_assertions)

        self.assertEqual(res["superseded_count"], 0)
        self.assertEqual(res["retired_count"], 2)  # a-live-2 and a-live-3 not matched in new
        self.assertEqual(res["promoted_count"], 2)

        # Confirm assertion matching
        prior_map = {a["assertion_id"]: a for a in res["reconciled_prior"]}
        self.assertEqual(prior_map["a-live-1"]["status"], "confirmed")
        self.assertEqual(prior_map["a-live-2"]["status"], "retired")
        self.assertEqual(prior_map["a-live-3"]["status"], "retired")

    def test_compute_granular_delta_table(self) -> None:
        baseline = {t: 10 for t in MANDATORY_DELTA_TABLES}
        # Reprocessed run: node count MUST remain strictly +0 (zero duplicate nodes)
        activated = dict(baseline)
        activated["node"] = 10  # Delta = 0
        activated["source"] = 10  # Delta = 0
        activated["source_version"] = 11  # Delta = +1
        activated["extraction_run"] = 11  # Delta = +1
        activated["evidence_item"] = 35  # Delta = +25
        activated["assertion"] = 14  # Delta = +4

        delta_rows = compute_granular_delta_table(baseline, activated)
        self.assertEqual(len(delta_rows), 14)

        row_map = {r["sqlite_table"]: r for r in delta_rows}
        self.assertEqual(row_map["node"]["delta"], 0)
        self.assertEqual(row_map["source_version"]["delta"], 1)
        self.assertEqual(row_map["extraction_run"]["delta"], 1)
        self.assertEqual(row_map["evidence_item"]["delta"], 25)

        # Markdown formatting check
        md = format_delta_table_markdown(delta_rows)
        self.assertIn("| `node` | 10 | 10 | **+0** |", md)
        self.assertIn("| `source_version` | 10 | 11 | **+1** |", md)


if __name__ == "__main__":
    unittest.main()
