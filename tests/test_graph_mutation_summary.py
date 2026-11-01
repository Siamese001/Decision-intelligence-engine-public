#!/usr/bin/env python3
"""Unit tests for the mandatory graph mutation summary."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.completion import IngestionCompletionGuard
from dual_pipeline.graph_delta import (
    build_graph_mutation_summary,
)
from dual_pipeline.release import ReleaseManager

ROOT = Path(__file__).resolve().parents[1]


class GraphMutationSummaryTests(unittest.TestCase):
    def test_idempotent_noop_mutation_summary(self) -> None:
        summary = build_graph_mutation_summary(
            generation_before="generation-bootstrap-91cf84562c5425b0f92fce74a07c",
            generation_after="generation-bootstrap-91cf84562c5425b0f92fce74a07c",
            graph_sha256_before="a780950fffe48e3dbc3e6509f7af8cfd5afef2e2117e0b3437f2afeb11a8129f",
            graph_sha256_after="a780950fffe48e3dbc3e6509f7af8cfd5afef2e2117e0b3437f2afeb11a8129f",
            total_operations=0,
            status="IDEMPOTENT_NOOP",
        )

        self.assertEqual(summary["schema_version"], "1.0.0")
        self.assertEqual(summary["summary_type"], "graph_mutation_summary")
        self.assertEqual(summary["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(summary["total_operations"], 0)
        self.assertEqual(summary["net_changes"]["sources"], 0)
        self.assertEqual(summary["net_changes"]["entities"], 0)
        self.assertEqual(summary["net_changes"]["canonical_assertions"], 0)
        self.assertEqual(summary["entities_created"], [])
        self.assertEqual(summary["sources_added"], [])
        self.assertIn("No graph mutations: 0 operations applied", summary["summary_text"])
        self.assertIn("GRAPH MUTATION SUMMARY", summary["human_readable_banner"])
        self.assertIn("Outcome:              IDEMPOTENT_NOOP", summary["human_readable_banner"])
        self.assertIn("None (Graph and Sidecar remain identical)", summary["human_readable_banner"])

        md_table = summary["markdown_breakdown_table"]
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", md_table)
        self.assertIn("| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |", md_table)
        self.assertIn("| `node` | — | — | `+0` | Idempotent no-op: zero nodes created or modified. |", md_table)
        self.assertIn("| `source` | — | — | `+0` | Idempotent no-op: source already ingested. |", md_table)
        self.assertIn("| `dense_vector` (sidecar) | — | — | `+0` | Idempotent no-op: neural vector embeddings unchanged. |", md_table)
        self.assertIn("| `retrieval_record` (sidecar) | — | — | `+0` | Idempotent no-op: retrieval corpus unchanged. |", md_table)

    def test_positive_mutation_summary_from_actual_delta(self) -> None:
        mock_actual_delta = {
            "schema_version": "1.0.0",
            "summary_type": "actual_graph_delta",
            "status": "PASS",
            "table": [
                {"component_key": "source", "actual_delta": 1},
                {"component_key": "source_version", "actual_delta": 1},
                {"component_key": "evidence", "actual_delta": 5},
                {"component_key": "external_concept", "actual_delta": 1},
                {"component_key": "internal_entity", "actual_delta": 2},
                {"component_key": "candidate_assertion", "actual_delta": 3},
                {"component_key": "canonical_assertion", "actual_delta": 4},
                {"component_key": "superseded_assertion", "actual_delta": 0},
                {"component_key": "candidate_search_document", "actual_delta": 0},
                {"component_key": "audit_event", "actual_delta": 16},
            ],
        }

        mock_change_set = {
            "change_set_id": "cs-test-01",
            "operations": [
                {"operation_id": "op-1", "operation": "create_entity", "target_id": "person:stephan_marceau"},
                {"operation_id": "op-2", "operation": "create_entity", "target_id": "org:partner_advisory"},
            ],
            "extensions": {
                "com.partner_advisory.nee.source": {
                    "source_id": "source_partner_advisory_nea_2026",
                    "name": "PartnerAdvisory NEA Briefing 2026",
                },
                "com.partner_advisory.nee.operation-payloads": {
                    "op-1": {
                        "entity_kind": "internal",
                        "candidate": {"canonical_name": "Cameron Mercer", "entity_type": "person"},
                    },
                    "op-2": {
                        "entity_kind": "external",
                        "candidate": {"canonical_name": "PartnerAdvisory", "entity_type": "org"},
                    },
                },
            },
        }

        mock_sidecar_delta = {
            "table": [
                {"component_key": "dense_vector", "table": "dense_vector", "label": "Dense vector embeddings (1024-dim BGE-M3 float32)", "baseline": 977, "actual_after": 979, "actual_delta": 2},
                {"component_key": "sparse_weight", "table": "sparse_weight", "label": "Sparse token weights (BGE-M3 learned lexical)", "baseline": 44905, "actual_after": 45010, "actual_delta": 105},
                {"component_key": "colbert_token", "table": "colbert_token", "label": "ColBERT multi-vectors (contextual token embeddings)", "baseline": 31228, "actual_after": 31300, "actual_delta": 72},
                {"component_key": "retrieval_record", "table": "retrieval_record", "label": "Retrieval records (clearance-approved corpus)", "baseline": 977, "actual_after": 979, "actual_delta": 2},
            ]
        }

        summary = build_graph_mutation_summary(
            generation_before="gen-001",
            generation_after="gen-002",
            graph_sha256_before="1111111111111111111111111111111111111111111111111111111111111111",
            graph_sha256_after="2222222222222222222222222222222222222222222222222222222222222222",
            total_operations=16,
            actual_graph_delta=mock_actual_delta,
            sidecar_delta=mock_sidecar_delta,
            change_set=mock_change_set,
        )

        self.assertEqual(summary["status"], "GRAPH_MUTATED")
        self.assertEqual(summary["total_operations"], 16)
        self.assertEqual(summary["net_changes"]["sources"], 1)
        self.assertEqual(summary["net_changes"]["entities"], 3)
        self.assertEqual(summary["net_changes"]["canonical_assertions"], 4)
        self.assertEqual(summary["net_changes"]["candidate_assertions"], 3)
        self.assertEqual(summary["net_changes"]["evidence_items"], 5)
        self.assertEqual(summary["net_changes"]["dense_vectors"], 2)
        self.assertEqual(summary["net_changes"]["sparse_weights"], 105)
        self.assertEqual(summary["net_changes"]["colbert_tokens"], 72)
        self.assertEqual(summary["net_changes"]["retrieval_records"], 2)

        self.assertEqual(len(summary["entities_created"]), 2)
        self.assertEqual(summary["entities_created"][0]["id"], "person:stephan_marceau")
        self.assertEqual(summary["entities_created"][0]["name"], "Cameron Mercer")
        self.assertEqual(summary["entities_created"][0]["type"], "person")

        self.assertEqual(len(summary["sources_added"]), 1)
        self.assertEqual(summary["sources_added"][0]["source_id"], "source_partner_advisory_nea_2026")

        self.assertIn("Graph mutated: +1 sources, +3 entities, +4 canonical facts", summary["summary_text"])
        banner = summary["human_readable_banner"]
        self.assertIn("GRAPH MUTATION SUMMARY", banner)
        self.assertIn("gen-001 -> gen-002", banner)
        self.assertIn("Cameron Mercer", banner)
        self.assertIn("source_partner_advisory_nea_2026", banner)

        md_table = summary["markdown_breakdown_table"]
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", md_table)
        self.assertIn("| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |", md_table)
        self.assertIn("| `source` (source) |", md_table)
        self.assertIn("| `node` (internal_entity) |", md_table)
        self.assertIn("| `dense_vector` (sidecar) | 977 | 979 | `+2` | Dense vector embeddings (1024-dim BGE-M3 float32) (+2 generated) |", md_table)
        self.assertIn("| `retrieval_record` (sidecar) | 977 | 979 | `+2` | Retrieval records (clearance-approved corpus) (+2 generated) |", md_table)

    def test_completion_guard_noop_includes_mutation_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-summary-test-") as temp_dir:
            runtime = Path(temp_dir) / "runtime"
            runtime.mkdir(parents=True)
            graph = ROOT / "graph" / "nee_graph.sqlite"
            _ = ReleaseManager(runtime, graph)

            # Pre-seed active pair so real BGE download is not required
            gen_id = "generation-bootstrap-91cf84562c5425b0f92fce74a07c"
            gen_dir = runtime / "generations" / gen_id
            gen_dir.mkdir(parents=True, exist_ok=True)
            from dual_pipeline.core import exact_file_copy, read_json, write_json
            existing_gen = ROOT / ".local" / "dual_pipeline" / "generations" / gen_id
            if not existing_gen.exists():
                for alt in [ROOT.parent.parent, ROOT.parent]:
                    cand = alt / ".local" / "dual_pipeline" / "generations" / gen_id
                    if cand.exists():
                        existing_gen = cand
                        break
            exact_file_copy(existing_gen / "graph.sqlite", gen_dir / "graph.sqlite")
            exact_file_copy(existing_gen / "sidecar.sqlite", gen_dir / "sidecar.sqlite")
            existing_pair = read_json(existing_gen / "pair_manifest.json")
            pair = dict(existing_pair)
            pair["graph_path"] = f"generations/{gen_id}/graph.sqlite"
            pair["sidecar_path"] = f"generations/{gen_id}/sidecar.sqlite"
            write_json(runtime / "active_pair.json", pair)
            write_json(gen_dir / "pair_manifest.json", pair)

            guard = IngestionCompletionGuard(runtime, graph)

            # Open a directive
            directive_id = "directive-noop-summary-test"
            package_path = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
            opened = guard.open(
                directive_id=directive_id,
                scope="No-op test directive",
                packages=[package_path],
            )
            guard.record_human_decision(
                directive_id,
                decision="APPROVE",
                classification_digest=opened["classification"]["classification_digest"],
                reviewer="test-operator",
                rationale="Approve test",
            )

            # Create a mock no-op run
            run_id = "run-noop-001"
            run_dir = runtime / "runs" / run_id
            run_dir.mkdir(parents=True)
            write_json(run_dir / "intake_summary.json", {
                "run_id": run_id,
                "status": "COMPLETED",
                "graph_operation_count": 0,
            })
            write_json(run_dir / "candidate_bundle.json", {
                "trace_id": "tr-001",
                "source_id": "src-001",
                "source_version_id": "srcver-001",
            })

            # Mock stage audits so validation succeeds
            recorder_payload_dir = run_dir / "learning_events"
            recorder_payload_dir.mkdir(parents=True)
            mock_evidence = [
                {
                    "run_id": run_id,
                    "trace_id": "tr-001",
                    "source_id": "src-001",
                    "package_file_sha256": "pkgsha",
                    "graph_sha256_before": pair["graph_sha256"],
                    "graph_sha256_after": pair["graph_sha256"],
                    "graph_operation_count": 0,
                    "audit_status": "PASS",
                }
            ]
            with mock.patch.object(
                guard, "_validate_release_request_state", return_value=mock_evidence
            ):
                result = guard.complete_noop(directive_id, run_ids=[run_id])

            self.assertEqual(result["status"], "PASS")
            self.assertIn("mutation_summary", result)
            summary = result["mutation_summary"]
            self.assertEqual(summary["status"], "IDEMPOTENT_NOOP")
            self.assertEqual(summary["total_operations"], 0)
            self.assertIn("No graph mutations: 0 operations applied", summary["summary_text"])
            self.assertIn("mutation_summary", result["completion"])
            self.assertEqual(result["completion"]["mutation_summary"]["status"], "IDEMPOTENT_NOOP")
            self.assertIn("human_readable_banner", summary)

    def test_completion_guard_positive_mutation_includes_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-summary-pos-") as temp_dir:
            runtime = Path(temp_dir) / "runtime"
            runtime.mkdir(parents=True)
            graph = ROOT / "graph" / "nee_graph.sqlite"
            _ = ReleaseManager(runtime, graph)

            # Pre-seed active pair
            gen_id = "generation-bootstrap-91cf84562c5425b0f92fce74a07c"
            gen_dir = runtime / "generations" / gen_id
            gen_dir.mkdir(parents=True, exist_ok=True)
            from dual_pipeline.core import digest, exact_file_copy, read_json, write_json
            existing_gen = ROOT / ".local" / "dual_pipeline" / "generations" / gen_id
            if not existing_gen.exists():
                for alt in [ROOT.parent.parent, ROOT.parent]:
                    cand = alt / ".local" / "dual_pipeline" / "generations" / gen_id
                    if cand.exists():
                        existing_gen = cand
                        break
            exact_file_copy(existing_gen / "graph.sqlite", gen_dir / "graph.sqlite")
            exact_file_copy(existing_gen / "sidecar.sqlite", gen_dir / "sidecar.sqlite")
            existing_pair = read_json(existing_gen / "pair_manifest.json")
            pair = dict(existing_pair)
            pair["graph_path"] = f"generations/{gen_id}/graph.sqlite"
            pair["sidecar_path"] = f"generations/{gen_id}/sidecar.sqlite"
            write_json(runtime / "active_pair.json", pair)
            write_json(gen_dir / "pair_manifest.json", pair)

            guard = IngestionCompletionGuard(runtime, graph)

            directive_id = "directive-pos-summary-test"
            package_path = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
            opened = guard.open(
                directive_id=directive_id,
                scope="Positive mutation test directive",
                packages=[package_path],
            )
            approved = guard.record_human_decision(
                directive_id,
                decision="APPROVE",
                classification_digest=opened["classification"]["classification_digest"],
                reviewer="test-operator",
                rationale="Approve test",
            )
            approval_id = approved["approval"]["approval_id"]

            # Set up next generation
            next_gen_id = "generation-next-001"
            next_gen_dir = runtime / "generations" / next_gen_id
            next_gen_dir.mkdir(parents=True, exist_ok=True)
            exact_file_copy(gen_dir / "graph.sqlite", next_gen_dir / "graph.sqlite")
            exact_file_copy(gen_dir / "sidecar.sqlite", next_gen_dir / "sidecar.sqlite")


            next_pair = dict(pair)
            next_pair["generation_id"] = next_gen_id
            next_pair["graph_path"] = f"generations/{next_gen_id}/graph.sqlite"
            next_pair["sidecar_path"] = f"generations/{next_gen_id}/sidecar.sqlite"
            next_pair["graph_sha256"] = "ffff" * 16
            write_json(next_gen_dir / "pair_manifest.json", next_pair)
            write_json(runtime / "active_pair.json", next_pair)

            receipt_id = "release-receipt-pos-001"
            activation_id = "activation-pos-001"
            releases_dir = runtime / "releases"
            releases_dir.mkdir(parents=True, exist_ok=True)

            delta = {
                "schema_version": "1.0.0",
                "table": [
                    {
                        "component_key": "internal_entity",
                        "actual_delta": 2,
                    },
                    {
                        "component_key": "canonical_assertion",
                        "actual_delta": 6,
                    },
                ],
                "operation_count": 8,
            }

            receipt = {
                "schema_version": "1.0.0",
                "receipt_id": receipt_id,
                "status": "PREPARED",
                "run_id": "run-pos-001",
                "trace_id": "tr-pos-001",
                "source_id": "src-pos-001",
                "approval_id": approval_id,
                "component_runs": [
                    {
                        "run_id": "run-pos-001",
                        "source_id": "src-pos-001",
                        "trace_id": "tr-pos-001",
                        "operation_ids": [f"op-{i}" for i in range(8)],
                    }
                ],
                "previous_pair": pair,
                "next_pair": next_pair,
                "graph_write_receipt": {"operation_count": 8},
                "graph_delta_summary": delta,
                "pair_validation": {"status": "PASS"},
                "production_qualified": False,
            }
            receipt["receipt_digest"] = digest(receipt)
            receipt_path = releases_dir / f"{receipt_id}.json"
            write_json(receipt_path, receipt)

            activation = {
                "schema_version": "1.0.0",
                "activation_id": activation_id,
                "status": "ACTIVE",
                "receipt_id": receipt_id,
                "run_id": "run-pos-001",
                "component_runs": [{"run_id": "run-pos-001", "source_id": "src-pos-001", "trace_id": "tr-pos-001"}],
                "previous_generation_id": gen_id,
                "active_pair": next_pair,
                "activated_at": "2026-09-10T12:00:00Z",
            }
            activation["receipt_digest"] = digest(activation)
            activation_path = releases_dir / f"activation-{next_gen_id}.json"
            write_json(activation_path, activation)

            mock_evidence = {
                "run_id": "run-pos-001",
                "trace_id": "tr-pos-001",
                "source_id": "src-pos-001",
                "package_file_sha256": opened["packages"][0]["sha256"],
                "graph_sha256_before": pair["graph_sha256"],
                "graph_sha256_after": pair["graph_sha256"],
                "graph_operation_count": 8,
                "audit_status": "PASS",
            }

            with (
                mock.patch.object(guard, "_run_evidence", return_value=mock_evidence),
                mock.patch.object(guard.manager, "validate_pair", return_value={"status": "PASS"}),
            ):
                result = guard.complete(directive_id, receipt_path=receipt_path)

            self.assertEqual(result["status"], "PASS")
            self.assertIn("mutation_summary", result)
            summary = result["mutation_summary"]
            self.assertEqual(summary["status"], "GRAPH_MUTATED")
            self.assertEqual(summary["total_operations"], 8)
            self.assertIn("across 8 operations", summary["summary_text"])
            self.assertIn("human_readable_banner", summary)
            banner = summary["human_readable_banner"]
            self.assertIn("Entities created", banner)
            self.assertIn("2", banner)
            self.assertIn("Canonical facts", banner)
            self.assertIn("6", banner)
            self.assertIn("mutation_summary", result["completion"])

    def test_cli_main_prints_human_readable_banner_to_stderr(self) -> None:
        import io
        import json
        from unittest.mock import patch

        from dual_pipeline.cli.engine import main

        dummy_summary = {
            "summary_type": "graph_mutation_summary",
            "status": "GRAPH_MUTATED",
            "total_operations": 5,
            "human_readable_banner": "=== DUMMY MUTATION BANNER ===",
        }
        mock_result = {
            "status": "PASS",
            "mutation_summary": dummy_summary,
        }

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with (
            patch("sys.stdout", stdout_buf),
            patch("sys.stderr", stderr_buf),
            patch("dual_pipeline.cli.engine.execute", return_value=mock_result),
            patch("sys.argv", ["nee", "validate-runtime"]),
        ):
            main()

        self.assertIn("=== DUMMY MUTATION BANNER ===", stderr_buf.getvalue())
        parsed_stdout = json.loads(stdout_buf.getvalue())
        self.assertEqual(parsed_stdout["status"], "PASS")
        self.assertEqual(parsed_stdout["mutation_summary"]["total_operations"], 5)

    def test_mutation_summary_from_actual_sidecar_sqlite_files(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory(prefix="nee-sidecar-summary-test-") as temp_dir:
            temp_path = Path(temp_dir)
            before_sidecar = temp_path / "before_sidecar.sqlite"
            after_sidecar = temp_path / "after_sidecar.sqlite"

            def init_sidecar(path: Path, num_records: int):
                conn = sqlite3.connect(path)
                conn.execute("""CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY)""")
                conn.execute("""CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dims INTEGER, vector BLOB)""")
                conn.execute("""CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))""")
                conn.execute("""CREATE TABLE colbert_token (record_id TEXT, pos INTEGER, token TEXT, dims INTEGER, vector BLOB, PRIMARY KEY (record_id, pos))""")
                for i in range(num_records):
                    rid = f"rec-{i}"
                    conn.execute("INSERT INTO retrieval_record VALUES (?)", (rid,))
                    conn.execute("INSERT INTO dense_vector VALUES (?, 1024, X'00')", (rid,))
                    conn.execute("INSERT INTO sparse_weight VALUES (?, 'token', 1.0)", (rid,))
                    conn.execute("INSERT INTO colbert_token VALUES (?, 0, 'tok', 128, X'00')", (rid,))
                conn.commit()
                conn.close()

            init_sidecar(before_sidecar, 5)
            init_sidecar(after_sidecar, 8)

            summary = build_graph_mutation_summary(
                generation_before="gen-001",
                generation_after="gen-002",
                before_sidecar_path=before_sidecar,
                after_sidecar_path=after_sidecar,
                total_operations=3,
                status="GRAPH_MUTATED",
            )

            self.assertEqual(summary["net_changes"]["dense_vectors"], 3)
            self.assertEqual(summary["net_changes"]["sparse_weights"], 3)
            self.assertEqual(summary["net_changes"]["colbert_tokens"], 3)
            self.assertEqual(summary["net_changes"]["retrieval_records"], 3)

            md = summary["markdown_breakdown_table"]
            self.assertIn("| `dense_vector` (sidecar) | 5 | 8 | `+3` | Dense vector embeddings (1024-dim BGE-M3 float32) (+3 generated) |", md)
            self.assertIn("| `retrieval_record` (sidecar) | 5 | 8 | `+3` | Retrieval records (clearance-approved corpus) (+3 generated) |", md)


if __name__ == "__main__":
    unittest.main()
