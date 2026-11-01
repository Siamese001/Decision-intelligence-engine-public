#!/usr/bin/env python3
"""Run the deterministic provider-free W6 strategic evidence evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file
from dual_pipeline.strategic_evidence import (
    StrategicEvidenceError,
    build_evidence_packet,
    project_synthesis_workspace,
    validate_evidence_packet,
)
from tests.decision_intelligence_support import authorization, monitor_for, source_assertion

DEFAULT_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
DEFAULT_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W6_STRATEGIC_EVIDENCE_CONTRACT.json"
DEFAULT_OUTPUT = ROOT / ".local" / "clearance6-w6-strategic-evidence-20260809" / "w6-evaluation.json"


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


def _packet(**overrides):
    values = {
        "claim_id": "claim-ai-pdlc",
        "evidence_role": "supporting",
        "content": {"finding": "pilot reduced rework"},
        "source_assertion_ids": ["assertion-1"],
        "source_as_of": "2026-08-01T00:00:00Z",
        "recorded_at": "2026-08-01T00:05:00Z",
        "expires_at": "2026-09-01T00:00:00Z",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "review_state": "REVIEWED",
        "reviewed_by": "Amit Ayer",
        "reviewed_at": "2026-08-02T00:00:00Z",
        "falsification_tests": [{"test_id": "test-1", "question": "Does the result replicate across two teams?"}],
    }
    values.update(overrides)
    return build_evidence_packet(**values)


def _project(packets, *, audience="internal_consulting", as_of="2026-08-09T00:00:00Z"):
    rows = list(packets)
    source_ids = [
        source_id for item in rows for source_id in item["source_assertion_ids"]
    ]
    return project_synthesis_workspace(
        rows,
        claim_id="claim-ai-pdlc",
        audience=audience,
        as_of=as_of,
        known_at=as_of,
        authorization_context=authorization(audiences=[audience]),
        reference_monitor=monitor_for(source_ids),
    )


def evaluate(graph: Path, contract: Path) -> dict[str, object]:
    graph_before = sha256_file(graph) if graph.exists() else None
    sqlite_counts = _inspect_sqlite_graph(graph)
    support = _packet()
    counter = _packet(evidence_id="counter-1", evidence_role="counterevidence", content={"finding": "benefit did not replicate"}, source_assertion_ids=["assertion-2"])
    expired = _packet(evidence_id="expired-1", expires_at="2026-08-08T00:00:00Z")
    unreviewed = _packet(evidence_id="unreviewed-1", review_state="UNREVIEWED", reviewed_by=None, reviewed_at=None)
    conflicted = _packet(evidence_id="conflicted-1", review_state="CONFLICTED", reviewed_by=None, reviewed_at=None)
    no_falsification = _packet(evidence_id="no-test-1", falsification_tests=[])
    mixed_audience = _packet(evidence_id="mixed-1", audiences=["client_approved_external", "internal_working_team"])
    context = _packet(evidence_id="context-1", evidence_role="context")
    as_of = "2026-08-09T00:00:00Z"
    cases = {
        "reviewed_support_and_counterevidence": _project([support, counter], as_of=as_of),
        "support_only_gap": _project([support], as_of=as_of),
        "expired_evidence": _project([expired], as_of=as_of),
        "unreviewed_evidence": _project([unreviewed], as_of=as_of),
        "conflicted_evidence": _project([conflicted], as_of=as_of),
        "missing_falsification_test": _project([no_falsification], as_of=as_of),
        "missing_supporting_evidence": _project([context], as_of=as_of),
    }
    try:
        _project([mixed_audience], audience="client_approved_external", as_of=as_of)
    except StrategicEvidenceError:
        audience_boundary = {"state": "HITL_REQUIRED", "boundary_rejected": True}
    else:
        audience_boundary = {"state": "UNSAFE", "boundary_rejected": False}
    cases["audience_boundary_violation"] = audience_boundary
    integrity_monitor = monitor_for(["assertion-1"])
    unknown_packet = _packet(source_assertion_ids=["unknown-source"])
    try:
        project_synthesis_workspace(
            [unknown_packet],
            claim_id="claim-ai-pdlc",
            audience="internal_consulting",
            as_of=as_of,
            known_at=as_of,
            authorization_context=authorization(),
            reference_monitor=integrity_monitor,
        )
    except StrategicEvidenceError:
        unknown_lineage = {"state": "HITL_REQUIRED", "boundary_rejected": True}
    else:
        unknown_lineage = {"state": "UNSAFE", "boundary_rejected": False}
    cases["unknown_source_lineage"] = unknown_lineage

    tampered = _packet()
    tampered["citation_bindings"] = [
        {
            "source_assertion_id": "assertion-1",
            "snapshot_digest": "0" * 64,
            "content_digest": source_assertion("assertion-1")["content_digest"],
            "locator": source_assertion("assertion-1")["locator"],
            "claim_digest": "0" * 64,
        }
    ]
    try:
        validate_evidence_packet(tampered, reference_monitor=integrity_monitor)
    except StrategicEvidenceError:
        tampered_citation = {"state": "HITL_REQUIRED", "boundary_rejected": True}
    else:
        tampered_citation = {"state": "UNSAFE", "boundary_rejected": False}
    cases["tampered_citation_integrity"] = tampered_citation

    claim_tampered = _packet()
    claim_tampered["citation_bindings"] = [
        {
            "source_assertion_id": "assertion-1",
            "snapshot_digest": source_assertion("assertion-1")["snapshot_digest"],
            "content_digest": source_assertion("assertion-1")["content_digest"],
            "locator": source_assertion("assertion-1")["locator"],
            "claim_digest": "f" * 64,
        }
    ]
    try:
        validate_evidence_packet(claim_tampered, reference_monitor=integrity_monitor)
    except StrategicEvidenceError:
        claim_binding = {"state": "HITL_REQUIRED", "boundary_rejected": True}
    else:
        claim_binding = {"state": "UNSAFE", "boundary_rejected": False}
    cases["claim_content_binding_mismatch"] = claim_binding
    expected_states = {
        "reviewed_support_and_counterevidence": "READY_WITH_COUNTEREVIDENCE",
        "support_only_gap": "READY_SUPPORT_ONLY",
        "expired_evidence": "STALE_OR_UNKNOWN",
        "unreviewed_evidence": "HITL_REQUIRED",
        "conflicted_evidence": "HITL_REQUIRED",
        "missing_falsification_test": "HITL_REQUIRED",
        "audience_boundary_violation": "HITL_REQUIRED",
        "missing_supporting_evidence": "STALE_OR_UNKNOWN",
        "unknown_source_lineage": "HITL_REQUIRED",
        "tampered_citation_integrity": "HITL_REQUIRED",
        "claim_content_binding_mismatch": "HITL_REQUIRED",
    }
    case_results = {
        name: {"passed": result["state"] == expected_states[name], "result": result, "expected_state": expected_states[name]}
        for name, result in cases.items()
    }
    graph_after = sha256_file(graph) if graph.exists() else None
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_type": "clearance6_w6_strategic_evidence_evaluation",
        "evaluation_id": "clearance6-w6-strategic-evidence-20260809",
        "contract_path": str(contract.resolve()),
        "contract_digest": digest(json.loads(contract.read_text(encoding="utf-8"))),
        "as_of": as_of,
        "cases": case_results,
        "passed_cases": sum(bool(item["passed"]) for item in case_results.values()),
        "total_cases": len(case_results),
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "graph_path": str(graph.resolve()),
        "sqlite_table_counts": sqlite_counts,
        "graph_sha256_before": graph_before,
        "graph_sha256_after": graph_after,
        "graph_unchanged": graph_before == graph_after,
        "status": "PASS" if all(bool(item["passed"]) for item in case_results.values()) and graph_before == graph_after else "FAIL",
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
