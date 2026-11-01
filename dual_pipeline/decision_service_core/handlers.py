"""Default handoff handlers and citations extraction for decision service."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

HandoffHandler = Callable[[str], Mapping[str, Any]]


def _default_general_handler(question: str) -> Mapping[str, Any]:
    return {
        "handoff_type": "GENERAL_ASSISTANT",
        "instruction": question,
        "message": "Route this request to the configured general-assistant handler.",
    }


def _default_workflow_handler(question: str) -> Mapping[str, Any]:
    return {
        "handoff_type": "OPERATIONAL_WORKFLOW",
        "instruction": question,
        "message": "Route this request to the configured operational workflow handler.",
    }


def _citations(value: object) -> list[str]:
    result: list[str] = []

    def visit(item: object, key: str | None = None) -> None:
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and key in {
            "citations",
            "evidence_ids",
            "supporting_evidence_ids",
            "counterevidence_ids",
        }:
            result.append(item)

    visit(value)
    return list(dict.fromkeys(result))


def _clarification(decision_question: str, missing_fields: Sequence[str]) -> dict[str, Any]:
    if "criteria" in missing_fields:
        prompt = (
            "Which criteria should be used to compare the options "
            "(for example impact, cost, effort, risk, and dependencies)?"
        )
    else:
        prompt = "What decision outcome should this request produce?"
    return {
        "question": decision_question,
        "missing_fields": list(missing_fields),
        "clarification_question": prompt,
    }


def _load_run_artifact(runtime_root: Any, run_id: str) -> dict[str, Any] | None:
    from pathlib import Path

    from ..core import read_json
    run_dir = Path(runtime_root) / "runs" / Path(run_id).name
    decision_path = run_dir / "decision_result.json"
    if decision_path.is_file():
        return read_json(decision_path)
    decision_results_dir = run_dir / "decision_results"
    decision_paths = sorted(decision_results_dir.glob("*/decision_result.json"))
    if len(decision_paths) == 1:
        return read_json(decision_paths[0])
    summary_path = run_dir / "query_summary.json"
    return read_json(summary_path) if summary_path.is_file() else None


__all__ = [
    "HandoffHandler",
    "_citations",
    "_clarification",
    "_default_general_handler",
    "_default_workflow_handler",
    "_load_run_artifact",
]
