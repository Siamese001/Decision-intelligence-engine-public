"""Retrieval release evaluation and suite discovery records for runtime evidence.

Extracted to preserve modularity, file line budgets, and strict cognitive complexity limits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .primitives import _read_json, _resolved


def _artifact_digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "canonical_digest"}
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _validate_evaluation_record_fields(record: dict[str, Any]) -> None:
    if (
        not isinstance(record["evaluation_id"], str)
        or not record["evaluation_id"].strip()
        or record["execution_tier"] != "E3"
        or record["status"] not in {"PASS", "FAIL"}
        or not isinstance(record["evaluated_at"], str)
        or not record["evaluated_at"].strip()
        or not isinstance(record["prepared_receipt_id"], str)
        or not record["prepared_receipt_id"].strip()
        or not isinstance(record["prepared_receipt_digest"], str)
        or len(record["prepared_receipt_digest"]) != 64
        or not isinstance(record["suite_id"], str)
        or not record["suite_id"].strip()
        or not isinstance(record["suite_digest"], str)
        or len(record["suite_digest"]) != 64
        or not isinstance(record["acceptance_policy"], dict)
        or not isinstance(record["coverage"], dict)
        or not isinstance(record["aggregate"], dict)
        or not isinstance(record["gates"], dict)
    ):
        raise ValueError(
            "retrieval release evaluation identity, status, or evidence binding is invalid"
        )


def _retrieval_evaluation_record(
    path: Path,
    runtime_root: Path | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    try:
        value = _read_json(path)
        canonical = value.get("canonical_digest")
        baseline = value.get("baseline_pair")
        candidate = value.get("candidate_pair")
        if (
            value.get("contract_id") != "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
            or not isinstance(canonical, dict)
            or canonical.get("algorithm") != "sha256"
            or canonical.get("scope") != "artifact_without_canonical_digest"
            or canonical.get("value") != _artifact_digest(value)
            or not isinstance(baseline, dict)
            or not isinstance(candidate, dict)
        ):
            raise ValueError(
                "retrieval release evaluation contract, digest, or pair binding is invalid"
            )
        required_pair_fields = (
            "embedding_backend",
            "generation_id",
            "graph_snapshot",
            "graph_sha256",
            "sidecar_sha256",
            "model_id",
            "model_revision",
            "production_qualified",
        )
        if any(
            pair.get(field) is None
            for pair in (baseline, candidate)
            for field in required_pair_fields
        ):
            raise ValueError("retrieval release evaluation pair binding is incomplete")
        record = {
            "path": str(_resolved(path)),
            "runtime_root": str(_resolved(runtime_root)) if runtime_root else None,
            "evaluation_id": value.get("evaluation_id"),
            "execution_tier": value.get("execution_tier"),
            "status": value.get("status"),
            "evaluated_at": value.get("evaluated_at"),
            "prepared_receipt_id": value.get("prepared_receipt_id"),
            "prepared_receipt_digest": value.get("prepared_receipt_digest"),
            "suite_id": value.get("suite_id"),
            "suite_digest": value.get("suite_digest"),
            "acceptance_policy": value.get("acceptance_policy"),
            "embedding_backend": candidate.get("embedding_backend"),
            "model_id": candidate.get("model_id"),
            "model_revision": candidate.get("model_revision"),
            "model_manifest_digest": candidate.get("model_manifest_digest"),
            "production_qualified": candidate.get("production_qualified"),
            "baseline_generation_id": baseline.get("generation_id"),
            "candidate_generation_id": candidate.get("generation_id"),
            "baseline_pair": baseline,
            "candidate_pair": candidate,
            "evaluation_digest": canonical["value"],
            "coverage": value.get("coverage"),
            "aggregate": value.get("aggregate"),
            "gates": value.get("gates"),
        }
        _validate_evaluation_record_fields(record)
        return record, None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return (
            None,
            {
                "code": "RETRIEVAL_EVALUATION_INVALID",
                "path": str(_resolved(path)),
                "detail": str(exc),
            },
        )


def _retrieval_evaluation_paths(
    runtime_roots: Iterable[Path],
    evidence_roots: Iterable[Path],
) -> list[tuple[Path, Path | None]]:
    paths: dict[Path, Path | None] = {}
    for root in runtime_roots:
        runtime_root = _resolved(root)
        evaluation_root = runtime_root / "retrieval_evaluations"
        if evaluation_root.is_dir():
            for path in evaluation_root.glob("retrieval-release-evaluation-*.json"):
                paths[_resolved(path)] = runtime_root
    for root in evidence_roots:
        evidence_root = _resolved(root)
        if not evidence_root.is_dir():
            continue
        for path in evidence_root.rglob("retrieval-release-evaluation-*.json"):
            paths.setdefault(_resolved(path), None)
    return sorted(
        paths.items(),
        key=lambda item: str(item[0]).casefold(),
    )


def _retrieval_suite_record(
    path: Path,
    runtime_root: Path | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    try:
        value = _read_json(path)
        canonical = value.get("canonical_digest")
        if (
            value.get("contract_id") != "urn:nee:dual-pipeline:retrieval-release-suite:1.0.0"
            or not isinstance(canonical, dict)
            or canonical.get("algorithm") != "sha256"
            or canonical.get("scope") != "artifact_without_canonical_digest"
            or canonical.get("value") != _artifact_digest(value)
            or value.get("execution_tier") != "E3"
            or not isinstance(value.get("suite_id"), str)
            or not isinstance(value.get("acceptance_policy"), dict)
            or not isinstance(value.get("cases"), list)
            or not value["cases"]
            or not isinstance(value.get("coverage"), list)
        ):
            raise ValueError("retrieval release suite contract, digest, or bindings are invalid")
        extension = value.get("extensions", {}).get(
            "com.partner_advisory.nee.automatic-suite",
            {},
        )
        return (
            {
                "path": str(_resolved(path)),
                "runtime_root": (str(_resolved(runtime_root)) if runtime_root else None),
                "suite_id": value["suite_id"],
                "suite_digest": canonical["value"],
                "execution_tier": value["execution_tier"],
                "created_at": value.get("created_at"),
                "acceptance_policy": value["acceptance_policy"],
                "case_count": len(value["cases"]),
                "coverage_record_count": len(value["coverage"]),
                "source": extension.get("source", "explicit"),
                "prepared_receipt_id": extension.get("prepared_receipt_id"),
                "prepared_receipt_digest": extension.get("prepared_receipt_digest"),
                "generation_policy": extension.get("generation_policy"),
            },
            None,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return (
            None,
            {
                "code": "RETRIEVAL_EVALUATION_SUITE_INVALID",
                "path": str(_resolved(path)),
                "detail": str(exc),
            },
        )


def _retrieval_suite_paths(
    runtime_roots: Iterable[Path],
    evidence_roots: Iterable[Path],
) -> list[tuple[Path, Path | None]]:
    paths: dict[Path, Path | None] = {}
    for root in runtime_roots:
        runtime_root = _resolved(root)
        suite_root = runtime_root / "retrieval_evaluation_suites"
        if suite_root.is_dir():
            for path in suite_root.rglob("retrieval-release-suite-*.json"):
                paths[_resolved(path)] = runtime_root
    for root in evidence_roots:
        evidence_root = _resolved(root)
        if not evidence_root.is_dir():
            continue
        for path in evidence_root.rglob("retrieval-release-suite-*.json"):
            paths.setdefault(_resolved(path), None)
    return sorted(
        paths.items(),
        key=lambda item: str(item[0]).casefold(),
    )


def collect_retrieval_evaluations(
    runtime_roots: Iterable[Path],
    evidence_roots: Iterable[Path],
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Scan and parse all retrieval release evaluations across roots."""
    records: list[dict[str, Any]] = []
    for path, runtime_root in _retrieval_evaluation_paths(runtime_roots, evidence_roots):
        record, issue = _retrieval_evaluation_record(path, runtime_root)
        if issue is not None:
            issues.append(issue)
        elif record is not None:
            records.append(record)
    records.sort(
        key=lambda value: (
            str(value["evaluation_id"]).casefold(),
            str(value["embedding_backend"]).casefold(),
            str(value["candidate_generation_id"]).casefold(),
        )
    )
    return records


def collect_retrieval_suites(
    runtime_roots: Iterable[Path],
    evidence_roots: Iterable[Path],
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Scan and parse all retrieval release suites across roots."""
    records: list[dict[str, Any]] = []
    for path, runtime_root in _retrieval_suite_paths(runtime_roots, evidence_roots):
        record, issue = _retrieval_suite_record(path, runtime_root)
        if issue is not None:
            issues.append(issue)
        elif record is not None:
            records.append(record)
    records.sort(
        key=lambda value: (
            str(value["suite_id"]).casefold(),
            str(value.get("prepared_receipt_id") or "").casefold(),
        )
    )
    return records
