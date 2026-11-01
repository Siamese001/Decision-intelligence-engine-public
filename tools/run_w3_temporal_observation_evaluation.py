#!/usr/bin/env python3
"""Run the deterministic, provider-free W3 temporal observation evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, utc_now
from dual_pipeline.temporal_observations import (
    build_observation,
    observation_state,
    project_observation_state,
)
from tests.decision_intelligence_support import authorization, monitor_for

DEFAULT_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
DEFAULT_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W3_TEMPORAL_OBSERVATION_CONTRACT.json"
DEFAULT_OUTPUT = ROOT / ".local" / "clearance4-w3-temporal-20260809" / "w3-evaluation.json"
CODE_PATHS = (
    ROOT / "tools" / "run_w3_temporal_observation_evaluation.py",
    ROOT / "dual_pipeline" / "temporal_observations.py",
    ROOT / "dual_pipeline" / "reference_monitor.py",
    ROOT / "tests" / "decision_intelligence_support.py",
)


def _inspect_sqlite_graph(db_path: Path) -> dict[str, int]:
    """Query live SQLite database tables (`assertions`, `temporal_observations`, `evidence_units`) if present."""
    counts = {"assertions": 0, "temporal_observations": 0, "evidence_units": 0}
    if not db_path.is_file():
        return counts
    from storage.connection import get_db_connection
    conn = get_db_connection(db_path, readonly=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for tbl in ("assertions", "temporal_observations", "evidence_units"):
            if tbl in tables:
                val = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                counts[tbl] = int(val[0]) if val else 0
            elif tbl == "assertions" and "assertion" in tables:
                val = conn.execute("SELECT COUNT(*) FROM assertion").fetchone()
                counts["assertions"] = int(val[0]) if val else 0
    finally:
        conn.close()
    return counts


def _observation(**overrides):
    values = {
        "scope_id": "project-1",
        "dimension": "delivery",
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": ["assertion-1"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _project(rows, *, as_of: str):
    observations = list(rows)
    source_ids = [
        source_id
        for item in observations
        for source_id in item["source_assertion_ids"]
    ]
    return project_observation_state(
        observations,
        scope_id="project-1",
        dimension="delivery",
        as_of=as_of,
        known_at=as_of,
        audience="internal_consulting",
        authorization_context=authorization(),
        reference_monitor=monitor_for(source_ids),
    )


def evaluate(graph: Path, contract: Path) -> dict[str, object]:
    graph_sha_before = sha256_file(graph) if graph.exists() else None
    sqlite_counts = _inspect_sqlite_graph(graph)
    older = _observation(
        value={"status": "at_risk"},
        observed_at="2026-08-01T12:00:00Z",
        recorded_at="2026-08-01T12:05:00Z",
        source_as_of="2026-08-01T12:00:00Z",
        stale_after="2026-08-10T00:00:00Z",
        source_assertion_ids=["assertion-0"],
        superseded_at="2026-08-08T12:00:00Z",
        superseded_by="obs-current",
    )
    current = _observation()
    expired = _observation(stale_after="2026-08-08T23:59:00Z", source_assertion_ids=["assertion-expired"])
    conflicting = _observation(
        observation_id="obs-conflict",
        value={"status": "at_risk"},
        source_assertion_ids=["assertion-conflict"],
    )
    superseded = _observation(
        observation_id="obs-superseded",
        superseded_at="2026-08-08T18:00:00Z",
        superseded_by="obs-new",
        source_assertion_ids=["assertion-superseded"],
    )
    future = _observation(
        observed_at="2026-08-15T12:00:00Z",
        valid_from="2026-08-15T12:00:00Z",
        recorded_at="2026-08-15T12:05:00Z",
        source_as_of="2026-08-15T12:00:00Z",
        stale_after="2026-08-30T00:00:00Z",
        source_assertion_ids=["assertion-future"],
    )
    as_of = "2026-08-09T00:00:00Z"
    cases = {
        "current": {
            "passed": observation_state(current, as_of=as_of) == "CURRENT",
            "state": observation_state(current, as_of=as_of),
        },
        "history": {
            "passed": _project([older, current], as_of=as_of)["value"]
            == {"status": "on_track"},
            "projection": _project([older, current], as_of=as_of),
        },
        "expired": {
            "passed": observation_state(expired, as_of=as_of) == "STALE_OR_UNKNOWN",
            "state": observation_state(expired, as_of=as_of),
        },
        "missing": {
            "passed": _project([], as_of=as_of)["state"]
            == "STALE_OR_UNKNOWN",
            "projection": _project([], as_of=as_of),
        },
        "conflict": {
            "passed": _project([current, conflicting], as_of=as_of)["state"]
            == "CONFLICT",
            "projection": _project([current, conflicting], as_of=as_of),
        },
        "superseded": {
            "passed": observation_state(superseded, as_of=as_of) == "SUPERSEDED",
            "state": observation_state(superseded, as_of=as_of),
        },
        "future": {
            "passed": observation_state(future, as_of=as_of) == "FUTURE",
            "state": observation_state(future, as_of=as_of),
        },
    }
    graph_sha_after = sha256_file(graph) if graph.exists() else None
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_type": "clearance4_w3_temporal_observation_evaluation",
        "wave": "W3",
        "evaluation_id": "clearance4-w3-temporal-20260809",
        "executed_at": utc_now(),
        "contract_path": str(contract.resolve()),
        "contract_digest": digest(json.loads(contract.read_text(encoding="utf-8"))),
        "runtime_root": str(ROOT.resolve()),
        "command": ".venv\\Scripts\\python.exe tools\\run_w3_temporal_observation_evaluation.py",
        "code_bindings": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
            for path in CODE_PATHS
        },
        "as_of": as_of,
        "cases": cases,
        "passed_cases": sum(bool(case["passed"]) for case in cases.values()),
        "total_cases": len(cases),
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "graph_path": str(graph.resolve()),
        "sqlite_table_counts": sqlite_counts,
        "graph_sha256_before": graph_sha_before,
        "graph_sha256_after": graph_sha_after,
        "graph_unchanged": graph_sha_before == graph_sha_after,
        "status": "PASS" if all(bool(case["passed"]) for case in cases.values()) and graph_sha_before == graph_sha_after else "FAIL",
    }
    payload["canonical_digest"] = {"algorithm": "sha256", "value": digest(payload)}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", "--db-path", dest="graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.graph, args.contract)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()), "passed_cases": result["passed_cases"], "total_cases": result["total_cases"], "graph_unchanged": result["graph_unchanged"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
