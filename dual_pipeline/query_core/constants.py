"""Constants, query stages, limits, and regexes for query execution."""

from __future__ import annotations

import re
from pathlib import Path


def _claim_new_directory(path: Path, *, label: str) -> None:
    """Claim a one-run directory without allowing stale artifact reuse."""

    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise FileExistsError(f"{label} must be a new directory: {path}") from exc


QUERY_STAGES = [
    "U0",
    "L1",
    "L0",
    "C0.1",
    "C0.2",
    "C0.3",
    "L2D",
    "PA",
    "L2",
    "X1",
    "X2",
    "X3",
    "L4",
]
MAX_QUERY_COVERAGE_REQUIREMENTS = 128
MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES = 262_144
MAX_REQUIRED_MATERIAL_RECORDS = 6
MAX_L2_INPUT_RECORDS_PER_LANE = 32
GRAPH_HOPS_BY_QUERY_TYPE = {
    "exact_lookup": 2,
    "current_state": 2,
    "current_state_corroboration": 2,
    "project_status_current": 2,
    "project_status_history": 2,
    "relationship_explanation": 3,
    "cross_workstream_synthesis": 3,
    "gap_risk_inconsistency": 3,
    "opportunity_nba": 3,
    "candidate_promotion_review": 3,
}
CITATION_ONLY_SOURCE_EVIDENCE_RETRIEVAL_MODES = frozenset(
    {
        "candidate_review",
        "status_snapshot_current",
        "status_snapshot_history",
    }
)

_COVERAGE_OUTPUT_ONLY = re.compile(
    r"\b(?:slide|slides|ppt|powerpoint|table|column|format|fenced block|"
    r"markdown|word count|font|legend|bullet|bullets|executive brief|"
    r"executive summary)\b",
    re.IGNORECASE,
)
_COVERAGE_CONCLUSION = re.compile(
    r"\b(?:recommendation|suggest|proposed next step|suggested action)\b",
    re.IGNORECASE,
)


def _entities(question: str, *, default_to_nee: bool = True) -> list[str]:
    values: list[str] = []
    for pattern, canonical in (
        (r"\bNEA\b", "NEA"),
        (r"\bNEE\b", "NEE"),
        (r"\bApex Global\b", "Apex Global Enterprises"),
        (r"\bAPEX\b", "APEX"),
        (r"\bEDO\b", "Enterprise Data Office"),
        (r"\bEnterprise Data Office\b", "Enterprise Data Office"),
        (r"\bDatabricks\b", "Databricks"),
        (r"\bAWS\b", "AWS"),
        (r"\bLakehouse\b", "Lakehouse"),
        (r"\bGridOpti\b", "GridOpti"),
        (r"\bProject Spring\b", "Project Spring"),
        (r"\bMaximo\b", "Maximo"),
        (r"\bServiceNow\b", "ServiceNow"),
        (r"\bSAP\b", "SAP"),
        (r"\bKafka\b", "Kafka"),
        (r"\bPDLC\b", "PDLC"),
        (r"\bSherpa\b", "Project Beacon"),
        (r"\bChakra\b", "Chakra"),
        (r"\bChristina Harris\b", "Christina Harris"),
    ):
        if re.search(pattern, question, re.I):
            values.append(canonical)
    deduped = list(dict.fromkeys(values))
    return deduped or (["NEE"] if default_to_nee else [])
