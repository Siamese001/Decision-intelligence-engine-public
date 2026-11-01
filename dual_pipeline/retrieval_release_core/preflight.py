"""
dual_pipeline/retrieval_release_core/preflight.py

Preflight checks for retrieval release evaluation schemas, policies, and contracts
prior to expensive model initialization and vector index generation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.contracts import SCHEMA_DIR
from dual_pipeline.core import ROOT, read_json



class RetrievalSchemaPreflightError(RuntimeError):
    """Raised when release schema or evaluation contract preflight fails."""

    def __init__(self, message: str, details: Dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


def _validate_schema_metric_nullability(schema: Dict[str, Any]) -> None:
    case_props = (
        schema.get("$defs", {}).get("caseResult", {}).get("properties", {})
        or schema.get("properties", {}).get("case_results", {}).get("items", {}).get("properties", {})
    )
    for metric_name in ("candidate_recall_at_k", "candidate_reciprocal_rank", "candidate_ndcg_at_k"):
        prop = case_props.get(metric_name, {})
        allowed_types = prop.get("type")
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        if not isinstance(allowed_types, list) or "null" not in allowed_types:
            raise RetrievalSchemaPreflightError(
                f"Schema error: {metric_name} must allow null in retrieval release evaluation schema, got {allowed_types}",
                {"metric": metric_name, "type": allowed_types},
            )


def _validate_probe_fixture(schema: Dict[str, Any]) -> None:
    import copy
    from dual_pipeline.contracts import CONTRACT_ROOT, registry
    from dual_pipeline.contract_validation.validator import validate_schema

    fixture_path = CONTRACT_ROOT / "fixtures" / "valid" / "retrieval-release-evaluation.json"
    if not fixture_path.is_file():
        return
    try:
        reg = registry()
        fixture = read_json(fixture_path)
        probe_payload = copy.deepcopy(fixture)
        if probe_payload.get("case_results"):
            probe_payload["case_results"][0]["candidate_recall_at_k"] = None
            probe_payload["case_results"][0]["candidate_reciprocal_rank"] = None
            probe_payload["case_results"][0]["candidate_ndcg_at_k"] = None
        validate_schema(probe_payload, schema, reg)
    except Exception as err:
        raise RetrievalSchemaPreflightError(
            f"Probe evaluation payload with nullable metrics failed schema validation: {err}",
            {"error": str(err)},
        ) from err


def preflight_retrieval_release_schema(
    schema_path: Path | None = None,
    policy_path: Path | None = None,
) -> Dict[str, Any]:
    """Validate that retrieval evaluation schema and policy support nullable metrics and are valid.

    Runs BEFORE model initialization, embedding backend loading, or vector sidecar generation.
    """
    s_path = schema_path or (SCHEMA_DIR / "retrieval-release-evaluation.schema.json")
    if not s_path.is_file():
        raise RetrievalSchemaPreflightError(
            f"Retrieval release evaluation schema not found at {s_path}",
            {"schema_path": str(s_path)},
        )

    try:
        schema = read_json(s_path)
    except Exception as err:
        raise RetrievalSchemaPreflightError(
            f"Failed to read retrieval release evaluation schema: {err}",
            {"schema_path": str(s_path), "error": str(err)},
        ) from err

    _validate_schema_metric_nullability(schema)
    _validate_probe_fixture(schema)

    # Policy validation
    p_path = policy_path or (ROOT / "requirements" / "retrieval-release-evaluation-policy.json")
    if p_path.is_file():
        try:
            policy = read_json(p_path)
            if not isinstance(policy.get("thresholds"), dict):
                raise RetrievalSchemaPreflightError("Evaluation policy missing required 'thresholds' dict")
        except Exception as err:
            raise RetrievalSchemaPreflightError(
                f"Evaluation policy validation failed: {err}",
                {"policy_path": str(p_path), "error": str(err)},
            ) from err

    return {
        "status": "PASS",
        "nullable_metrics_verified": True,
        "schema_path": str(s_path),
        "policy_path": str(p_path) if p_path.is_file() else None,
    }
