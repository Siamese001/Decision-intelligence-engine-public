#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "validate_dual_pipeline_contracts.py"
SPEC = importlib.util.spec_from_file_location("dual_pipeline_contracts", TOOL_PATH)
assert SPEC and SPEC.loader
contracts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contracts
SPEC.loader.exec_module(contracts)

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
VALID = CONTRACT_ROOT / "fixtures" / "valid"
INVALID = CONTRACT_ROOT / "fixtures" / "invalid"
EXPECTED_RUNTIME_HASHES = {
    "graph/nee_graph.sqlite": "a780950fffe48e3dbc3e6509f7af8cfd5afef2e2117e0b3437f2afeb11a8129f",
    "database/canonical_database.json": "c70804183e3d691a15b6a44dc75a2ca5eb36bb8c3b89c4ebf275267e1255480c",
    "ingestion/pipeline.py": "6196f377915ed7723f3113b378bfb8c5eb803ca0c7da2c93c4cc68057d6d75c0",
    "ingestion/input_schema.json": "6bd87485999cd46740627e8389eee55c85f1b891ccff48667068680d0180ef7a",
    "release_state.json": "0836ad81d95f1710b3070254362c4dc333fd62678dae3e6d0fd18b4caa2e5219",
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resign(value: dict[str, Any]) -> dict[str, Any]:
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": contracts.canonical_artifact_digest(value),
    }
    return value


class DualPipelineWave1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = contracts.SchemaRegistry(CONTRACT_ROOT / "schemas")
        cls.registry.load()

    def assert_rejected(self, artifact: dict[str, Any], code: str) -> None:
        with self.assertRaises(contracts.ValidationFailure) as raised:
            contracts.validate_artifact(artifact, self.registry)
        self.assertEqual(raised.exception.issue.code, code)

    def test_01_complete_contract_suite_passes(self) -> None:
        report = contracts.run_validation(CONTRACT_ROOT)
        self.assertEqual(report["status"], "PASS")
        # The contract surface is intentionally additive as new governed
        # artifacts are introduced. Keep a floor that detects accidental
        # removal, while deriving the relational invariants rather than
        # pinning this older Wave 1 test to a stale exact inventory count.
        self.assertGreaterEqual(report["schema_count"], 93)
        self.assertEqual(report["artifact_contract_count"], report["schema_count"] - 1)
        self.assertEqual(report["fixtures"]["valid_count"], report["artifact_contract_count"])
        self.assertGreaterEqual(report["fixtures"]["invalid_count"], 54)

    def test_02_every_artifact_contract_has_exact_version_and_fixture(self) -> None:
        artifacts = set(self.registry.by_id) - contracts.SUB_OBJECT_SCHEMA_IDS
        fixture_contracts = {load(path)["contract_id"] for path in VALID.glob("*.json")}
        self.assertEqual(artifacts, fixture_contracts)
        for schema_id in artifacts:
            schema = self.registry.by_id[schema_id]
            self.assertEqual(
                schema["properties"]["schema_version"]["const"],
                schema_id.rsplit(":", 1)[-1],
            )

    def test_03_unknown_fields_fail_closed(self) -> None:
        artifact = load(VALID / "route-decision.json")
        artifact["unexpected_field"] = "not allowed"
        self.assert_rejected(resign(artifact), "SCHEMA_UNKNOWN_FIELD")

    def test_04_explicit_namespaced_extensions_are_allowed(self) -> None:
        artifact = load(VALID / "route-decision.json")
        artifact["extensions"] = {"com.partner_advisory.test-field": {"value": 1}}
        contracts.validate_artifact(resign(artifact), self.registry)

    def test_05_unscoped_extension_key_is_rejected(self) -> None:
        artifact = load(VALID / "route-decision.json")
        artifact["extensions"] = {"future": true_value()}
        self.assert_rejected(resign(artifact), "SCHEMA_PATTERN")

    def test_06_canonical_digest_is_order_independent(self) -> None:
        artifact = load(VALID / "pdlc-query-plan.json")
        reversed_artifact = dict(reversed(list(artifact.items())))
        self.assertEqual(
            contracts.canonical_artifact_digest(artifact),
            contracts.canonical_artifact_digest(reversed_artifact),
        )
        contracts.validate_artifact(reversed_artifact, self.registry)

    def test_07_tampered_artifact_digest_is_rejected(self) -> None:
        artifact = load(VALID / "copilot-meeting.json")
        artifact["meeting"]["title"] = "Tampered title"
        self.assert_rejected(artifact, "DIGEST_MISMATCH")

    def test_08_copilot_statements_require_locators(self) -> None:
        artifact = load(INVALID / "copilot-missing-locator.json")
        self.assert_rejected(artifact, "SCHEMA_REQUIRED")

    def test_09_architecture_components_require_regions(self) -> None:
        artifact = load(INVALID / "architecture-missing-region.json")
        self.assert_rejected(artifact, "SCHEMA_REQUIRED")

    def test_10_route_selection_has_exactly_one_route_family(self) -> None:
        artifact = load(VALID / "route-decision.json")
        artifact["intake_route"] = "NEW_ASSERTION"
        self.assert_rejected(resign(artifact), "SEMANTIC_ROUTE_CARDINALITY")

    def test_11_naked_fuzzy_entity_link_is_rejected(self) -> None:
        artifact = load(INVALID / "entity-naked-fuzzy.json")
        self.assert_rejected(artifact, "SEMANTIC_NAKED_FUZZY_MATCH")

    def test_12_candidate_lane_cannot_be_promoted_by_label(self) -> None:
        artifact = load(INVALID / "disposition-lane-contamination.json")
        self.assert_rejected(artifact, "SEMANTIC_LANE_CONTAMINATION")

    def test_13_approved_disposition_requires_every_gate(self) -> None:
        artifact = load(VALID / "disposition-quarantine.json")
        artifact.update(
            {
                "disposition": "APPROVED",
                "x3_outcome": "X3D_ALLOW_APPROVED_WRITE",
                "persistent_lane": "approved",
                "hitl_required": false_value(),
                "hitl_complete": false_value(),
            }
        )
        self.assert_rejected(resign(artifact), "SEMANTIC_UNSAFE_APPROVAL")

    def test_14_query_lane_blending_is_denied(self) -> None:
        artifact = load(INVALID / "query-lane-blending.json")
        self.assert_rejected(artifact, "SCHEMA_CONST")

    def test_14b_candidate_lane_requires_explicit_review_route(self) -> None:
        artifact = load(INVALID / "query-candidate-lane-without-review.json")
        self.assert_rejected(artifact, "SCHEMA_ONE_OF")

    def test_15_otel_attribute_allowlist_excludes_sensitive_content(self) -> None:
        artifact = load(INVALID / "otel-sensitive-content.json")
        self.assert_rejected(artifact, "SCHEMA_UNKNOWN_FIELD")
        with self.assertRaises(contracts.ValidationFailure) as raised:
            contracts.validate_semantics(artifact)
        self.assertEqual(raised.exception.issue.code, "PRIVACY_SENSITIVE_OTEL_ATTRIBUTE")

    def test_16_learning_event_correlates_all_material_identifier_classes(self) -> None:
        artifact = load(VALID / "learning-event.json")
        correlation = artifact["correlation"]
        self.assertTrue(
            all(
                correlation[key]
                for key in (
                    "source_id",
                    "candidate_id",
                    "query_id",
                    "graph_operation_id",
                    "hitl_item_id",
                )
            )
        )
        contracts.validate_artifact(artifact, self.registry)

    def test_17_learning_proposal_cannot_auto_apply(self) -> None:
        artifact = load(INVALID / "learning-auto-apply.json")
        self.assert_rejected(artifact, "SCHEMA_CONST")
        with self.assertRaises(contracts.ValidationFailure) as raised:
            contracts.validate_semantics(artifact)
        self.assertEqual(raised.exception.issue.code, "SEMANTIC_AUTOMATIC_SELF_MODIFICATION")

    def test_18_retrieval_text_hash_and_action_status_are_bound(self) -> None:
        artifact = load(VALID / "retrieval-record-candidate-action.json")
        artifact["retrieval_text"] += " Changed."
        self.assert_rejected(resign(artifact), "SEMANTIC_RETRIEVAL_TEXT_HASH")

    def test_19_graph_change_set_is_proposal_only_fixture(self) -> None:
        artifact = load(VALID / "graph-change-set.json")
        self.assertEqual(artifact["status"], "proposed")
        self.assertIsNone(artifact["approval_id"])
        contracts.validate_artifact(artifact, self.registry)

    def test_20_runtime_graph_index_configuration_and_model_inputs_are_unchanged(self) -> None:
        actual = {path: contracts.sha256_file(ROOT / path) for path in EXPECTED_RUNTIME_HASHES}
        self.assertEqual(actual, EXPECTED_RUNTIME_HASHES)

    def test_21_validator_reports_zero_provider_and_runtime_mutation_counts(self) -> None:
        report = contracts.run_validation(CONTRACT_ROOT)
        self.assertEqual(report["provider_call_count"], 0)
        self.assertEqual(report["graph_mutation_count"], 0)
        self.assertEqual(report["index_mutation_count"], 0)
        self.assertEqual(report["configuration_mutation_count"], 0)
        self.assertEqual(report["model_mutation_count"], 0)
        self.assertEqual(report["active_runtime_mutation_count"], 0)

    def test_22_coverage_manifest_rejects_duplicate_requirements(self) -> None:
        artifact = load(INVALID / "query-coverage-manifest-duplicate-requirement.json")
        self.assert_rejected(artifact, "SEMANTIC_DUPLICATE_ID")

    def test_23_evidence_report_binds_l2_and_selected_records(self) -> None:
        artifact = load(INVALID / "retrieval-evidence-report-unbound-record.json")
        self.assert_rejected(artifact, "SEMANTIC_RETRIEVAL_RECORD_UNBOUND")

    def test_24_claim_map_rejects_candidate_factual_reverse_proof(self) -> None:
        artifact = load(INVALID / "claim-evidence-map-reverse-factual.json")
        self.assert_rejected(artifact, "SEMANTIC_CANDIDATE_FACTUAL_ANSWER")

    def test_25_claim_map_rejects_duplicate_claim_ids(self) -> None:
        artifact = load(VALID / "claim-evidence-map.json")
        artifact["claims"].append(dict(artifact["claims"][0]))
        self.assert_rejected(resign(artifact), "SEMANTIC_DUPLICATE_ID")

    def test_26_directional_claim_rejects_reverse_path(self) -> None:
        artifact = load(VALID / "claim-evidence-map.json")
        artifact["claims"][0]["evidence"][0]["traversal_direction"] = "reverse"
        self.assert_rejected(resign(artifact), "SEMANTIC_GRAPH_DIRECTION_UNSUPPORTED")


def true_value() -> bool:
    return True


def false_value() -> bool:
    return False


def write_results(result: unittest.TestResult) -> None:
    test_count = result.testsRun
    failure_count = len(result.failures)
    error_count = len(result.errors)
    status = "PASS" if result.wasSuccessful() else "FAIL"
    payload = {
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "wave": 1,
        "status": status,
        "test_count": test_count,
        "pass_count": test_count - failure_count - error_count,
        "failure_count": failure_count,
        "error_count": error_count,
        "failures": [{"test": str(test), "detail": detail} for test, detail in result.failures],
        "errors": [{"test": str(test), "detail": detail} for test, detail in result.errors],
        "runtime_hashes": EXPECTED_RUNTIME_HASHES,
    }
    result_path = ROOT / "tests" / "dual_pipeline_wave1_acceptance_results.json"
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = [
        "# NEE dual-pipeline Wave 1 acceptance",
        "",
        f"**Status: {status}**",
        "",
        f"{payload['pass_count']}/{test_count} checks passed.",
        "",
        "The suite validates schema/version coverage, canonical digests, strict unknown-field behavior, source locators, entity ambiguity, lane isolation, approval gates, telemetry privacy, learning correlation, no automatic self-modification, and immutable runtime hashes.",
    ]
    (ROOT / "tests" / "dual_pipeline_wave1_acceptance_report.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DualPipelineWave1Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_results(result)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
