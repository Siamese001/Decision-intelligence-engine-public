#!/usr/bin/env python3
"""Create and seal the Wave 1 L1 controller contracts and fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
VALID_ROOT = CONTRACT_ROOT / "fixtures" / "valid"
INVALID_ROOT = CONTRACT_ROOT / "fixtures" / "invalid"
INVENTORY = CONTRACT_ROOT / "contract_inventory.json"
BASELINE_DIGEST = "d4ea80257ef5ec779bbd20842adf9505b3048018494565347ee7ee298486df58"
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64
THREE = "3" * 64


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(value)
    artifact.pop("canonical_digest", None)
    artifact["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(artifact),
    }
    return artifact


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.write_bytes(payload)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def string(min_length: int = 1) -> dict[str, Any]:
    return {"type": "string", "minLength": min_length}


def sha() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[a-f0-9]{64}$"}


def digest_object() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["algorithm", "scope", "value"],
        "properties": {
            "algorithm": {"const": "sha256"},
            "scope": {"const": "artifact_without_canonical_digest"},
            "value": sha(),
        },
    }


def array(item: dict[str, Any], *, minimum: int = 0, unique: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "array", "items": item}
    if minimum:
        value["minItems"] = minimum
    if unique:
        value["uniqueItems"] = True
    return value


def object_schema(contract_id: str, version: str, title: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    common = {
        "contract_id": {"const": contract_id},
        "schema_version": {"const": version},
        "artifact_id": string(),
        "canonical_digest": digest_object(),
        "extensions": {"type": "object"},
    }
    required_fields = list(dict.fromkeys(["contract_id", "schema_version", "artifact_id", "canonical_digest", "extensions", *required]))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": contract_id,
        "title": title,
        "type": "object",
        "additionalProperties": False,
        "required": required_fields,
        "properties": {**common, **properties},
    }


def base_artifact(contract_id: str, version: str, artifact_id: str) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "schema_version": version,
        "artifact_id": artifact_id,
        "extensions": {},
    }


def plan_v3_schema() -> dict[str, Any]:
    source = json.loads((SCHEMA_ROOT / "query-plan-v2.schema.json").read_text(encoding="utf-8"))
    schema = copy.deepcopy(source)
    schema["$id"] = "urn:nee:dual-pipeline:query-plan:3.0.0"
    schema["title"] = "NEE controller-bound query execution plan"
    schema["properties"]["contract_id"] = {"const": schema["$id"]}
    schema["properties"]["schema_version"] = {"const": "3.0.0"}
    schema["required"].append("controller")
    schema["properties"]["controller"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "policy_digest", "coverage_manifest_digest", "action_allowlist", "aggregate_budget", "progress_rule", "initial_action_ids", "terminal_predicates"],
        "properties": {
            "mode": {"enum": ["off", "shadow", "bounded"]},
            "policy_digest": sha(),
            "coverage_manifest_digest": sha(),
            "action_allowlist": array({"enum": ["requirement_hybrid_retrieval", "stop"]}, minimum=1, unique=True),
            "aggregate_budget": {
                "type": "object",
                "additionalProperties": False,
                "required": ["max_additional_attempts", "max_actions", "max_unique_records", "max_result_bytes", "max_elapsed_ms"],
                "properties": {
                    "max_additional_attempts": {"type": "integer", "minimum": 0, "maximum": 1},
                    "max_actions": {"type": "integer", "minimum": 1, "maximum": 16},
                    "max_unique_records": {"type": "integer", "minimum": 1, "maximum": 500},
                    "max_result_bytes": {"type": "integer", "minimum": 1, "maximum": 10000000},
                    "max_elapsed_ms": {"type": "integer", "minimum": 1, "maximum": 300000},
                },
            },
            "progress_rule": {"const": "newly_covered_material_requirements"},
            "initial_action_ids": array(string(), unique=True),
            "terminal_predicates": array({"enum": ["coverage_complete", "explicit_gap", "no_progress", "budget_exhausted", "unauthorized", "conflict", "hitl_required", "shadow"]}, minimum=1, unique=True),
        },
    }
    return schema


def new_schemas() -> dict[str, dict[str, Any]]:
    attempt_id = "urn:nee:dual-pipeline:query-retrieval-attempt:1.0.0"
    observation_id = "urn:nee:dual-pipeline:query-plan-observation:1.0.0"
    terminal_id = "urn:nee:dual-pipeline:query-plan-terminal:1.0.0"
    policy_id = "urn:nee:dual-pipeline:query-l1-control-policy:1.0.0"
    suite_id = "urn:nee:dual-pipeline:query-l1-controller-eval-suite:1.0.0"
    result_id = "urn:nee:dual-pipeline:query-l1-controller-eval-result:1.0.0"
    comparison_id = "urn:nee:dual-pipeline:query-l1-controller-eval-comparison:1.0.0"
    release_suite_id = "urn:nee:dual-pipeline:retrieval-release-suite:2.0.0"
    release_eval_id = "urn:nee:dual-pipeline:retrieval-release-evaluation:2.0.0"
    wave_id = "urn:nee:dual-pipeline:l1-wave-receipt:1.0.0"
    return {
        "query-retrieval-attempt.schema.json": object_schema(
            attempt_id, "1.0.0", "Governed L1 retrieval attempt", {
                "attempt_id": string(), "parent_plan_digest": sha(), "coverage_manifest_digest": sha(),
                "attempt_ordinal": {"type": "integer", "minimum": 0, "maximum": 1},
                "action_id": string(), "action_type": {"enum": ["requirement_hybrid_retrieval", "stop"]},
                "requirement_ids": array(string(), minimum=1, unique=True), "authorization_scope_digest": sha(),
                "active_pair": {"type": "object", "additionalProperties": False, "required": ["generation_id", "graph_sha256", "sidecar_sha256", "backend", "model_id", "model_revision"], "properties": {"generation_id": string(), "graph_sha256": sha(), "sidecar_sha256": sha(), "backend": string(), "model_id": string(), "model_revision": string()}},
                "input_digest": sha(), "budget_before": {"type": "object", "additionalProperties": False, "required": ["actions_used", "records_used", "bytes_used", "elapsed_ms"], "properties": {"actions_used": {"type": "integer", "minimum": 0}, "records_used": {"type": "integer", "minimum": 0}, "bytes_used": {"type": "integer", "minimum": 0}, "elapsed_ms": {"type": "integer", "minimum": 0}}},
                "result": {"type": "object", "additionalProperties": False, "required": ["status", "record_ids_by_lane", "result_digest", "elapsed_ms", "bytes", "provider_initialization_count", "provider_call_count"], "properties": {"status": {"enum": ["completed", "blocked", "skipped"]}, "record_ids_by_lane": {"type": "object", "additionalProperties": {"type": "array", "items": string(), "uniqueItems": True}}, "result_digest": sha(), "elapsed_ms": {"type": "integer", "minimum": 0}, "bytes": {"type": "integer", "minimum": 0}, "provider_initialization_count": {"type": "integer", "const": 0}, "provider_call_count": {"type": "integer", "const": 0}}},
                "budget_after": {"type": "object", "additionalProperties": False, "required": ["actions_used", "records_used", "bytes_used", "elapsed_ms"], "properties": {"actions_used": {"type": "integer", "minimum": 0}, "records_used": {"type": "integer", "minimum": 0}, "bytes_used": {"type": "integer", "minimum": 0}, "elapsed_ms": {"type": "integer", "minimum": 0}}},
                "graph_sha256_before": sha(), "graph_sha256_after": sha(), "provider_initialization_count": {"type": "integer", "const": 0}, "provider_call_count": {"type": "integer", "const": 0},
            }, ["attempt_id", "parent_plan_digest", "coverage_manifest_digest", "attempt_ordinal", "action_id", "action_type", "requirement_ids", "authorization_scope_digest", "active_pair", "input_digest", "budget_before", "result", "budget_after", "graph_sha256_before", "graph_sha256_after", "provider_initialization_count", "provider_call_count"]),
        "query-plan-observation.schema.json": object_schema(
            observation_id, "1.0.0", "Governed L1 plan observation", {
                "observation_id": string(), "parent_plan_digest": sha(), "attempt_digest": sha(), "evidence_report_digest": sha(), "coverage_manifest_digest": sha(),
                "requirement_outcomes": array({"type": "object", "additionalProperties": False, "required": ["requirement_id", "outcome"], "properties": {"requirement_id": string(), "outcome": {"enum": ["covered", "explicit_gap", "unauthorized", "conflict", "excluded"]}}}, minimum=1),
                "newly_covered_requirement_ids": array(string(), unique=True), "unresolved_requirement_ids": array(string(), unique=True),
                "progress": {"enum": ["progress", "no_progress", "complete", "blocked"]}, "progress_reason": string(), "graph_sha256_before": sha(), "graph_sha256_after": sha(), "provider_call_count": {"type": "integer", "const": 0},
            }, ["observation_id", "parent_plan_digest", "attempt_digest", "evidence_report_digest", "coverage_manifest_digest", "requirement_outcomes", "newly_covered_requirement_ids", "unresolved_requirement_ids", "progress", "progress_reason", "graph_sha256_before", "graph_sha256_after", "provider_call_count"]),
        "query-plan-terminal.schema.json": object_schema(
            terminal_id, "1.0.0", "Governed L1 terminal receipt", {
                "terminal_id": string(), "parent_plan_digest": sha(), "coverage_manifest_digest": sha(), "attempt_chain": array(sha(), minimum=1), "final_evidence_report_digest": sha(),
                "total_work": {"type": "object", "additionalProperties": False, "required": ["attempts", "actions", "unique_records", "bytes", "elapsed_ms"], "properties": {"attempts": {"type": "integer", "minimum": 1}, "actions": {"type": "integer", "minimum": 1}, "unique_records": {"type": "integer", "minimum": 0}, "bytes": {"type": "integer", "minimum": 0}, "elapsed_ms": {"type": "integer", "minimum": 0}}},
                "final_provider_call_count": {"type": "integer", "const": 0}, "graph_sha256_before": sha(), "graph_sha256_after": sha(), "terminal_reason": {"enum": ["COMPLETED", "EXPLICIT_GAP", "NO_PROGRESS", "BUDGET_EXHAUSTED", "UNAUTHORIZED", "CONFLICT", "HITL_REQUIRED", "SHADOW"]}, "coverage_complete": {"type": "boolean"}, "permitted_next_stage": {"enum": ["L2D", "X1", "X2", "X3", "X4", "HITL", "NONE"]},
            }, ["terminal_id", "parent_plan_digest", "coverage_manifest_digest", "attempt_chain", "final_evidence_report_digest", "total_work", "final_provider_call_count", "graph_sha256_before", "graph_sha256_after", "terminal_reason", "coverage_complete", "permitted_next_stage"]),
        "query-l1-control-policy.schema.json": object_schema(
            policy_id, "1.0.0", "L1 controller policy", {
                "policy_id": string(), "policy_version": {"const": "1.0.0"}, "mode": {"enum": ["off", "shadow", "bounded"]}, "action_allowlist": array({"enum": ["requirement_hybrid_retrieval", "stop"]}, minimum=1, unique=True), "budget": {"type": "object", "additionalProperties": False, "required": ["max_additional_attempts", "max_actions", "max_unique_records", "max_result_bytes", "max_elapsed_ms"], "properties": {"max_additional_attempts": {"type": "integer", "minimum": 0, "maximum": 1}, "max_actions": {"type": "integer", "minimum": 1, "maximum": 16}, "max_unique_records": {"type": "integer", "minimum": 1, "maximum": 500}, "max_result_bytes": {"type": "integer", "minimum": 1, "maximum": 10000000}, "max_elapsed_ms": {"type": "integer", "minimum": 1, "maximum": 300000}}}, "progress_rule": {"const": "newly_covered_material_requirements"}, "terminal_reasons": array(string(), minimum=1, unique=True), "provider_calls_before_l2d": {"type": "integer", "const": 0}, "graph_mutations_before_l2d": {"type": "integer", "const": 0}, "runtime_default": {"const": "off"},
            }, ["policy_id", "policy_version", "mode", "action_allowlist", "budget", "progress_rule", "terminal_reasons", "provider_calls_before_l2d", "graph_mutations_before_l2d", "runtime_default"]),
        "query-l1-controller-eval-suite.schema.json": object_schema(
            suite_id, "1.0.0", "L1 controller evaluation suite", {
                "suite_id": string(), "baseline_suite_digest": sha(), "case_taxonomy": array(string(), minimum=1, unique=True), "treatments": array({"type": "object", "additionalProperties": False, "required": ["treatment_id", "mode", "policy_digest"], "properties": {"treatment_id": string(), "mode": {"enum": ["off", "shadow", "bounded"]}, "policy_digest": sha()}}, minimum=1), "comparison_invariants": {"type": "object", "additionalProperties": False, "required": ["graph_sha256", "authorization_scope_digest", "workload_digest", "evaluator_digest"], "properties": {"graph_sha256": sha(), "authorization_scope_digest": sha(), "workload_digest": sha(), "evaluator_digest": sha()}}, "thresholds": {"type": "object", "additionalProperties": False, "required": ["maximum_provider_calls", "maximum_graph_mutations", "minimum_recovery_rate"], "properties": {"maximum_provider_calls": {"type": "integer", "const": 0}, "maximum_graph_mutations": {"type": "integer", "const": 0}, "minimum_recovery_rate": {"type": "number", "minimum": 0, "maximum": 1}}},
            }, ["suite_id", "baseline_suite_digest", "case_taxonomy", "treatments", "comparison_invariants", "thresholds"]),
        "query-l1-controller-eval-result.schema.json": object_schema(
            result_id, "1.0.0", "L1 controller evaluation result", {
                "result_id": string(), "suite_id": string(), "suite_digest": sha(), "case_results": array({"type": "object", "additionalProperties": False, "required": ["case_id", "status", "initial_terminal_class", "final_terminal_class", "attempt_chain_digest"], "properties": {"case_id": string(), "status": {"enum": ["PASS", "FAIL", "MEASUREMENT_INVALID"]}, "initial_terminal_class": string(), "final_terminal_class": string(), "attempt_chain_digest": sha()}}, minimum=1), "aggregate": {"type": "object", "additionalProperties": False, "required": ["case_count", "passed_case_count", "failed_case_count", "invalid_case_count", "recovery_rate"], "properties": {"case_count": {"type": "integer", "minimum": 1}, "passed_case_count": {"type": "integer", "minimum": 0}, "failed_case_count": {"type": "integer", "minimum": 0}, "invalid_case_count": {"type": "integer", "minimum": 0}, "recovery_rate": {"type": "number", "minimum": 0, "maximum": 1}}}, "status": {"enum": ["PASS", "FAIL", "MEASUREMENT_INVALID"]}, "treatment_mode": {"enum": ["off", "shadow", "bounded"]}, "policy_digest": sha(), "graph_sha256": sha(), "provider_call_count": {"type": "integer", "const": 0}, "graph_mutation_count": {"type": "integer", "const": 0},
            }, ["result_id", "suite_id", "suite_digest", "case_results", "aggregate", "status", "treatment_mode", "policy_digest", "graph_sha256", "provider_call_count", "graph_mutation_count"]),
        "query-l1-controller-eval-comparison.schema.json": object_schema(
            comparison_id, "1.0.0", "L1 controller evaluation comparison", {
                "comparison_id": string(), "baseline_result_digest": sha(), "candidate_result_digest": sha(), "treatment_dimension": string(), "invariants": {"type": "object", "additionalProperties": False, "required": ["graph_sha256", "authorization_scope_digest", "workload_digest", "evaluator_digest"], "properties": {"graph_sha256": sha(), "authorization_scope_digest": sha(), "workload_digest": sha(), "evaluator_digest": sha()}}, "metric_deltas": array({"type": "object", "additionalProperties": False, "required": ["metric", "baseline", "candidate", "delta"], "properties": {"metric": string(), "baseline": {"type": "number"}, "candidate": {"type": "number"}, "delta": {"type": "number"}}}, minimum=1), "status": {"enum": ["COMPARABLE", "NOT_COMPARABLE"]}, "cause_codes": array(string(), unique=True),
            }, ["comparison_id", "baseline_result_digest", "candidate_result_digest", "treatment_dimension", "invariants", "metric_deltas", "status", "cause_codes"]),
        "retrieval-release-suite-v2.schema.json": object_schema(
            release_suite_id, "2.0.0", "Controller-bound retrieval release suite", {
                "suite_id": string(), "suite_version": {"const": "2.0.0"}, "controller_mode": {"enum": ["off", "shadow", "bounded"]}, "policy_digest": sha(), "budget_profile": string(), "cases": array(string(), minimum=1, unique=True), "active_pair": {"type": "object", "additionalProperties": False, "required": ["generation_id", "graph_sha256", "sidecar_sha256", "backend", "model_id", "model_revision"], "properties": {"generation_id": string(), "graph_sha256": sha(), "sidecar_sha256": sha(), "backend": string(), "model_id": string(), "model_revision": string()}}, "graph_sha256": sha(),
            }, ["suite_id", "suite_version", "controller_mode", "policy_digest", "budget_profile", "cases", "active_pair", "graph_sha256"]),
        "retrieval-release-evaluation-v2.schema.json": object_schema(
            release_eval_id, "2.0.0", "Controller-bound retrieval release evaluation", {
                "evaluation_id": string(), "suite_id": string(), "suite_digest": sha(), "active_pair": {"type": "object", "additionalProperties": False, "required": ["generation_id", "graph_sha256", "sidecar_sha256", "backend", "model_id", "model_revision"], "properties": {"generation_id": string(), "graph_sha256": sha(), "sidecar_sha256": sha(), "backend": string(), "model_id": string(), "model_revision": string()}}, "controller_mode": {"enum": ["off", "shadow", "bounded"]}, "policy_digest": sha(), "initial_coverage": {"type": "object", "additionalProperties": False, "required": ["covered", "unresolved"], "properties": {"covered": {"type": "integer", "minimum": 0}, "unresolved": {"type": "integer", "minimum": 0}}}, "final_coverage": {"type": "object", "additionalProperties": False, "required": ["covered", "unresolved"], "properties": {"covered": {"type": "integer", "minimum": 0}, "unresolved": {"type": "integer", "minimum": 0}}}, "attempt_chain_digests": array(sha(), unique=True), "final_evidence_report_digest": sha(), "work_counts": {"type": "object", "additionalProperties": False, "required": ["attempts", "actions", "records", "bytes", "elapsed_ms"], "properties": {"attempts": {"type": "integer", "minimum": 0}, "actions": {"type": "integer", "minimum": 0}, "records": {"type": "integer", "minimum": 0}, "bytes": {"type": "integer", "minimum": 0}, "elapsed_ms": {"type": "integer", "minimum": 0}}}, "status": {"enum": ["PASS", "FAIL", "NOT_QUALIFIED"]},
            }, ["evaluation_id", "suite_id", "suite_digest", "active_pair", "controller_mode", "policy_digest", "initial_coverage", "final_coverage", "attempt_chain_digests", "final_evidence_report_digest", "work_counts", "status"]),
        "l1-wave-receipt.schema.json": object_schema(
            wave_id, "1.0.0", "L1 implementation wave receipt", {
                "wave_id": {"enum": [f"W{i}" for i in range(1, 13)]}, "program_id": string(), "branch": string(), "base_commit": {"type": "string", "pattern": "^[a-f0-9]{40}$"}, "result_commit": {"type": "string", "pattern": "^[a-f0-9]{40}$"}, "predecessor_receipt_digest": sha(), "changed_files": array(string(), minimum=1, unique=True), "contract_inventory_digest": sha(), "policy_digest": sha(), "commands": array({"type": "object", "additionalProperties": False, "required": ["command", "exit_code", "status", "skipped", "timed_out"], "properties": {"command": string(), "exit_code": {"type": "integer", "minimum": 0}, "status": {"enum": ["PASS", "FAIL"]}, "skipped": {"type": "integer", "minimum": 0}, "timed_out": {"type": "boolean"}}}, minimum=1), "provider_initialization_count": {"type": "integer", "const": 0}, "provider_call_count": {"type": "integer", "const": 0}, "graph_mutation_count": {"type": "integer", "const": 0}, "next_wave_authorized": {"type": "boolean"}, "artifacts": array(string(), minimum=1, unique=True), "exit_gate_verdicts": {"type": "object", "additionalProperties": {"type": "boolean"}}, "rollback_control": string(), "cache_namespace_disposition": string(),
            }, ["wave_id", "program_id", "branch", "base_commit", "result_commit", "predecessor_receipt_digest", "changed_files", "contract_inventory_digest", "policy_digest", "commands", "provider_initialization_count", "provider_call_count", "graph_mutation_count", "next_wave_authorized", "artifacts", "exit_gate_verdicts", "rollback_control", "cache_namespace_disposition"]),
    }


def valid_fixtures() -> dict[str, dict[str, Any]]:
    plan = json.loads((VALID_ROOT / "query-plan-v2.json").read_text(encoding="utf-8"))
    plan["contract_id"] = "urn:nee:dual-pipeline:query-plan:3.0.0"
    plan["schema_version"] = "3.0.0"
    plan["artifact_id"] = "query-plan-v3-fixture"
    plan["plan_id"] = "query-plan-v3-fixture"
    plan["controller"] = {"mode": "off", "policy_digest": TWO, "coverage_manifest_digest": THREE, "action_allowlist": ["requirement_hybrid_retrieval", "stop"], "aggregate_budget": {"max_additional_attempts": 1, "max_actions": 2, "max_unique_records": 50, "max_result_bytes": 100000, "max_elapsed_ms": 30000}, "progress_rule": "newly_covered_material_requirements", "initial_action_ids": ["initial-retrieval"], "terminal_predicates": ["coverage_complete", "explicit_gap", "no_progress", "budget_exhausted", "unauthorized", "conflict", "hitl_required", "shadow"]}
    values: dict[str, dict[str, Any]] = {"query-plan-v3.json": seal(plan)}
    active_pair = {"generation_id": "wave1-generation", "graph_sha256": ZERO, "sidecar_sha256": ONE, "backend": "deterministic_fixture", "model_id": "fixture-model", "model_revision": "fixture-revision"}
    attempt = base_artifact("urn:nee:dual-pipeline:query-retrieval-attempt:1.0.0", "1.0.0", "query-retrieval-attempt-fixture")
    attempt.update({"attempt_id": "attempt-1", "parent_plan_digest": TWO, "coverage_manifest_digest": THREE, "attempt_ordinal": 0, "action_id": "initial-retrieval", "action_type": "requirement_hybrid_retrieval", "requirement_ids": ["requirement-1"], "authorization_scope_digest": TWO, "active_pair": active_pair, "input_digest": THREE, "budget_before": {"actions_used": 0, "records_used": 0, "bytes_used": 0, "elapsed_ms": 0}, "result": {"status": "completed", "record_ids_by_lane": {"approved": ["record-1"]}, "result_digest": ONE, "elapsed_ms": 5, "bytes": 100, "provider_initialization_count": 0, "provider_call_count": 0}, "budget_after": {"actions_used": 1, "records_used": 1, "bytes_used": 100, "elapsed_ms": 5}, "graph_sha256_before": ZERO, "graph_sha256_after": ZERO, "provider_initialization_count": 0, "provider_call_count": 0})
    values["query-retrieval-attempt.json"] = seal(attempt)
    observation = base_artifact("urn:nee:dual-pipeline:query-plan-observation:1.0.0", "1.0.0", "query-plan-observation-fixture")
    observation.update({"observation_id": "observation-1", "parent_plan_digest": TWO, "attempt_digest": digest(values["query-retrieval-attempt.json"]), "evidence_report_digest": ONE, "coverage_manifest_digest": THREE, "requirement_outcomes": [{"requirement_id": "requirement-1", "outcome": "covered"}], "newly_covered_requirement_ids": ["requirement-1"], "unresolved_requirement_ids": [], "progress": "complete", "progress_reason": "coverage complete", "graph_sha256_before": ZERO, "graph_sha256_after": ZERO, "provider_call_count": 0})
    values["query-plan-observation.json"] = seal(observation)
    terminal = base_artifact("urn:nee:dual-pipeline:query-plan-terminal:1.0.0", "1.0.0", "query-plan-terminal-fixture")
    terminal.update({"terminal_id": "terminal-1", "parent_plan_digest": TWO, "coverage_manifest_digest": THREE, "attempt_chain": [digest(values["query-retrieval-attempt.json"])], "final_evidence_report_digest": ONE, "total_work": {"attempts": 1, "actions": 1, "unique_records": 1, "bytes": 100, "elapsed_ms": 5}, "final_provider_call_count": 0, "graph_sha256_before": ZERO, "graph_sha256_after": ZERO, "terminal_reason": "COMPLETED", "coverage_complete": True, "permitted_next_stage": "L2D"})
    values["query-plan-terminal.json"] = seal(terminal)
    policy = base_artifact("urn:nee:dual-pipeline:query-l1-control-policy:1.0.0", "1.0.0", "query-l1-control-policy-fixture")
    policy.update({"policy_id": "query-l1-control-policy-fixture", "policy_version": "1.0.0", "mode": "off", "action_allowlist": ["requirement_hybrid_retrieval", "stop"], "budget": {"max_additional_attempts": 1, "max_actions": 2, "max_unique_records": 50, "max_result_bytes": 100000, "max_elapsed_ms": 30000}, "progress_rule": "newly_covered_material_requirements", "terminal_reasons": ["COMPLETED", "EXPLICIT_GAP", "NO_PROGRESS", "BUDGET_EXHAUSTED", "UNAUTHORIZED", "CONFLICT", "HITL_REQUIRED", "SHADOW"], "provider_calls_before_l2d": 0, "graph_mutations_before_l2d": 0, "runtime_default": "off"})
    values["query-l1-control-policy.json"] = seal(policy)
    eval_suite = base_artifact("urn:nee:dual-pipeline:query-l1-controller-eval-suite:1.0.0", "1.0.0", "query-l1-controller-eval-suite-fixture")
    eval_suite.update({"suite_id": "query-l1-controller-eval-suite-fixture", "baseline_suite_digest": TWO, "case_taxonomy": ["complete_first_pass", "recoverable_miss", "true_gap", "irrelevant_lexical_overlap", "unauthorized_evidence", "reverse_only_path", "conflict", "candidate_lane", "cache_replay", "no_progress"], "treatments": [{"treatment_id": "off", "mode": "off", "policy_digest": TWO}], "comparison_invariants": {"graph_sha256": ZERO, "authorization_scope_digest": TWO, "workload_digest": THREE, "evaluator_digest": ONE}, "thresholds": {"maximum_provider_calls": 0, "maximum_graph_mutations": 0, "minimum_recovery_rate": 0.0}})
    values["query-l1-controller-eval-suite.json"] = seal(eval_suite)
    eval_result = base_artifact("urn:nee:dual-pipeline:query-l1-controller-eval-result:1.0.0", "1.0.0", "query-l1-controller-eval-result-fixture")
    eval_result.update({"result_id": "query-l1-controller-eval-result-fixture", "suite_id": "query-l1-controller-eval-suite-fixture", "suite_digest": digest(values["query-l1-controller-eval-suite.json"]), "case_results": [{"case_id": "case-1", "status": "PASS", "initial_terminal_class": "COMPLETED", "final_terminal_class": "COMPLETED", "attempt_chain_digest": digest(values["query-retrieval-attempt.json"])}], "aggregate": {"case_count": 1, "passed_case_count": 1, "failed_case_count": 0, "invalid_case_count": 0, "recovery_rate": 0.0}, "status": "PASS", "treatment_mode": "off", "policy_digest": TWO, "graph_sha256": ZERO, "provider_call_count": 0, "graph_mutation_count": 0})
    values["query-l1-controller-eval-result.json"] = seal(eval_result)
    comparison = base_artifact("urn:nee:dual-pipeline:query-l1-controller-eval-comparison:1.0.0", "1.0.0", "query-l1-controller-eval-comparison-fixture")
    comparison.update({"comparison_id": "query-l1-controller-eval-comparison-fixture", "baseline_result_digest": TWO, "candidate_result_digest": THREE, "treatment_dimension": "mode", "invariants": {"graph_sha256": ZERO, "authorization_scope_digest": TWO, "workload_digest": THREE, "evaluator_digest": ONE}, "metric_deltas": [{"metric": "recovery_rate", "baseline": 0.0, "candidate": 0.0, "delta": 0.0}], "status": "COMPARABLE", "cause_codes": []})
    values["query-l1-controller-eval-comparison.json"] = seal(comparison)
    release_suite = base_artifact("urn:nee:dual-pipeline:retrieval-release-suite:2.0.0", "2.0.0", "retrieval-release-suite-v2-fixture")
    release_suite.update({"suite_id": "retrieval-release-suite-v2-fixture", "suite_version": "2.0.0", "controller_mode": "off", "policy_digest": TWO, "budget_profile": "l1-off", "cases": ["case-1"], "active_pair": active_pair, "graph_sha256": ZERO})
    values["retrieval-release-suite-v2.json"] = seal(release_suite)
    release_eval = base_artifact("urn:nee:dual-pipeline:retrieval-release-evaluation:2.0.0", "2.0.0", "retrieval-release-evaluation-v2-fixture")
    release_eval.update({"evaluation_id": "retrieval-release-evaluation-v2-fixture", "suite_id": "retrieval-release-suite-v2-fixture", "suite_digest": digest(values["retrieval-release-suite-v2.json"]), "active_pair": active_pair, "controller_mode": "off", "policy_digest": TWO, "initial_coverage": {"covered": 1, "unresolved": 0}, "final_coverage": {"covered": 1, "unresolved": 0}, "attempt_chain_digests": [digest(values["query-retrieval-attempt.json"])], "final_evidence_report_digest": ONE, "work_counts": {"attempts": 1, "actions": 1, "records": 1, "bytes": 100, "elapsed_ms": 5}, "status": "PASS"})
    values["retrieval-release-evaluation-v2.json"] = seal(release_eval)
    receipt = base_artifact("urn:nee:dual-pipeline:l1-wave-receipt:1.0.0", "1.0.0", "l1-wave-receipt-fixture")
    receipt.update({"wave_id": "W1", "program_id": "NEE-KG-DUAL-PIPELINE-01", "branch": "codex/l1-agentic-gap-analysis", "base_commit": "de14722d90243fcb1cd7ee513f23d180da33527d", "result_commit": "de14722d90243fcb1cd7ee513f23d180da33527d", "predecessor_receipt_digest": BASELINE_DIGEST, "changed_files": ["contracts/dual_pipeline/schemas/", "contracts/dual_pipeline/fixtures/", "requirements/query-l1-control-policy.json"], "contract_inventory_digest": TWO, "policy_digest": TWO, "commands": [{"command": "python tools/validate_dual_pipeline_contracts.py", "exit_code": 0, "status": "PASS", "skipped": 0, "timed_out": False}], "provider_initialization_count": 0, "provider_call_count": 0, "graph_mutation_count": 0, "next_wave_authorized": True, "artifacts": ["query-plan-v3", "query-retrieval-attempt", "query-plan-observation", "query-plan-terminal", "query-l1-control-policy"], "exit_gate_verdicts": {"contracts_validate": True, "runtime_mode_off": True, "historical_v2_readable": True}, "rollback_control": "Remove Wave 1 schemas, fixtures, policy, and inventory entries as one unit.", "cache_namespace_disposition": "No runtime cache namespace introduced."})
    values["l1-wave-01-receipt.json"] = seal(receipt)
    return values


def invalid_fixtures(valid: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    def mutate(name: str, source: str, path: tuple[str, ...], value: Any) -> None:
        artifact = copy.deepcopy(valid[source])
        target = artifact
        for token in path[:-1]:
            target = target[token]
        target[path[-1]] = value
        cases[name] = seal(artifact)
    mutate("query-plan-v3-provider-egress.json", "query-plan-v3.json", ("controller", "action_allowlist"), ["stop"])
    mutate("query-plan-v3-action-broadening.json", "query-plan-v3.json", ("controller", "action_allowlist"), ["requirement_hybrid_retrieval", "arbitrary_sql"])
    mutate("query-retrieval-attempt-graph-drift.json", "query-retrieval-attempt.json", ("graph_sha256_after",), THREE)
    mutate("query-retrieval-attempt-provider-call.json", "query-retrieval-attempt.json", ("provider_call_count",), 1)
    mutate("query-plan-observation-false-progress.json", "query-plan-observation.json", ("progress",), "no_progress")
    mutate("query-plan-terminal-incomplete-coverage.json", "query-plan-terminal.json", ("coverage_complete",), False)
    mutate("query-l1-control-policy-bounded-default.json", "query-l1-control-policy.json", ("mode",), "bounded")
    mutate("query-l1-controller-eval-comparison-invariant-drift.json", "query-l1-controller-eval-comparison.json", ("status",), "NOT_COMPARABLE")
    mutate("retrieval-release-evaluation-unbound-pair.json", "retrieval-release-evaluation-v2.json", ("final_coverage", "covered"), 0)
    mutate("l1-wave-receipt-failed-command-pass.json", "l1-wave-01-receipt.json", ("commands", 0, "status"), "FAIL")
    return cases


def refresh_inventory() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    rows = []
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        properties = schema.get("properties", {})
        version = properties.get("schema_version", {}).get("const")
        if not isinstance(version, str):
            version = schema["$id"].rsplit(":", 1)[-1]
        rows.append({"path": path.relative_to(ROOT).as_posix(), "schema_id": schema["$id"], "schema_version": version, "sha256": sha256_file(path)})
    inventory["schemas"] = rows
    inventory.pop("canonical_digest", None)
    inventory["canonical_digest"] = {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": digest(inventory)}
    write_json(INVENTORY, inventory)


def main() -> None:
    write_json(SCHEMA_ROOT / "query-plan-v3.schema.json", plan_v3_schema())
    for filename, schema in new_schemas().items():
        write_json(SCHEMA_ROOT / filename, schema)
    valid = valid_fixtures()
    for filename, artifact in valid.items():
        write_json(VALID_ROOT / filename, artifact)
    for filename, artifact in invalid_fixtures(valid).items():
        write_json(INVALID_ROOT / filename, artifact)
    policy = {
        "policy_id": "query-l1-control-policy",
        "schema_version": "1.0.0",
        "mode": "off",
        "action_allowlist": ["requirement_hybrid_retrieval", "stop"],
        "budget": {"max_additional_attempts": 1, "max_actions": 2, "max_unique_records": 50, "max_result_bytes": 100000, "max_elapsed_ms": 30000},
        "progress_rule": "newly_covered_material_requirements",
        "terminal_reasons": ["COMPLETED", "EXPLICIT_GAP", "NO_PROGRESS", "BUDGET_EXHAUSTED", "UNAUTHORIZED", "CONFLICT", "HITL_REQUIRED", "SHADOW"],
        "provider_calls_before_l2d": 0,
        "graph_mutations_before_l2d": 0,
        "runtime_default": "off",
        "extensions": {},
    }
    write_json(ROOT / "requirements" / "query-l1-control-policy.json", policy)
    refresh_inventory()
    print(json.dumps({"schemas_created": 11, "valid_fixtures_created": len(valid), "invalid_fixtures_created": len(invalid_fixtures(valid))}, sort_keys=True))


if __name__ == "__main__":
    main()
