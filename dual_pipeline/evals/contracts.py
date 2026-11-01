"""Loading and validation helpers for dual-pipeline evaluation artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import validate_file
from ..core import ROOT, read_json, sha256_file


def load_suite(path: Path) -> dict[str, Any]:
    """Load one closed-schema suite and bind all declared intake package bytes."""

    suite_path = Path(path).expanduser().resolve()
    value = validate_file(suite_path)
    if value["contract_id"] not in {
        "urn:nee:dual-pipeline:eval-suite:1.0.0",
        "urn:nee:dual-pipeline:eval-suite:3.0.0",
    }:
        raise ValueError("evaluation suite has the wrong contract")
    if value["contract_id"].endswith(":1.0.0") and any(
        case["pipeline"] == "query" for case in value["cases"]
    ):
        raise ValueError(
            "legacy query evaluation suites are not executable; migrate to "
            "eval-suite 3.0.0 with non-vacuous evidence expectations"
        )
    seen: set[str] = set()
    for case in value["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError(f"evaluation suite has duplicate case ID: {case_id}")
        seen.add(case_id)
        if value["pipeline"] != "dual" and case["pipeline"] != value["pipeline"]:
            raise ValueError("evaluation case does not match suite pipeline")
        if case["pipeline"] == "ingestion":
            package = resolve_repository_file(case["package_path"])
            if sha256_file(package) != case["package_sha256"]:
                raise RuntimeError(
                    f"evaluation package hash mismatch for case {case_id}: {package}"
                )
    return value


def load_result(path: Path) -> dict[str, Any]:
    value = validate_file(Path(path).expanduser().resolve())
    if value["contract_id"] != "urn:nee:dual-pipeline:eval-run-result:1.0.0":
        raise ValueError("evaluation result has the wrong contract")
    return value


def resolve_repository_file(relative_path: str) -> Path:
    """Resolve a suite-controlled file without permitting a source-tree escape."""

    candidate = (ROOT / relative_path).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("evaluation path escapes the repository") from exc
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def write_markdown_report(path: Path, result: dict[str, Any]) -> None:
    """Write a concise, non-sensitive report next to the sealed JSON artifact."""

    lines = [
        "# Dual-pipeline evaluation result",
        "",
        f"- Evaluation: `{result['evaluation_id']}`",
        f"- Suite: `{result['suite_id']}`",
        f"- Status: `{result['status']}`",
        f"- Cases: {result['aggregate']['passed_case_count']}/{result['aggregate']['case_count']} passed",
        f"- Provider calls: {result['aggregate']['provider_call_count']}",
        f"- Graph mutations: {result['aggregate']['graph_mutation_count']}",
        "",
        "| Case | Pipeline | Status | Cause | Stage | Provider calls | Graph mutations |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for case in result["case_results"]:
        lines.append(
            "| {case_id} | {pipeline} | {status} | {cause} | {stage} | {calls} | {mutations} |".format(
                case_id=case["case_id"],
                pipeline=case["pipeline"],
                status=case["status"],
                cause=case["cause_code"] or "",
                stage=case["stage"] or "",
                calls=case["provider_call_count"],
                mutations=case["graph_mutation_count"],
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_json_artifact(path: Path) -> dict[str, Any]:
    """Read an internal run artifact after its location has been established."""

    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"evaluation artifact is not an object: {path}")
    return value
