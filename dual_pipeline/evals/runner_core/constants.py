"""Constants, exceptions, and boundary validators for sealed evaluation runner."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ...core import sha256_file, write_json
from ...drift_guard import DriftError
from ...index import (
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    EmbeddingBackend,
    render_retrieval_records,
)
from ...l2 import load_l2_config
from ...query_router import load_query_routing_policy
from ...release import ReleaseManager
from ...x1_quality import load_x1_quality_config


class EvaluationFailure(RuntimeError):
    """A harness-level failure that prevents a trustworthy evaluation result."""


FAILED_WORKSPACE_RETENTION_DAYS = 7
WORKSPACE_RETENTION_FILE = ".retention.json"
PAIR_BINDING_FIELDS = (
    "generation_id",
    "graph_sha256",
    "sidecar_sha256",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
    "production_qualified",
)


def _pair_binding(pair: dict[str, Any]) -> dict[str, Any]:
    binding = {field: pair.get(field) for field in PAIR_BINDING_FIELDS}
    if any(binding[field] is None for field in PAIR_BINDING_FIELDS):
        raise EvaluationFailure("evaluation case has an incomplete graph/index pair binding")
    return binding


def _evaluation_policy_digests() -> dict[str, str]:
    """Load every default policy used by query evaluation at the boundary."""

    return {
        "routing": load_query_routing_policy().policy_digest,
        "l2": load_l2_config().config_digest,
        "x1": load_x1_quality_config().config_digest,
        "hybrid_retrieval": HYBRID_RETRIEVAL_POLICY_DIGEST,
    }


def _assert_expected_records_present(
    cases: Iterable[dict[str, Any]],
    graph: Path,
) -> None:
    """Reject a suite whose required record IDs are absent from the graph bytes."""

    required_by_case = {
        case["case_id"]: sorted(
            set(case.get("expected", {}).get("required_record_ids", []))
        )
        for case in cases
        if case["pipeline"] == "query"
        and case.get("expected", {}).get("required_record_ids")
    }
    if not required_by_case:
        return
    available = {
        record["record_id"]
        for record in render_retrieval_records(graph, "evaluation-preflight")
    }
    missing = {
        case_id: sorted(set(record_ids) - available)
        for case_id, record_ids in required_by_case.items()
        if set(record_ids) - available
    }
    if missing:
        raise DriftError(
            "EVAL_EXPECTED_CORPUS_DRIFT",
            "evaluation suite requires retrieval records absent from the supplied graph",
            expected=missing,
            observed={
                "graph_sha256": sha256_file(graph),
                "available_record_count": len(available),
            },
        )


def _validated_template_pair_binding(
    manager: ReleaseManager,
    pair: dict[str, Any],
    backend: EmbeddingBackend,
) -> dict[str, Any]:
    """Fail before cases run unless the template is a verified BGE graph/index pair."""

    try:
        manager.validate_pair(pair)
    except Exception as exc:
        raise EvaluationFailure(
            "eval-run requires a valid BGE graph/index pair with graph and sidecar bindings"
        ) from exc
    binding = _pair_binding(pair)
    expected = {
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
    }
    mismatches = {
        field: {"pair": binding[field], "backend": value}
        for field, value in expected.items()
        if binding[field] != value
    }
    if mismatches:
        raise EvaluationFailure(
            "eval-run template pair does not match the BGE backend: "
            f"{mismatches}"
        )
    return binding


def _cache_binding_matches_backend(
    semantic_cache: Any,
    backend: EmbeddingBackend,
) -> bool:
    if not isinstance(semantic_cache, dict):
        return False
    binding_result = semantic_cache.get("embedding_binding")
    if not isinstance(binding_result, dict):
        return False
    binding = binding_result.get("binding")
    return (
        binding_result.get("status") in {"BOUND", "REBUILT"}
        and isinstance(binding, dict)
        and binding.get("embedding_backend") == backend.backend_id
        and binding.get("model_id") == backend.model_id
        and binding.get("model_revision") == backend.model_revision
        and binding.get("model_manifest_digest") == backend.model_manifest_digest
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evaluation workspace time must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _audiences(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _case_artifact_path(case_dir: Path, value: dict[str, Any]) -> Path:
    path = case_dir / "case-result.json"
    write_json(path, value)
    return path


def _requirement_expectations_met(
    expected_rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    """Match declared requirement oracles to sealed runtime coverage outcomes."""

    outcome_by_id = {row["requirement_id"]: row for row in outcomes}
    checks: list[dict[str, Any]] = []
    for expected in expected_rows:
        matches = []
        needle = expected["label_contains"].casefold()
        for requirement in manifest["requirements"]:
            if needle not in requirement["requirement_label"].casefold():
                continue
            outcome = outcome_by_id.get(requirement["requirement_id"])
            if outcome is None:
                continue
            passed = (
                outcome["outcome"] in expected["expected_outcomes"]
                and len(outcome["record_ids"]) >= expected["minimum_record_count"]
                and set(expected.get("required_record_ids", []))
                <= set(outcome["record_ids"])
                and set(expected.get("required_support_directions", []))
                <= set(outcome["support_directions"])
            )
            matches.append(
                {
                    "requirement_id": requirement["requirement_id"],
                    "requirement_label": requirement["requirement_label"],
                    "outcome": outcome["outcome"],
                    "record_ids": outcome["record_ids"],
                    "support_directions": outcome["support_directions"],
                    "passed": passed,
                }
            )
        checks.append(
            {
                "label_contains": expected["label_contains"],
                "passed": any(row["passed"] for row in matches),
                "matches": matches,
            }
        )
    return all(row["passed"] for row in checks), checks


def _failure_result(
    *,
    case: dict[str, Any],
    case_dir: Path,
    error: Exception,
) -> dict[str, Any]:
    drift = error.as_dict() if isinstance(error, DriftError) else None
    payload = {
        "case_id": case["case_id"],
        "pipeline": case["pipeline"],
        "status": "FAIL",
        "failure_category": "DRIFT" if drift is not None else "PIPELINE_ERROR",
        "cause_code": drift["code"] if drift is not None else type(error).__name__,
        "stage": None,
        "provider_initialization_count": 0,
        "provider_call_count": 0,
        "answer_present": False,
        "graph_mutation_count": 0,
        "directive_status": None,
        "terminal_receipt_digest": None,
        "hard_gates": {"execution_completed": False},
        "error": str(error),
    }
    if drift is not None:
        payload["drift"] = drift
    path = _case_artifact_path(case_dir, payload)
    return {key: payload[key] for key in (
        "case_id", "pipeline", "status", "failure_category", "cause_code", "stage",
        "provider_initialization_count", "provider_call_count", "answer_present",
        "graph_mutation_count", "directive_status", "terminal_receipt_digest", "hard_gates"
    )} | {"artifact_path": str(path)}


def _assert_operator_backend(backend: EmbeddingBackend) -> None:
    approved = {"flagembedding-bge-m3", "flagembedding-bge-m3-dev", "flagembedding-bge-m3-internal"}
    if backend.backend_id not in approved:
        raise EvaluationFailure(
            "eval-run requires a real BGE-M3 embedding backend; "
            f"{backend.backend_id} is not accepted"
        )
    if backend.model_id != "BAAI/bge-m3":
        raise EvaluationFailure("eval-run requires model_id BAAI/bge-m3")
    if getattr(backend, "retrieval_encoder", None) != "real-bge-m3":
        raise EvaluationFailure("eval-run requires a real BGE-M3 retrieval encoder")
    revision = str(getattr(backend, "model_revision", ""))
    manifest_digest = str(getattr(backend, "model_manifest_digest", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise EvaluationFailure("eval-run requires an immutable 40-character BGE model revision")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise EvaluationFailure("eval-run requires a BGE model manifest digest")
    verifier = getattr(backend, "verify_snapshot", None)
    if not callable(verifier):
        raise EvaluationFailure("eval-run requires BGE snapshot verification")
    snapshot = verifier()
    if snapshot.get("model_id", backend.model_id) != backend.model_id:
        raise EvaluationFailure("BGE snapshot model binding mismatch")
    if snapshot.get("model_revision", revision) != revision:
        raise EvaluationFailure("BGE snapshot revision mismatch")
    if snapshot.get("manifest_digest", manifest_digest) != manifest_digest:
        raise EvaluationFailure("BGE snapshot manifest mismatch")
