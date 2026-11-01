"""Wave 4: Cross-Platform Retrieval Concurrency, Multi-Tenant Scale & Latency SLA Evaluation.

Evaluates PolicyGatedRetrievalEngine under high-concurrency (50 concurrent worker threads),
certifying:
  1. Multi-Tenant Zero-Leakage: Zero INTERNAL_ADVISORY items leaked in CLIENT_SAFE (stealth) queries.
  2. Tactical Quarantined Node Suppression: Zero quarantined nodes returned in production queries.
  3. Latency Distribution: p95 <= 150.0 ms, max <= 7500.0 ms.
  4. Concurrency Integrity: 100% successful query completion under 50 simultaneous workers.
  5. Memory Stability: RSS memory delta <= 500 MB.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline_c_retrieval import (
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
)


def populate_benchmark_graph(mgr: DatabaseManager, num_canonical: int = 500, num_advisory: int = 250) -> None:
    """Populates an isolated benchmark database with multi-tenant canonical, quarantined, and advisory nodes."""
    # 1. Base Evidence Records
    for i in range(1, 101):
        mgr.append_evidence(
            EvidenceRecord(
                evidence_id=f"ev_scale_{i:04d}",
                meeting_id=f"meet_scale_{i % 10:02d}",
                speaker=f"Architect_{i % 5}",
                text=f"Benchmark transcript segment {i} regarding WAL journal mode, architecture stability, and data pipelines.",
                start_timestamp=float(i * 10),
            )
        )

    # 2. Canonical Nodes (80% valid strategic, 20% quarantined tactical)
    for i in range(1, num_canonical + 1):
        is_quarantine = (i % 5 == 0)
        mgr.upsert_canonical_node(
            CanonicalGraphNode(
                node_id=f"c_node_{i:04d}",
                evidence_id=f"ev_scale_{(i % 100) + 1:04d}",
                type="ARCHITECTURAL_DECISION" if i % 2 == 0 else "SYSTEM_COMPONENT",
                name=f"Canonical Feature {i} WAL Optimization" if not is_quarantine else f"Quarantined Tactical Note {i}",
                properties={"context": f"Detailed production context for canonical asset {i} with database index hints."},
                strategic_value=round(5.0 + (i % 50) / 10.0, 1),
                tacticality=1.0 if not is_quarantine else 9.0,
                is_quarantined=is_quarantine,
                quarantine_reason="TACTICAL_SUPPRESSION" if is_quarantine else None,
            )
        )

    # 3. Internal Advisory Nodes (strictly firewalled, stealth_score = 1.0)
    categories = [
        AdvisoryCategory.STAKEHOLDER_DYNAMICS,
        AdvisoryCategory.DECISION_INFLUENCE,
        AdvisoryCategory.ADOPTION_RISK,
        AdvisoryCategory.BLOCKER,
    ]
    for i in range(1, num_advisory + 1):
        cat = categories[i % len(categories)]
        mgr.upsert_internal_advisory_node(
            InternalAdvisoryNode(
                node_id=f"adv_node_{i:04d}",
                evidence_id=f"ev_scale_{(i % 100) + 1:04d}",
                type=cat.value,
                name=f"Internal Advisory Risk {i}: Executive Leadership Alignment",
                category=cat,
                properties={
                    "rationale": f"Confidential internal political assessment {i} regarding career trajectory and steering committee friction.",
                    "signal": f"Strategic advisory signal {i} concerning negotiation leverage.",
                },
                strategic_value=round(7.0 + (i % 30) / 10.0, 1),
                tacticality=2.0,
                stealth_score=1.0,
                is_quarantined=False,
                namespace=INTERNAL_ADVISORY_NAMESPACE,
            )
        )


def run_retrieval_concurrency_evaluation(
    num_workers: int = 50,
    num_queries: int = 100,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Executes the high-concurrency retrieval benchmark."""
    print("=" * 80)
    print("Wave 4: Policy-Gated Retrieval Concurrency & Scale Evaluation")
    print(f"Workers: {num_workers} concurrent threads | Queries: {num_queries}")
    print("=" * 80)

    process = psutil.Process(os.getpid())
    mem_before_mb = process.memory_info().rss / (1024 * 1024)

    temp_dir = Path(tempfile.mkdtemp(prefix="nee-retrieval-scale-"))
    try:
        db_mgr = DatabaseManager(base_dir=temp_dir, allow_unencrypted_dev_fallback=True)
        print("Populating benchmark database (750 nodes across canonical & advisory)...")
        populate_benchmark_graph(db_mgr, num_canonical=500, num_advisory=250)

        retrieval_engine = PolicyGatedRetrievalEngine(db_manager=db_mgr)

        sample_queries = [
            ("WAL journal mode", True),
            ("Architecture Optimization", True),
            ("Data pipeline architecture", True),
            ("Executive Leadership Alignment", True),  # Client query searching for advisory wording
            ("Tactical Note", True),                   # Client query searching for quarantined wording
            ("WAL journal mode", False),               # Unmasked advisory query
            ("Executive Leadership Alignment", False), # Unmasked advisory query
            ("System Component", True),
            ("Database index hints", True),
            ("Strategic advisory signal", False),
        ]

        # Formulate query workload (cycling through samples)
        workload = [sample_queries[i % len(sample_queries)] for i in range(num_queries)]

        latencies_ms: list[float] = []
        cross_tenant_leaks: int = 0
        quarantine_leaks: int = 0
        errors: list[str] = []

        print(f"\nExecuting {num_queries} queries across {num_workers} simultaneous workers...")
        start_time = time.perf_counter()

        def execute_single_query(query_tuple: tuple[str, bool]) -> tuple[float, bool, bool, str | None]:
            q_text, stealth = query_tuple
            t0 = time.perf_counter()
            try:
                results = retrieval_engine.search(query=q_text, stealth_mode=stealth, limit=10)
                duration_ms = (time.perf_counter() - t0) * 1000.0

                leak = False
                if stealth:
                    for r in results:
                        if r.namespace == INTERNAL_ADVISORY_NAMESPACE or "Confidential" in r.snippet:
                            leak = True
                            break

                quarantine_leak = any(r.is_quarantined for r in results)
                return duration_ms, leak, quarantine_leak, None
            except Exception as e:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                return duration_ms, False, False, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            with tqdm(total=num_queries, desc="Concurrent Retrieval", unit="query") as pbar:
                futures = [executor.submit(execute_single_query, w) for w in workload]
                for future in concurrent.futures.as_completed(futures):
                    dur_ms, is_leak, is_q_leak, err = future.result()
                    latencies_ms.append(dur_ms)
                    if is_leak:
                        cross_tenant_leaks += 1
                    if is_q_leak:
                        quarantine_leaks += 1
                    if err:
                        errors.append(err)
                    pbar.update(1)

        total_elapsed = time.perf_counter() - start_time
        mem_after_mb = process.memory_info().rss / (1024 * 1024)
        mem_delta_mb = max(0.0, mem_after_mb - mem_before_mb)

        # Metrics computation
        p50 = float(np.percentile(latencies_ms, 50))
        p90 = float(np.percentile(latencies_ms, 90))
        p95 = float(np.percentile(latencies_ms, 95))
        p99 = float(np.percentile(latencies_ms, 99))
        max_lat = float(np.max(latencies_ms))
        mean_lat = float(np.mean(latencies_ms))
        qps = float(num_queries / total_elapsed) if total_elapsed > 0 else 0.0

        # Gates
        gates = {
            "1. p95_latency (<= 150ms)": p95 <= 150.0,
            "2. max_latency (<= 7500ms)": max_lat <= 7500.0,
            "3. zero_cross_tenant_leakage (0 leaks)": cross_tenant_leaks == 0,
            "4. zero_quarantine_leakage (0 leaks)": quarantine_leaks == 0,
            "5. concurrency_integrity (0 errors)": len(errors) == 0,
            "6. memory_stability (delta <= 500MB)": mem_delta_mb <= 500.0,
        }

        all_passed = all(gates.values())
        status = "PASS" if all_passed else "FAIL"

        print("\n" + "=" * 80)
        print(f"CONCURRENCY & SCALE EVALUATION RESULTS: {status}")
        print("=" * 80)
        for g_name, g_pass in gates.items():
            print(f"  [{'PASS' if g_pass else 'FAIL'}] {g_name}")

        print(f"\nLatency Distribution ({len(latencies_ms)} queries under {num_workers} concurrent workers):")
        print(f"  Mean Latency: {mean_lat:.2f} ms")
        print(f"  p50 Latency:  {p50:.2f} ms")
        print(f"  p90 Latency:  {p90:.2f} ms")
        print(f"  p95 Latency:  {p95:.2f} ms (Threshold: 150.0 ms)")
        print(f"  p99 Latency:  {p99:.2f} ms")
        print(f"  Max Latency:  {max_lat:.2f} ms (Threshold: 7500.0 ms)")
        print(f"  Throughput:   {qps:.1f} QPS")
        print(f"  Memory Delta: {mem_delta_mb:.2f} MB (RSS: {mem_before_mb:.1f}MB -> {mem_after_mb:.1f}MB)")

        receipt = {
            "evaluation_contract": "urn:nee:retrieval:concurrency-and-scale-evaluation:1.0.0",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": status,
            "concurrency": {
                "max_workers": num_workers,
                "total_queries": num_queries,
                "total_elapsed_seconds": round(total_elapsed, 3),
                "qps": round(qps, 2),
            },
            "latency_distribution_ms": {
                "mean": round(mean_lat, 2),
                "p50": round(p50, 2),
                "p90": round(p90, 2),
                "p95": round(p95, 2),
                "p99": round(p99, 2),
                "max": round(max_lat, 2),
            },
            "security_and_filtering": {
                "cross_tenant_leaks": cross_tenant_leaks,
                "quarantine_leaks": quarantine_leaks,
                "error_count": len(errors),
            },
            "memory_usage": {
                "before_mb": round(mem_before_mb, 2),
                "after_mb": round(mem_after_mb, 2),
                "delta_mb": round(mem_delta_mb, 2),
            },
            "gates": gates,
        }

        if output_path is None:
            output_path = ROOT / "reports" / "wave4_retrieval_concurrency_scale_receipt.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"\nSaved concurrency evaluation receipt to: {output_path}")

        return receipt

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wave 4 Retrieval Concurrency & Scale Evaluation")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent worker threads (default: 10)")
    parser.add_argument("--queries", type=int, default=50, help="Total queries to execute (default: 50)")
    parser.add_argument("--output", type=Path, default=None, help="Output receipt path")
    args = parser.parse_args()

    run_retrieval_concurrency_evaluation(
        num_workers=args.workers,
        num_queries=args.queries,
        output_path=args.output,
    )
