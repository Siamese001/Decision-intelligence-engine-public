"""
dual_pipeline/decision_engine_core/options.py

Viable option synthesis and caller/answer option alignment.
"""

from __future__ import annotations

from typing import Any, Mapping

from dual_pipeline.core import stable_id

from .constants import extract_tokens


def answer_options(answer: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if answer is None:
        return []
    raw = answer.get("opportunities", [])
    if not isinstance(raw, list):
        return []
    options: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        label = row.get("opportunity")
        rationale = row.get("why_prioritize")
        if not isinstance(label, str) or not label.strip():
            continue
        citations = [value for value in row.get("citations", []) if isinstance(value, str)]
        evidence_ids = [
            value for value in row.get("evidence_ids", []) if isinstance(value, str)
        ]
        if not citations and not evidence_ids:
            continue
        options.append(
            {
                "option_id": stable_id("decision-option", label, citations, length=24),
                "label": label.strip(),
                "source": "evidence_bound_answer_option",
                "rationale": str(rationale or "").strip() or None,
                "record_ids": list(dict.fromkeys(citations)),
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            }
        )
    return options


def build_viable_options(
    *,
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    answer: Mapping[str, Any] | None,
    query_type: str | None = None,
) -> list[dict[str, Any]]:
    """Build options without ever promoting retrieval rank into preference."""

    caller_options = [dict(item) for item in frame["options"]]
    answ_options = answer_options(answer)
    records = evidence_pack["records"]
    if caller_options:
        if query_type == "candidate_promotion_review":
            # Candidate disposition is assessed from lanes below. Do not attach
            # every token-matched record to both caller options first.
            return [
                option | {"record_ids": [], "evidence_ids": []}
                for option in caller_options
            ]
        result: list[dict[str, Any]] = []
        for option in caller_options:
            option_tokens = extract_tokens(option["label"])
            matches = [
                record
                for record in records
                if (option_tokens & extract_tokens(f"{record['title']} {record['text']}"))
                or record.get("answer_use") == "decision_constraint"
            ]
            result.append(
                option
                | {
                    "record_ids": [record["record_id"] for record in matches],
                    "evidence_ids": sorted(
                        {value for record in matches for value in record["evidence_ids"]}
                    ),
                }
            )
        return result
    return answ_options
