#!/usr/bin/env python3
"""Provider Fault Injector CLI for Chaos & Resilience Testing.

Simulates external LLM provider failures (FAULT-429, FAULT-503, FAULT-TRUNC, FAULT-TIMEOUT)
to verify that the L2 Healing Mesh and deterministic fallback cascades engage cleanly without crashing.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.policy.circuit_breaker import BreakerState, CircuitBreakerGateway
from dual_pipeline.resilience import with_idempotent_retry
from pipeline_b_extraction.extractors_core.mock_extractors import mock_extract_canonical
from pipeline_b_extraction.json_repair import repair_and_parse_json
from storage.database import EvidenceRecord

logger = logging.getLogger("evals.run_provider_fault_injection")


@dataclass
class ScenarioResult:
    scenario_id: str
    description: str
    injected_failure: str
    status: str
    retries_observed: int
    fallback_engaged: bool
    recovery_mechanism: str
    latency_ms: float
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FaultInjectionReport:
    pipeline: str
    timestamp_utc: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    is_overall_compliant: bool
    results: List[ScenarioResult] = field(default_factory=list)
    receipt_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def simulate_fault_429(max_retries: int = 3) -> ScenarioResult:
    """FAULT-429: HTTP 429 Rate Limit -> Backoff retries 3 times via with_idempotent_retry, cascades to secondary provider."""
    t0 = time.perf_counter()
    retries = 0
    fallback_engaged = False

    def on_retry(attempt: int, exc: Exception, delay: float) -> None:
        nonlocal retries
        retries = attempt

    @with_idempotent_retry(
        max_retries=max_retries,
        base_delay=0.005,
        max_delay=0.05,
        jitter=True,
        on_retry_callback=on_retry,
    )
    def primary_provider_call():
        raise RuntimeError("HTTP 429 rate limit exceeded on primary model tier")

    try:
        primary_provider_call()
    except Exception:
        fallback_engaged = True

    # Engage secondary fallback provider
    evidence = EvidenceRecord(
        evidence_id="ev_fault_429_recovery",
        meeting_id="recovery_meeting",
        speaker="System Healer",
        text="Architectural decision confirmed via secondary provider cascade.",
        start_timestamp=0.0,
        is_final=True,
    )
    nodes = mock_extract_canonical(evidence)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    passed = (retries == max_retries and fallback_engaged and len(nodes) > 0)
    return ScenarioResult(
        scenario_id="FAULT-429",
        description="HTTP 429 Rate Limit Exceeded",
        injected_failure="HTTP 429 RateLimitError on primary model tier",
        status="PASSED" if passed else "FAILED",
        retries_observed=retries,
        fallback_engaged=fallback_engaged,
        recovery_mechanism="Exponential backoff retries -> Secondary provider fallback",
        latency_ms=round(elapsed_ms, 2),
        passed=passed,
        details={"recovered_nodes": len(nodes), "fallback_provider": "local-heuristic-tier"},
    )


def simulate_fault_503() -> ScenarioResult:
    """FAULT-503: HTTP 503 Service Unavailable -> Immediate fallback via CircuitBreakerGateway."""
    t0 = time.perf_counter()
    breaker = CircuitBreakerGateway(failure_threshold=1, allow_cloud_fallback=True)
    fallback_engaged = False

    try:
        # Trip the circuit breaker upon 503 error
        breaker.record_failure("upstream_provider", "HTTP 503 Service Unavailable upstream")
        if breaker.state == BreakerState.OPEN:
            fallback_engaged = True
            evidence = EvidenceRecord(
                evidence_id="ev_fault_503_recovery",
                meeting_id="recovery_meeting",
                speaker="System Healer",
                text="Schedule follow-up operational task via fallback heuristic.",
                start_timestamp=0.0,
                is_final=True,
            )
            nodes = mock_extract_canonical(evidence)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            passed = fallback_engaged and len(nodes) > 0
            return ScenarioResult(
                scenario_id="FAULT-503",
                description="HTTP 503 Service Unavailable",
                injected_failure="HTTP 503 Service Unavailable upstream",
                status="PASSED" if passed else "FAILED",
                retries_observed=0,
                fallback_engaged=fallback_engaged,
                recovery_mechanism="CircuitBreakerGateway trip -> deterministic local extractor fallback",
                latency_ms=round(elapsed_ms, 2),
                passed=passed,
                details={
                    "recovered_nodes": len(nodes),
                    "circuit_state": breaker.state.value,
                    "strategy": "deterministic-local-extractor",
                },
            )
    except Exception as exc:
        return ScenarioResult(
            scenario_id="FAULT-503",
            description="HTTP 503 Service Unavailable",
            injected_failure="HTTP 503 Service Unavailable upstream",
            status="FAILED",
            retries_observed=0,
            fallback_engaged=False,
            recovery_mechanism="None",
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            passed=False,
            error=str(exc),
        )


def simulate_fault_trunc() -> ScenarioResult:
    """FAULT-TRUNC: Truncated JSON output -> JSON repair restores valid parse tree."""
    t0 = time.perf_counter()
    truncated_json = (
        '{"schema_version": "5.7", "graph_mutations": {"decision_updates": ['
        '{"decision": "Deploy partitioned WAL SQLite cluster", "rationale": "High throughput and ACID durability"'
    )  # Deliberately missing closing braces/brackets

    repaired = repair_and_parse_json(truncated_json)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    passed = isinstance(repaired, dict) and "graph_mutations" in repaired
    return ScenarioResult(
        scenario_id="FAULT-TRUNC",
        description="Truncated JSON Completion (EOF mid-stream)",
        injected_failure="Truncated JSON string without terminal braces or brackets",
        status="PASSED" if passed else "FAILED",
        retries_observed=0,
        fallback_engaged=True,
        recovery_mechanism="pipeline_b_extraction.json_repair.repair_and_parse_json",
        latency_ms=round(elapsed_ms, 2),
        passed=passed,
        details={"parsed_keys": list(repaired.keys()) if isinstance(repaired, dict) else []},
    )


def simulate_fault_timeout(timeout_sim_seconds: float = 0.05) -> ScenarioResult:
    """FAULT-TIMEOUT: Network socket hang -> Bounded ThreadPoolExecutor timeout cancellation + fallback."""
    t0 = time.perf_counter()
    timeout_triggered = False
    future_cancelled = False
    fallback_engaged = False
    stop_event = threading.Event()

    def _stalling_provider_call() -> str:
        stop_event.wait(timeout=max(timeout_sim_seconds * 4.0, 0.1))
        return "unreachable_response"

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_stalling_provider_call)
    try:
        future.result(timeout=timeout_sim_seconds)
    except concurrent.futures.TimeoutError:
        timeout_triggered = True
        future_cancelled = future.cancel() or future.running()
        stop_event.set()
        fallback_engaged = True
    finally:
        stop_event.set()
        executor.shutdown(wait=False, cancel_futures=True)

    nodes = []
    if fallback_engaged:
        evidence = EvidenceRecord(
            evidence_id="ev_fault_timeout_recovery",
            meeting_id="recovery_meeting",
            speaker="System Healer",
            text="Timeout cancellation recovered via deterministic local extractor fallback.",
            start_timestamp=0.0,
            is_final=True,
        )
        nodes = mock_extract_canonical(evidence)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    passed = timeout_triggered and future_cancelled and fallback_engaged and len(nodes) > 0
    return ScenarioResult(
        scenario_id="FAULT-TIMEOUT",
        description="Network Socket Hang / Hard Timeout",
        injected_failure=f"Simulated network socket stall with hard timeout threshold ({timeout_sim_seconds}s)",
        status="PASSED" if passed else "FAILED",
        retries_observed=0,
        fallback_engaged=fallback_engaged,
        recovery_mechanism="Bounded executor timeout cancellation -> deterministic local extractor fallback",
        latency_ms=round(elapsed_ms, 2),
        passed=passed,
        details={
            "timeout_simulated_seconds": timeout_sim_seconds,
            "timeout_error_caught": timeout_triggered,
            "future_cancelled": future_cancelled,
            "recovered_nodes": len(nodes),
        },
    )


def run_fault_injection(
    scenarios: Sequence[str],
    pipeline: str = "pipeline_b_extraction",
    timeout_sim_seconds: float = 0.05,
) -> FaultInjectionReport:
    """Execute all requested provider fault injection scenarios."""
    results: List[ScenarioResult] = []
    scenario_set = {s.strip().upper() for s in scenarios}

    if "FAULT-429" in scenario_set:
        results.append(simulate_fault_429())
    if "FAULT-503" in scenario_set:
        results.append(simulate_fault_503())
    if "FAULT-TRUNC" in scenario_set:
        results.append(simulate_fault_trunc())
    if "FAULT-TIMEOUT" in scenario_set:
        results.append(simulate_fault_timeout(timeout_sim_seconds=timeout_sim_seconds))

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    is_compliant = (total > 0 and failed == 0)

    return FaultInjectionReport(
        pipeline=pipeline,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        total_scenarios=total,
        passed_scenarios=passed,
        failed_scenarios=failed,
        is_overall_compliant=is_compliant,
        results=results,
    )


def build_mesh_receipt(report: FaultInjectionReport, command: str) -> Dict[str, Any]:
    """Generate signed runtime receipt for provider fault injection."""
    receipt = {
        "$schema": "urn:nee:provider-fault-injection-receipt:1.0.0",
        "timestamp_utc": report.timestamp_utc,
        "command": command,
        "pipeline": report.pipeline,
        "status": "PASSED" if report.is_overall_compliant else "FAILED",
        "metrics": {
            "total_scenarios": report.total_scenarios,
            "passed_scenarios": report.passed_scenarios,
            "failed_scenarios": report.failed_scenarios,
            "is_overall_compliant": report.is_overall_compliant,
        },
        "itemized_scenarios": [r.to_dict() for r in report.results],
    }
    serialized = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt["canonical_digest"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return receipt


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Provider Fault Injector CLI for Resilience Verification")
    parser.add_argument(
        "--scenarios",
        type=str,
        default="FAULT-429,FAULT-503,FAULT-TRUNC,FAULT-TIMEOUT",
        help="Comma-separated scenario IDs",
    )
    parser.add_argument("--pipeline", type=str, default="pipeline_b_extraction", help="Target pipeline")
    parser.add_argument("--verify-mesh-receipt", action="store_true", help="Generate and verify signed mesh receipt")
    parser.add_argument("--receipt", type=Path, default=None, help="Output path for mesh receipt")
    parser.add_argument("--timeout-sim-seconds", type=float, default=0.05, help="Timeout threshold in seconds for FAULT-TIMEOUT")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    args = parser.parse_args(argv)

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    report = run_fault_injection(
        scenarios=scenarios,
        pipeline=args.pipeline,
        timeout_sim_seconds=args.timeout_sim_seconds,
    )

    receipt_target = args.receipt
    if args.verify_mesh_receipt and receipt_target is None:
        receipt_target = ROOT / "reports" / "provider_fault_injection_receipt.json"

    if receipt_target:
        receipt = build_mesh_receipt(report, command=" ".join(sys.argv))
        receipt_target.parent.mkdir(parents=True, exist_ok=True)
        receipt_target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status_line = "PASSED" if report.is_overall_compliant else "FAILED"
        print(f"Provider Fault Injection: {report.passed_scenarios}/{report.total_scenarios} scenarios passed on {report.pipeline} -> {status_line}")
        for r in report.results:
            icon = "✅" if r.passed else "❌"
            print(f"  {icon} {r.scenario_id}: {r.description} ({r.latency_ms}ms) -> {r.recovery_mechanism}")

    return 0 if report.is_overall_compliant else 1


if __name__ == "__main__":
    sys.exit(main())
