#!/usr/bin/env python3
"""Run the provider-free W7 AI decision-use-case qualification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, utc_now
from dual_pipeline.decision_use_cases import (
    DecisionUseCaseError,
    evaluate_use_case,
    validate_use_case,
)
from dual_pipeline.strategic_evidence import build_evidence_packet
from dual_pipeline.temporal_observations import build_observation
from tests.decision_intelligence_support import authorization, monitor_for

DEFAULT_GRAPH = ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729" / "generations" / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3" / "graph.sqlite"
DEFAULT_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W7_AI_USE_CASE_QUALIFICATION_CONTRACT.json"
DEFAULT_PORTFOLIO = ROOT / "governance" / "decision_intelligence" / "W15_USE_CASE_PORTFOLIO.json"
DEFAULT_OUTPUT = ROOT / ".local" / "w7-ai-use-case-qualification-20260810" / "w7-evaluation.json"
AS_OF = "2026-08-09T00:00:00Z"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _observation(
    scope_id: str,
    dimension: str,
    source_id: str,
    *,
    value: Mapping[str, Any] | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    return build_observation(
        observation_id=observation_id,
        scope_id=scope_id,
        dimension=dimension,
        value=dict(value or {"status": "on_track"}),
        observed_at="2026-08-08T12:00:00Z",
        recorded_at="2026-08-08T12:05:00Z",
        source_as_of="2026-08-08T12:00:00Z",
        stale_after="2026-08-20T00:00:00Z",
        source_assertion_ids=[source_id],
        classification="internal",
        audiences=["internal_consulting"],
    )


def _packet(
    claim_id: str,
    source_id: str,
    *,
    evidence_role: str = "supporting",
    evidence_id: str | None = None,
    review_state: str = "REVIEWED",
    reviewed_by: str | None = "W7 evaluator",
    reviewed_at: str | None = "2026-08-08T00:00:00Z",
) -> dict[str, Any]:
    return build_evidence_packet(
        evidence_id=evidence_id,
        claim_id=claim_id,
        evidence_role=evidence_role,
        content={"finding": f"{claim_id}:{evidence_role}"},
        source_assertion_ids=[source_id],
        source_as_of="2026-08-08T00:00:00Z",
        recorded_at="2026-08-08T00:05:00Z",
        expires_at="2026-09-01T00:00:00Z",
        classification="internal",
        audiences=["internal_consulting"],
        review_state=review_state,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        falsification_tests=[
            {"test_id": "w7-falsification", "question": "Does this hold across more than one workstream?"}
        ],
    )


def _evaluate(
    use_case: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    packets: Iterable[Mapping[str, Any]],
    source_ids: Iterable[str],
) -> dict[str, Any]:
    return evaluate_use_case(
        use_case=use_case,
        as_of=AS_OF,
        known_at=AS_OF,
        authorization_context=authorization(),
        reference_monitor=monitor_for(source_ids),
        observations=observations,
        evidence_packets=packets,
    )


def evaluate(graph: Path, contract: Path, portfolio: Path = DEFAULT_PORTFOLIO) -> dict[str, Any]:
    graph_before = sha256_file(graph) if graph.exists() else None
    portfolio_value = _read_json(portfolio)
    use_cases = {
        item["use_case_id"]: validate_use_case(item)
        for item in portfolio_value.get("use_cases", [])
    }
    cases: dict[str, dict[str, Any]] = {}

    fixture_specs = {
        "ai-pdlc-pov": {
            "dimensions": ("delivery", "readiness"),
            "claim": "claim-ai-pdlc-pov",
            "counter": True,
        },
        "ai-productivity-metrics": {
            "dimensions": ("capacity", "adoption", "value"),
            "claim": "claim-ai-productivity-metrics",
            "counter": False,
        },
        "agentic-readiness": {
            "dimensions": ("dependency", "readiness", "delivery"),
            "claim": "claim-agentic-readiness",
            "counter": True,
        },
    }
    for use_case_id, spec in fixture_specs.items():
        use_case = use_cases[use_case_id]
        observation_ids = [f"w7-{use_case_id}-{dimension}" for dimension in spec["dimensions"]]
        observations = [
            _observation(use_case["scope_id"], dimension, source_id)
            for dimension, source_id in zip(spec["dimensions"], observation_ids)
        ]
        support_id = f"w7-{use_case_id}-support-source"
        packet_ids = [support_id]
        packets = [_packet(spec["claim"], support_id, evidence_id=f"{use_case_id}-support")]
        if spec["counter"]:
            counter_id = f"w7-{use_case_id}-counter-source"
            packet_ids.append(counter_id)
            packets.append(
                _packet(
                    spec["claim"],
                    counter_id,
                    evidence_role="counterevidence",
                    evidence_id=f"{use_case_id}-counter",
                )
            )
        result = _evaluate(use_case, observations, packets, observation_ids + packet_ids)
        expected_state = "CANDIDATE_ANSWER_WITH_GAPS" if not spec["counter"] else "CANDIDATE_ANSWER_READY"
        cases[use_case_id] = {
            "passed": result["state"] == expected_state
            and result["answer_posture"] == "CANDIDATE_ONLY"
            and bool(result["decision_context"]["source_assertion_ids_by_item"]),
            "expected_state": expected_state,
            "result": result,
        }

    pov = use_cases["ai-pdlc-pov"]
    pov_obs = [_observation(pov["scope_id"], "delivery", "w7-hard-negative-delivery")]
    pov_support = _packet("claim-ai-pdlc-pov", "w7-hard-negative-support", evidence_id="hard-negative-support")
    missing_status = _evaluate(
        pov,
        pov_obs,
        [pov_support],
        ["w7-hard-negative-delivery", "w7-hard-negative-support"],
    )
    cases["missing_required_project_status"] = {
        "passed": missing_status["state"] == "BLOCKED_STALE_OR_UNKNOWN" and missing_status["answer_posture"] == "NO_ANSWER",
        "expected_state": "BLOCKED_STALE_OR_UNKNOWN",
        "result": missing_status,
    }

    conflict_obs = [
        _observation(pov["scope_id"], "delivery", "w7-conflict-left", value={"status": "on_track"}, observation_id="w7-conflict-left"),
        _observation(pov["scope_id"], "delivery", "w7-conflict-right", value={"status": "at_risk"}, observation_id="w7-conflict-right"),
        _observation(pov["scope_id"], "readiness", "w7-conflict-readiness"),
    ]
    conflict = _evaluate(
        pov,
        conflict_obs,
        [
            _packet("claim-ai-pdlc-pov", "w7-conflict-support", evidence_id="conflict-support"),
            _packet("claim-ai-pdlc-pov", "w7-conflict-counter", evidence_role="counterevidence", evidence_id="conflict-counter"),
        ],
        ["w7-conflict-left", "w7-conflict-right", "w7-conflict-readiness", "w7-conflict-support", "w7-conflict-counter"],
    )
    cases["conflicting_project_status"] = {
        "passed": conflict["state"] == "BLOCKED_HITL_REQUIRED" and conflict["answer_posture"] == "NO_ANSWER",
        "expected_state": "BLOCKED_HITL_REQUIRED",
        "result": conflict,
    }

    unreviewed = _evaluate(
        pov,
        [
            _observation(pov["scope_id"], "delivery", "w7-unreviewed-delivery"),
            _observation(pov["scope_id"], "readiness", "w7-unreviewed-readiness"),
        ],
        [_packet("claim-ai-pdlc-pov", "w7-unreviewed-support", review_state="UNREVIEWED", reviewed_by=None, reviewed_at=None)],
        ["w7-unreviewed-delivery", "w7-unreviewed-readiness", "w7-unreviewed-support"],
    )
    cases["unreviewed_strategic_evidence"] = {
        "passed": unreviewed["state"] == "BLOCKED_HITL_REQUIRED" and unreviewed["answer_posture"] == "NO_ANSWER",
        "expected_state": "BLOCKED_HITL_REQUIRED",
        "result": unreviewed,
    }

    invalid = dict(pov)
    invalid["candidate_answer_sections"] = list(pov["candidate_answer_sections"]) + ["undeclared_section"]
    try:
        validate_use_case(invalid)
    except DecisionUseCaseError:
        section_contract = {"passed": True, "rejected": True, "expected_state": "REJECTED"}
    else:
        section_contract = {"passed": False, "rejected": False, "expected_state": "REJECTED"}
    cases["undeclared_answer_section"] = section_contract

    graph_after = sha256_file(graph) if graph.exists() else None
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": "decision_intelligence_w7_ai_use_case_qualification",
        "wave": "W7",
        "evaluation_id": "w7-ai-use-case-qualification-20260810",
        "executed_at": utc_now(),
        "contract_path": str(contract.resolve()),
        "contract_digest": digest(_read_json(contract)),
        "portfolio_path": str(portfolio.resolve()),
        "portfolio_digest": digest(portfolio_value),
        "as_of": AS_OF,
        "cases": cases,
        "passed_cases": sum(bool(item.get("passed")) for item in cases.values()),
        "total_cases": len(cases),
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "graph_path": str(graph.resolve()),
        "graph_sha256_before": graph_before,
        "graph_sha256_after": graph_after,
        "graph_unchanged": graph_before == graph_after,
    }
    payload["status"] = "PASS" if payload["passed_cases"] == payload["total_cases"] and payload["graph_unchanged"] else "FAIL"
    payload["canonical_digest"] = {"algorithm": "sha256", "value": digest(payload)}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--portfolio", type=Path, default=DEFAULT_PORTFOLIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.graph, args.contract, args.portfolio)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()), "passed_cases": result["passed_cases"], "total_cases": result["total_cases"], "graph_unchanged": result["graph_unchanged"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
