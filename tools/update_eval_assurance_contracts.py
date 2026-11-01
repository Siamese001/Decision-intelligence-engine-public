#!/usr/bin/env python3
"""Seal eval-assurance fixtures and refresh the closed contract inventory."""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, sha256_file, write_json  # noqa: E402

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
VALID_ROOT = CONTRACT_ROOT / "fixtures" / "valid"
INVALID_ROOT = CONTRACT_ROOT / "fixtures" / "invalid"
ASSURANCE_VALID = (
    VALID_ROOT / "eval-suite-v2.json",
)
WORKFLOW_SUITE = ROOT / "evals" / "suites" / "agentic-workflow-assurance-e1.json"
WORKFLOW_FIXTURE = (
    ROOT / "evals" / "workloads" / "codex-read-amplification-v1.json"
)
EVALUATOR_SOURCE = ROOT / "dual_pipeline" / "evals" / "assurance.py"
FIXED_TIME = "2026-07-29T12:00:00Z"


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(value)
    value.pop("canonical_digest", None)
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(value),
    }
    return value


def _missing_usage(value: dict[str, Any]) -> None:
    attempt = value["attempts"][0]
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        attempt[key] = None
    attempt["microcredits"] = None
    attempt["usage_reported"] = False


def _token_mismatch(value: dict[str, Any]) -> None:
    value["attempts"][0]["total_tokens"] += 1
    value["aggregate"]["total_tokens"] += 1


def _duplicate_ordinal(value: dict[str, Any]) -> None:
    duplicate = copy.deepcopy(value["attempts"][0])
    value["attempts"].append(duplicate)


def _aggregate_mismatch(value: dict[str, Any]) -> None:
    value["aggregate"]["request_serialized_bytes_total"] += 1


def _unknown_field(value: dict[str, Any]) -> None:
    value["attempts"][0]["undeclared_counter"] = 1


def _sensitive_extension(value: dict[str, Any]) -> None:
    value["extensions"] = {"com.partner_advisory.raw-prompt": "forbidden"}


INVALID_MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "eval-measurement-missing-usage.json": _missing_usage,
    "eval-measurement-token-mismatch.json": _token_mismatch,
    "eval-measurement-duplicate-ordinal.json": _duplicate_ordinal,
    "eval-measurement-aggregate-mismatch.json": _aggregate_mismatch,
    "eval-measurement-unknown-field.json": _unknown_field,
    "eval-measurement-sensitive-extension.json": _sensitive_extension,
}


def update_inventory() -> None:
    rows = []
    for path in sorted((CONTRACT_ROOT / "schemas").glob("*.schema.json")):
        value = read_json(path)
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "schema_id": value["$id"],
                "schema_version": (
                    "1.0.0"
                    if value["$id"] == "urn:nee:dual-pipeline:common:1.0.0"
                    else value["properties"]["schema_version"]["const"]
                ),
                "sha256": sha256_file(path),
            }
        )
    previous = read_json(CONTRACT_ROOT / "contract_inventory.json")
    inventory = {
        "compatibility_policy": previous["compatibility_policy"],
        "initiative_id": previous["initiative_id"],
        "inventory_version": previous["inventory_version"],
        "schemas": rows,
    }
    write_json(CONTRACT_ROOT / "contract_inventory.json", seal(inventory))


def _refresh_workflow_suite() -> dict[str, Any]:
    suite = read_json(WORKFLOW_SUITE)
    suite["workload"]["sha256"] = sha256_file(WORKFLOW_FIXTURE)
    suite["evaluator"]["digest"] = sha256_file(EVALUATOR_SOURCE)
    suite = seal(suite)
    write_json(WORKFLOW_SUITE, suite)
    write_json(VALID_ROOT / "agentic-workflow-eval-suite.json", suite)
    return suite


def _normalize_result(
    result: dict[str, Any],
    *,
    evaluation_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized_measurements: list[dict[str, Any]] = []
    for case_result in result["case_results"]:
        measurement = read_json(Path(case_result["measurement_path"]))
        measurement["measured_at"] = FIXED_TIME
        measurement = seal(measurement)
        relative_path = (
            Path("cases") / case_result["case_id"] / "execution-measurement.json"
        ).as_posix()
        case_result["measurement_path"] = relative_path
        case_result["measurement_digest"] = measurement["canonical_digest"]["value"]
        normalized_measurements.append(measurement)
    result["evaluated_at"] = FIXED_TIME
    return seal(result), normalized_measurements


def _generate_workflow_fixtures() -> None:
    from dual_pipeline.evals.assurance import (  # noqa: PLC0415
        compare_workflow_results,
        run_workflow_suite,
    )

    with tempfile.TemporaryDirectory(prefix="nee-eval-contract-") as temp:
        root = Path(temp)
        candidate, _ = run_workflow_suite(
            suite_path=WORKFLOW_SUITE,
            output_root=root,
            treatment_id="candidate-bounded",
            evaluation_id="fixture-candidate-evaluation",
        )
        legacy, _ = run_workflow_suite(
            suite_path=WORKFLOW_SUITE,
            output_root=root,
            treatment_id="legacy-replay",
            evaluation_id="fixture-legacy-evaluation",
        )
        candidate, measurements = _normalize_result(
            candidate,
            evaluation_root=root,
        )
        legacy, _ = _normalize_result(legacy, evaluation_root=root)
        candidate_path = root / "candidate-normalized.json"
        legacy_path = root / "legacy-normalized.json"
        write_json(candidate_path, candidate)
        write_json(legacy_path, legacy)
        comparison = compare_workflow_results(
            baseline_path=legacy_path,
            candidate_path=candidate_path,
        )
        comparison["compared_at"] = FIXED_TIME
        comparison = seal(comparison)

        write_json(
            VALID_ROOT / "eval-execution-measurement.json",
            measurements[0],
        )
        write_json(VALID_ROOT / "eval-run-result-v2.json", candidate)
        write_json(VALID_ROOT / "eval-comparison-v2.json", comparison)


def main() -> int:
    _refresh_workflow_suite()
    for path in ASSURANCE_VALID:
        write_json(path, seal(read_json(path)))
    _generate_workflow_fixtures()

    measurement = read_json(VALID_ROOT / "eval-execution-measurement.json")
    for filename, mutate in INVALID_MUTATIONS.items():
        value = copy.deepcopy(measurement)
        value["artifact_id"] = filename.removesuffix(".json")
        value["measurement_id"] = value["artifact_id"]
        mutate(value)
        write_json(INVALID_ROOT / filename, seal(value))

    update_inventory()
    print(
        "sealed the workflow suite and 5 valid assurance fixtures, "
        f"generated {len(INVALID_MUTATIONS)} invalid fixtures, and refreshed inventory"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
