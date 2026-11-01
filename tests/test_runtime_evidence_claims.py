from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import seal_artifact
from dual_pipeline.runtime_evidence import (
    discover_inventory,
    evaluate_claim,
    validate_control_files,
)

RETIRED_EMBEDDING_BACKEND = "deterministic" + "-test"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _active_pair(backend: str, model_id: str, generation: str) -> dict:
    return {
        "activated_at": "2026-07-28T00:00:00Z",
        "embedding_backend": backend,
        "generation_id": generation,
        "graph_path": f"generations/{generation}/graph.sqlite",
        "graph_sha256": "1" * 64,
        "graph_snapshot": f"snapshot-{generation}",
        "model_id": model_id,
        "model_manifest_digest": None,
        "model_revision": "revision-1",
        "previous_generation_id": None,
        "production_qualified": False,
        "schema_version": "1.0.0",
        "sidecar_path": f"generations/{generation}/sidecar.sqlite",
        "sidecar_sha256": "2" * 64,
    }


class RuntimeEvidenceClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.external = self.root / "external-bge-runtime"
        self.evidence = self.root / "external-evidence"
        self.repo.mkdir()

        fixture_runtime = self.repo / ".local" / "runtime-fixture"
        bge = self.external
        for runtime, backend, model, generation in (
            (
                fixture_runtime,
                "metadata-only",
                "metadata-only",
                "generation-fixture",
            ),
            (
                bge,
                "flagembedding-bge-m3-dev",
                "BAAI/bge-m3",
                "generation-bge",
            ),
        ):
            pair = _active_pair(backend, model, generation)
            _write_json(runtime / "active_pair.json", pair)
            generation_dir = runtime / "generations" / generation
            generation_dir.mkdir(parents=True)
            (generation_dir / "graph.sqlite").write_bytes(b"graph")
            (generation_dir / "sidecar.sqlite").write_bytes(b"sidecar")

        _write_json(
            self.repo / "governance" / "bge-receipt.json",
            {
                "receipt_type": "bge-evidence",
                "execution": {
                    "runtime_root": str(self.external),
                    "evidence_root": str(self.evidence),
                },
            },
        )
        report = seal_artifact(
            {
                "contract_id": (
                    "urn:nee:dual-pipeline:retrieval-evidence-report:1.0.0"
                ),
                "schema_version": "1.0.0",
                "artifact_id": "retrieval-report-bge",
                "report_id": "retrieval-report-bge",
                "run_id": "run-query-bge",
                "validated_request_digest": "a" * 64,
                "query_plan_digest": "b" * 64,
                "coverage_manifest_digest": "c" * 64,
                "graph_snapshot": "snapshot-generation-bge",
                "graph_sha256": "d" * 64,
                "authorization_scope_digest": "e" * 64,
                "evidence_packet_digest": "f" * 64,
                "selected_record_ids_by_lane": {
                    "approved": ["retrieval-record-approved"],
                    "candidate": [],
                    "external_public": [],
                },
                "l2_input_record_ids_by_lane": {
                    "approved": ["retrieval-record-approved"],
                    "candidate": [],
                    "external_public": [],
                },
                "requirement_outcomes": [
                    {
                        "requirement_id": "requirement-bge",
                        "outcome": "covered",
                        "record_ids": ["retrieval-record-approved"],
                        "reason_code": None,
                    }
                ],
                "view_authorized": True,
                "extensions": {},
            }
        )
        report_path = self.evidence / "retrieval_evidence_report.json"
        _write_json(report_path, report)
        markdown_path = self.evidence / "retrieval_evidence.md"
        markdown_path.write_text("# Retrieval evidence\n", encoding="utf-8")
        _write_json(
            self.evidence / "query_summary.json",
            {
                "run_id": "run-query-bge",
                "status": "COMPLETED",
                "embedding_backend": "flagembedding-bge-m3-dev",
                "generation_id": "generation-bge",
                "production_qualified": False,
                "dense_executed": True,
                "sparse_executed": True,
                "colbert_executed": True,
                "approved_record_count": 20,
                "candidate_record_count": 10,
                "audit_status": "PASS",
                "x3_outcome": "X3D_ALLOW_FINISH",
                "learning_event_chain_digest": "0" * 64,
                "graph_sha256_before": "d" * 64,
                "graph_sha256_after": "d" * 64,
                "retrieval_evidence_report_digest": report[
                    "canonical_digest"
                ]["value"],
                "retrieval_evidence": {
                    "report_id": "retrieval-report-bge",
                    "report_digest": report["canonical_digest"]["value"],
                    "report_json_path": str(report_path),
                    "report_markdown_path": str(markdown_path),
                    "coverage_counts": {
                        "supported": 1,
                        "partial": 0,
                        "missing": 0,
                        "unauthorized": 0,
                        "conflicted": 0,
                    },
                    "selected_record_counts_by_lane": {
                        "approved": 1,
                        "candidate": 0,
                        "external_public": 0,
                    },
                    "l2_visible_record_count": 1,
                    "terminal_coverage_disposition": "COMPLETE",
                },
            },
        )
        _write_json(
            self.external
            / "retrieval_evaluations"
            / "retrieval-release-evaluation-release-receipt-bge.json",
            seal_artifact(
                {
                    "contract_id": (
                        "urn:nee:dual-pipeline:"
                        "retrieval-release-evaluation:1.0.0"
                    ),
                    "evaluation_id": "retrieval-release-evaluation-bge",
                    "execution_tier": "E3",
                    "status": "PASS",
                    "evaluated_at": "2026-07-28T00:00:00Z",
                    "prepared_receipt_id": "release-receipt-bge",
                    "prepared_receipt_digest": "3" * 64,
                    "suite_id": "retrieval-release-suite-bge",
                    "suite_digest": "4" * 64,
                    "acceptance_policy": {"policy_digest": "5" * 64},
                    "baseline_pair": {
                        "embedding_backend": "flagembedding-bge-m3-dev",
                        "generation_id": "generation-bge-baseline",
                        "graph_snapshot": "snapshot-bge-baseline",
                        "graph_sha256": "6" * 64,
                        "sidecar_sha256": "7" * 64,
                        "model_id": "BAAI/bge-m3",
                        "model_revision": "revision-1",
                        "model_manifest_digest": None,
                        "production_qualified": False,
                    },
                    "candidate_pair": {
                        "embedding_backend": "flagembedding-bge-m3-dev",
                        "generation_id": "generation-bge",
                        "graph_snapshot": "snapshot-bge",
                        "graph_sha256": "8" * 64,
                        "sidecar_sha256": "9" * 64,
                        "model_id": "BAAI/bge-m3",
                        "model_revision": "revision-1",
                        "model_manifest_digest": None,
                        "production_qualified": False,
                    },
                    "coverage": {"complete": True},
                    "aggregate": {"core": {"recall_at_k": 1}},
                    "gates": {"core_thresholds": True},
                }
            ),
        )
        _write_json(
            self.external
            / "retrieval_evaluation_suites"
            / "automatic"
            / "retrieval-release-suite-release-receipt-bge.json",
            seal_artifact(
                {
                    "contract_id": (
                        "urn:nee:dual-pipeline:retrieval-release-suite:1.0.0"
                    ),
                    "suite_id": "retrieval-release-suite-bge",
                    "execution_tier": "E3",
                    "acceptance_policy": {"policy_digest": "5" * 64},
                    "cases": [{"case_id": "case-bge"}],
                    "coverage": [],
                    "created_at": "2026-07-28T00:00:00Z",
                    "extensions": {
                        "com.partner_advisory.nee.automatic-suite": {
                            "source": "governance_owned_templates",
                            "prepared_receipt_id": "release-receipt-bge",
                            "prepared_receipt_digest": "3" * 64,
                            "generation_policy": {"policy_digest": "a" * 64},
                        }
                    },
                }
            ),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inventory_finds_multiple_runtime_roots_and_follows_receipts(self) -> None:
        inventory = discover_inventory(self.repo)
        backends = {
            pair["embedding_backend"] for pair in inventory["runtime_pairs"]
        }
        self.assertEqual(
            {"flagembedding-bge-m3-dev", "metadata-only"},
            backends,
        )
        labels = {
            pair["embedding_backend"]: pair["assurance_status"]
            for pair in inventory["runtime_pairs"]
        }
        self.assertEqual("development", labels["flagembedding-bge-m3-dev"])
        self.assertEqual("development", labels["metadata-only"])
        self.assertEqual(
            ["run-query-bge"],
            [item["run_id"] for item in inventory["query_evidence"]],
        )
        self.assertEqual(
            [str((self.evidence / "query_summary.json").resolve())],
            inventory["query_evidence"][0]["artifact_paths"],
        )
        query = inventory["query_evidence"][0]
        self.assertEqual("live_query", query["evidence_kind"])
        self.assertEqual("valid", query["retrieval_report_binding_status"])
        self.assertEqual("retrieval-report-bge", query["retrieval_report_id"])
        self.assertEqual("COMPLETE", query["retrieval_terminal_outcome"])
        self.assertEqual(1, inventory["query_observability"]["eligible_run_count"])
        self.assertEqual(
            "sealed_terminal_query_only",
            inventory["query_observability"]["admission_policy"],
        )
        self.assertEqual(
            ["retrieval-release-evaluation-bge"],
            [
                item["evaluation_id"]
                for item in inventory["retrieval_evaluations"]
            ],
        )
        self.assertEqual(
            ["retrieval-release-suite-bge"],
            [
                item["suite_id"]
                for item in inventory["retrieval_evaluation_suites"]
            ],
        )
        self.assertFalse(inventory["issues"])

    def test_repeated_run_id_isolated_by_runtime_scope(self) -> None:
        summary = json.loads(
            (self.evidence / "query_summary.json").read_text(encoding="utf-8")
        )
        second_runtime = self.root / "second-bge-runtime"
        for runtime in (self.external, second_runtime):
            _write_json(
                runtime / "active_pair.json",
                _active_pair(
                    "flagembedding-bge-m3-dev",
                    "BAAI/bge-m3",
                    "generation-bge",
                ),
            )
            generation_dir = runtime / "generations" / "generation-bge"
            generation_dir.mkdir(parents=True, exist_ok=True)
            (generation_dir / "graph.sqlite").write_bytes(b"graph")
            (generation_dir / "sidecar.sqlite").write_bytes(b"sidecar")
            _write_json(
                runtime / "runs" / "run-query-bge" / "query_summary.json",
                summary,
            )

        inventory = discover_inventory(self.repo, extra_roots=[second_runtime])
        conflicts = [
            issue
            for issue in inventory["issues"]
            if issue["code"] == "QUERY_EVIDENCE_CONFLICT"
        ]
        self.assertFalse(conflicts)
        scoped_queries = [
            item
            for item in inventory["query_evidence"]
            if item["run_id"] == "run-query-bge"
        ]
        self.assertEqual(3, len(scoped_queries))
        self.assertEqual(
            {
                str(self.external.resolve()),
                str(second_runtime.resolve()),
                None,
            },
            {item["runtime_root"] for item in scoped_queries},
        )

    def test_positive_bge_claims_are_evidence_bound(self) -> None:
        inventory = discover_inventory(self.repo)
        active = evaluate_claim(
            inventory,
            kind="backend-active",
            backend="bge-m3",
        )
        retrieval = evaluate_claim(
            inventory,
            kind="retrieval-present",
            backend="bge-m3",
        )
        self.assertTrue(active["supported"])
        self.assertTrue(retrieval["supported"])
        self.assertEqual("generation-bge", active["matching_runtime_pairs"][0]["generation_id"])
        self.assertEqual("run-query-bge", retrieval["matching_query_evidence"][0]["run_id"])
        self.assertEqual(
            "retrieval-release-evaluation-bge",
            retrieval["matching_retrieval_evaluations"][0]["evaluation_id"],
        )

    def test_l1_terminal_claim_requires_a_summary_bound_terminal(self) -> None:
        summary_path = self.evidence / "query_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        terminal = seal_artifact(
            {
                "contract_id": "urn:nee:dual-pipeline:query-plan-terminal:1.0.0",
                "schema_version": "1.0.0",
                "terminal_reason": "COMPLETED",
                "coverage_complete": True,
                "permitted_next_stage": "L2D",
                "final_evidence_report_digest": summary["retrieval_evidence_report_digest"],
                "graph_sha256_before": "d" * 64,
                "graph_sha256_after": "d" * 64,
            }
        )
        terminal_path = self.evidence / "run-query-bge" / "query_plan_terminal.json"
        _write_json(terminal_path, terminal)
        summary["l1_controller"] = {
            "mode": "bounded",
            "terminal_digest": terminal["canonical_digest"]["value"],
            "final_evidence_report_digest": terminal["final_evidence_report_digest"],
        }
        _write_json(summary_path, summary)
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(inventory, kind="l1-terminal-present", backend="bge-m3")
        self.assertTrue(result["supported"])
        self.assertEqual("run-query-bge", result["matching_l1_terminals"][0]["run_id"])
        negative = evaluate_claim(
            inventory,
            kind="l1-terminal-absent",
            backend="bge-m3",
            scope_roots=[self.root],
            complete_scope=True,
        )
        self.assertFalse(negative["supported"])
        self.assertIn("MATCHING_L1_TERMINAL_EXISTS", negative["reason_codes"])

    def test_historical_query_without_report_is_marked_legacy_absent(self) -> None:
        legacy = self.evidence / "legacy" / "query_summary.json"
        _write_json(
            legacy,
            {
                "run_id": "run-query-legacy",
                "status": "COMPLETED",
                "embedding_backend": "flagembedding-bge-m3-dev",
                "generation_id": "generation-bge",
                "x3_outcome": "X3D_ALLOW_FINISH",
            },
        )
        inventory = discover_inventory(self.repo)
        record = next(
            item
            for item in inventory["query_evidence"]
            if item["run_id"] == "run-query-legacy"
        )
        self.assertEqual(
            "legacy_absent",
            record["retrieval_report_binding_status"],
        )
        self.assertIsNone(record["retrieval_report_digest"])
        self.assertFalse(record["shadow_observability_eligible"])

    def test_negative_claim_requires_explicit_complete_scope(self) -> None:
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(
            inventory,
            kind="backend-inactive",
            backend="bge-m3",
        )
        self.assertFalse(result["supported"])
        self.assertIn(
            "NEGATIVE_CLAIM_REQUIRES_EXPLICIT_SCOPE_ROOTS",
            result["reason_codes"],
        )
        self.assertIn(
            "NEGATIVE_CLAIM_REQUIRES_COMPLETE_SCOPE_ATTESTATION",
            result["reason_codes"],
        )

    def test_negative_claim_fails_when_matching_evidence_exists(self) -> None:
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(
            inventory,
            kind="retrieval-absent",
            backend="bge-m3",
            scope_roots=[self.root],
            complete_scope=True,
        )
        self.assertFalse(result["supported"])
        self.assertIn("MATCHING_QUERY_EVIDENCE_EXISTS", result["reason_codes"])
        self.assertIn(
            "MATCHING_RETRIEVAL_EVALUATION_EXISTS",
            result["reason_codes"],
        )

    def test_negative_claim_can_be_scoped_to_nonmatching_runtime(self) -> None:
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(
            inventory,
            kind="backend-inactive",
            backend="bge-m3",
            scope_roots=[self.repo / ".local" / "runtime-fixture"],
            complete_scope=True,
        )
        self.assertTrue(result["supported"])
        self.assertEqual(1, result["searched_runtime_count"])

    def test_backend_inactive_ignores_unrelated_bge_query_receipt_defect(self) -> None:
        (self.evidence / "retrieval_evidence.md").unlink()
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(
            inventory,
            kind="backend-inactive",
            backend=RETIRED_EMBEDDING_BACKEND,
            scope_roots=[self.root],
            complete_scope=True,
        )
        self.assertTrue(result["supported"])
        self.assertEqual([], result["blocking_issues"])
        self.assertEqual(1, len(result["nonblocking_issues"]))
        self.assertEqual(
            "QUERY_RETRIEVAL_REPORT_INVALID",
            result["nonblocking_issues"][0]["code"],
        )

    def test_backend_inactive_fails_closed_for_invalid_active_pair(self) -> None:
        invalid_pair = self.repo / ".local" / "invalid-runtime" / "active_pair.json"
        invalid_pair.parent.mkdir(parents=True)
        invalid_pair.write_text("not-json", encoding="utf-8")
        inventory = discover_inventory(self.repo)
        result = evaluate_claim(
            inventory,
            kind="backend-inactive",
            backend=RETIRED_EMBEDDING_BACKEND,
            scope_roots=[self.root],
            complete_scope=True,
        )
        self.assertFalse(result["supported"])
        self.assertEqual(
            ["NEGATIVE_CLAIM_BLOCKED_BY_INCOMPLETE_OR_INVALID_EVIDENCE"],
            result["reason_codes"],
        )
        self.assertEqual("ACTIVE_PAIR_INVALID", result["blocking_issues"][0]["code"])

    def test_repository_control_files_are_enforced(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        self.assertEqual([], validate_control_files(repository_root))


if __name__ == "__main__":
    unittest.main()
