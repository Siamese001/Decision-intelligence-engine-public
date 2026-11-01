#!/usr/bin/env python3
"""Run the deterministic, provider-free W4 daily intelligence evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, utc_now
from dual_pipeline.daily_intelligence import (
    DailyIntelligenceError,
    project_change_brief,
    project_decision_inbox,
    project_dependency_warnings,
    project_meeting_brief,
    project_readiness_gaps,
)
from dual_pipeline.temporal_observations import build_observation
from tests.decision_intelligence_support import authorization, monitor_for

DEFAULT_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
DEFAULT_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W4_DAILY_INTELLIGENCE_CONTRACT.json"
DEFAULT_OUTPUT = ROOT / ".local" / "w4-daily-intelligence-evaluation-20260810" / "w4-evaluation.json"
AS_OF = "2026-08-09T00:00:00Z"
PREVIOUS_AS_OF = "2026-08-05T00:00:00Z"
CODE_PATHS = (
    ROOT / "tools" / "run_w4_daily_intelligence_evaluation.py",
    ROOT / "dual_pipeline" / "daily_intelligence.py",
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


def _observation(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "scope_id": "initiative-a",
        "dimension": "delivery",
        "value": {"status": "on_track"},
        "observed_at": "2026-08-08T12:00:00Z",
        "recorded_at": "2026-08-08T12:05:00Z",
        "source_as_of": "2026-08-08T12:00:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "source_assertion_ids": ["assertion-current"],
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return build_observation(**values)


def _decision(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_id": "decision-a",
        "scope_id": "initiative-a",
        "title": "Approve pilot scope",
        "decision_state": "OPEN",
        "owner": "Delivery lead",
        "consequence_of_delay": "Delays the pilot start",
        "source_assertion_ids": ["assertion-decision"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return values


def _dependency(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "dependency_id": "dependency-a",
        "scope_id": "initiative-a",
        "depends_on_scope_id": "initiative-b",
        "title": "Needs data-platform access",
        "dependency_state": "OPEN",
        "impact": "Blocks pilot integration",
        "source_assertion_ids": ["assertion-dependency"],
        "source_as_of": "2026-08-08T00:00:00Z",
        "recorded_at": "2026-08-08T00:05:00Z",
        "stale_after": "2026-08-20T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
    }
    values.update(overrides)
    return values


def _guard() -> dict[str, object]:
    return {
        "known_at": AS_OF,
        "authorization_context": authorization(),
        "reference_monitor": monitor_for(
            [
                "assertion-current",
                "assertion-older",
                "assertion-expired",
                "assertion-left",
                "assertion-right",
                "assertion-decision",
                "assertion-dependency",
            ]
        ),
    }


def _case(*, passed: bool, result: dict[str, object] | None = None, detail: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"passed": passed}
    if result is not None:
        artifact = result.get("artifact")
        payload["state"] = result.get("state")
        payload["gaps"] = result.get("gaps")
        payload["artifact_id"] = artifact.get("artifact_id") if isinstance(artifact, dict) else None
        payload["authority"] = artifact.get("authority") if isinstance(artifact, dict) else None
    if detail is not None:
        payload["detail"] = detail
    return payload


def evaluate(graph: Path, contract: Path) -> dict[str, object]:
    graph_before = sha256_file(graph) if graph.exists() else None
    sqlite_counts = _inspect_sqlite_graph(graph)
    older = _observation(
        value={"status": "at_risk"},
        observed_at="2026-08-01T12:00:00Z",
        recorded_at="2026-08-01T12:05:00Z",
        source_as_of="2026-08-01T12:00:00Z",
        stale_after="2026-08-20T00:00:00Z",
        source_assertion_ids=["assertion-older"],
        superseded_at="2026-08-08T12:00:00Z",
        superseded_by="obs-current",
    )
    current = _observation()
    expired_readiness = _observation(
        dimension="readiness",
        value={"status": "ready"},
        stale_after="2026-08-08T23:00:00Z",
        source_assertion_ids=["assertion-expired"],
    )
    conflicting_left = _observation(
        dimension="readiness",
        observation_id="readiness-left",
        value={"status": "ready"},
        source_assertion_ids=["assertion-left"],
    )
    conflicting_right = _observation(
        dimension="readiness",
        observation_id="readiness-right",
        value={"status": "not_ready"},
        source_assertion_ids=["assertion-right"],
    )
    change = project_change_brief(
        [older, current],
        previous_as_of=PREVIOUS_AS_OF,
        as_of=AS_OF,
        audience="internal_consulting",
        **_guard(),
    )
    stale_gap = project_readiness_gaps(
        [expired_readiness],
        scope_ids=["initiative-a"],
        required_dimensions=["readiness"],
        audience="internal_consulting",
        as_of=AS_OF,
        **_guard(),
    )
    conflict_gap = project_readiness_gaps(
        [conflicting_left, conflicting_right],
        scope_ids=["initiative-a"],
        required_dimensions=["readiness"],
        audience="internal_consulting",
        as_of=AS_OF,
        **_guard(),
    )
    decision = project_decision_inbox(
        [_decision(owner=None, decision_state="BLOCKED")],
        audience="internal_consulting",
        as_of=AS_OF,
        **_guard(),
    )
    dependency = project_dependency_warnings(
        [_dependency()], audience="internal_consulting", as_of=AS_OF, **_guard()
    )
    meeting = project_meeting_brief(
        [change, stale_gap, decision, dependency], audience="internal_consulting", as_of=AS_OF, **_guard()
    )
    no_evidence = project_readiness_gaps(
        [],
        scope_ids=["initiative-a"],
        required_dimensions=["readiness"],
        audience="internal_consulting",
        as_of=AS_OF,
        **_guard(),
    )
    try:
        mixed = _observation(audiences=["nee-engineering", "internal_working_team"])
        project_change_brief(
            [mixed],
            previous_as_of=PREVIOUS_AS_OF,
            as_of=AS_OF,
            audience="nee-engineering",
            **_guard(),
        )
    except DailyIntelligenceError:
        audience_boundary_passed = True
    else:
        audience_boundary_passed = False
    cases = {
        "material_observation_change": _case(
            passed=change["state"] == "READY_FOR_REVIEW"
            and change["artifact"] is not None
            and change["artifact"]["authority"] == "DERIVED_CANDIDATE_ONLY"
            and set(change["artifact"]["source_assertion_ids"])
            == {"assertion-current", "assertion-older"},
            result=change,
        ),
        "stale_readiness_evidence": _case(
            passed=stale_gap["state"] == "STALE_OR_UNKNOWN"
            and stale_gap["artifact"] is not None
            and stale_gap["artifact"]["state"] == "STALE_OR_UNKNOWN",
            result=stale_gap,
        ),
        "conflicting_readiness_evidence": _case(
            passed=conflict_gap["state"] == "HITL_REQUIRED"
            and conflict_gap["artifact"] is not None
            and conflict_gap["artifact"]["review_state"] == "HITL_REQUIRED",
            result=conflict_gap,
        ),
        "unowned_unresolved_decision": _case(
            passed=decision["state"] == "HITL_REQUIRED"
            and decision["artifact"] is not None
            and decision["artifact"]["graph_mutation_authorized"] is False,
            result=decision,
        ),
        "cross_scope_dependency_warning": _case(
            passed=dependency["state"] == "READY_FOR_REVIEW"
            and dependency["artifact"] is not None
            and set(dependency["artifact"]["scope_ids"]) == {"initiative-a", "initiative-b"},
            result=dependency,
        ),
        "candidate_only_meeting_brief": _case(
            passed=meeting["artifact"] is not None
            and meeting["artifact"]["authority"] == "DERIVED_CANDIDATE_ONLY"
            and meeting["artifact"]["graph_mutation_authorized"] is False,
            result=meeting,
        ),
        "audience_boundary_rejection": _case(
            passed=audience_boundary_passed,
            detail="mixed client and restricted audiences are rejected before projection",
        ),
        "no_evidence_abstention": _case(
            passed=no_evidence["state"] == "STALE_OR_UNKNOWN"
            and no_evidence["artifact"] is None,
            result=no_evidence,
        ),
    }
    graph_after = sha256_file(graph) if graph.exists() else None
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_type": "w4_daily_intelligence_evaluation",
        "wave": "W4",
        "evaluation_id": "w4-daily-intelligence-evaluation-20260810",
        "executed_at": utc_now(),
        "contract_path": str(contract.resolve()),
        "contract_digest": digest(json.loads(contract.read_text(encoding="utf-8"))),
        "runtime_root": str(ROOT.resolve()),
        "command": ".venv\\Scripts\\python.exe tools\\run_w4_daily_intelligence_evaluation.py",
        "code_bindings": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
            for path in CODE_PATHS
        },
        "as_of": AS_OF,
        "cases": cases,
        "passed_cases": sum(bool(case["passed"]) for case in cases.values()),
        "total_cases": len(cases),
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "graph_path": str(graph.resolve()),
        "sqlite_table_counts": sqlite_counts,
        "graph_sha256_before": graph_before,
        "graph_sha256_after": graph_after,
        "graph_unchanged": graph_before == graph_after,
        "status": "PASS"
        if all(bool(case["passed"]) for case in cases.values())
        and graph_before == graph_after
        else "FAIL",
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
    args.output.resolve().write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output.resolve()),
                "passed_cases": result["passed_cases"],
                "total_cases": result["total_cases"],
                "graph_unchanged": result["graph_unchanged"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
