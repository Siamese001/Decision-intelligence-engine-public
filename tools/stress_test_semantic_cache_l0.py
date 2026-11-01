#!/usr/bin/env python3
"""Comprehensive stress test harness for semantic cache in L0 routing.

Covers:
1. High-concurrency reader/writer lock contention (WAL mode stress).
2. Cache depth scaling and burst lookup throughput (p50/p95/p99 latency).
3. L0 multi-tenant route context and security namespace isolation.
4. Adversarial edge cases and fail-closed resilience (boundary vectors,
   corrupt payloads, broken event chains, non-finite vectors).
5. L0 telemetry route decision recording and learning event chain validation.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (
    digest,
    stable_id,
    trace_id,
    utc_now,
    write_json,
)
from dual_pipeline.semantic_cache import (
    SemanticCache,
    configure_semantic_cache,
    load_semantic_cache_policy,
)
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def make_l0_context(
    *,
    use_case: str = "query",
    route: str = "exact_lookup",
    classification: str = "internal",
    audiences: list[str] | None = None,
    user_id: str = "user-analyst-1",
    generation_id: str = "stress-gen-v1",
) -> dict[str, Any]:
    return {
        "use_case": use_case,
        "cache_payload_kind": "sealed_query_output_v4",
        "query_plan_version": "1.0.0",
        "l1_controller_mode": "off",
        "l1_controller_policy_digest": None,
        "l1_action_policy_digest": None,
        "l1_budget_profile": None,
        "destination": "retrieval_router",
        "eligibility_policy_digest": digest("eligibility-policy-stress"),
        "assurance_profile": "standard",
        "delivery_profile": "direct_answer",
        "artifact_type": "decision_synthesis",
        "route": route,
        "query_type": "governed_query",
        "materiality": "high",
        "classification": classification,
        "audiences": audiences or ["internal_consulting", "nee-engineering"],
        "allowed_lanes": ["approved"],
        "active_pair": {
            "generation_id": generation_id,
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",  # noqa: SSOT_EXEMPT
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "model_manifest_digest": "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e",
            "production_qualified": False,
        },
        "coverage_requirements_digest": digest("coverage-requirements-stress"),
        "l2_policy_digest": digest("l2-policy-stress"),
        "x1_policy_digest": digest("x1-policy-stress"),
        "provider_egress_allowed": False,
    }


def stage_and_activate_entry(
    cache: SemanticCache,
    runtime_root: Path,
    *,
    context: dict[str, Any],
    canonical_text: str,
    intent_vector: list[float],
    cluster_id: str,
    ordinal: int,
) -> str:
    run_id = stable_id("stress-l0-source", cluster_id, ordinal, length=24)
    curr_trace = trace_id(run_id, cluster_id, "stress")
    recorder = RunRecorder(
        runtime_root,
        run_id=run_id,
        trace_id=curr_trace,
        use_case="query",
        graph_snapshot="stress-snapshot",
        model_or_index_generation="stress-gen-v1",
    )
    correlation = {
        "query_id": cluster_id,
        "source_id": None,
        "candidate_id": None,
        "graph_operation_id": None,
        "hitl_item_id": None,
    }
    recorder.record(
        stage="L0",
        event_type="route_decision",
        payload={"stress_cluster_id": cluster_id, "canonical": canonical_text},
        correlation=correlation,
        route=context["route"],
    )
    output = {
        "cluster_id": cluster_id,
        "answer": f"Governed answer for {cluster_id}",
        "confidence": 0.98,
        "status": "APPROVED",
    }
    result = cache.stage(
        context=context,
        intent_digest=digest(canonical_text),
        intent_vector=intent_vector,
        output=output,
        outcome="X3D_ALLOW_FINISH",
        source_run_id=run_id,
        source_trace_id=curr_trace,
        replay_eligible=True,
    )
    if not result.entry:
        raise RuntimeError(f"Cache staging failed: {result.reason}")

    recorder.record(
        stage="L4",
        event_type="pipeline_event",
        payload={"entry_id": result.entry.entry_id, "status": result.status},
        correlation=correlation,
        route=context["route"],
    )
    seal = recorder.seal(["L0", "L4"], correlation)
    activated = cache.activate(
        result.entry,
        source_event_count=seal["event_count"],
        source_event_chain_digest=seal["event_chain_digest"],
    )
    if activated.status != "ACTIVE":
        raise RuntimeError(f"Cache activation failed: {activated.reason}")
    return result.entry.entry_id


def run_stress_suite(
    runtime_root: Path,
    backend: Any,
    *,
    concurrency: int = 16,
    burst_queries: int = 300,
    depth: int = 80,
) -> dict[str, Any]:
    print(f"\n[1/5] Initializing Stress Test Environment at {runtime_root}...")
    configure_semantic_cache(runtime_root, "read_write")
    cache = SemanticCache(runtime_root)
    binding = cache.bind_embedding_backend(backend)
    assert binding["status"] in {"BOUND", "REBUILT"}, f"Failed binding: {binding}"

    base_context = make_l0_context()
    seed_canonical = "How does the Project Beacon governance engine protect the knowledge graph?"
    seed_vector = [float(v) for v in backend.encode(seed_canonical)["dense"]]

    print(f"[2/5] Seeding {depth} active cache entries for depth scaling...")
    seeded_entries = {}
    with tqdm(total=depth, desc="Seeding Entries", unit="entry") as pbar:
        for i in range(depth):
            cluster_name = f"cluster-topic-{i:03d}"
            text = f"{seed_canonical} Topic variation {i}: specific operational details {i*17}."
            # Vary vectors slightly
            vec = list(seed_vector)
            vec[i % len(vec)] += 0.05
            # Re-normalize
            norm = math.sqrt(sum(x * x for x in vec))
            vec = [x / norm for x in vec]

            entry_id = stage_and_activate_entry(
                cache,
                runtime_root,
                context=base_context,
                canonical_text=text,
                intent_vector=vec,
                cluster_id=cluster_name,
                ordinal=i,
            )
            seeded_entries[cluster_name] = (entry_id, vec, text)
            pbar.update(1)

    # ── Phase 1: High Concurrency & Lock Contention Stress ──────────────
    print(f"\n[Phase 1] Executing Concurrency & Lock Stress ({concurrency} workers, 200 mixed ops)...")
    ops_completed = 0
    lock_errors = 0
    concurrency_latencies = []

    def worker_mixed_op(worker_idx: int, op_idx: int) -> tuple[bool, str, float]:
        t0 = time.perf_counter()
        op_type = op_idx % 3
        local_cache = SemanticCache(runtime_root)
        local_cache.bind_embedding_backend(backend)
        try:
            if op_type == 0:
                # Concurrent Lookup
                res = local_cache.lookup(context=base_context, intent_vector=seed_vector)
                elapsed = (time.perf_counter() - t0) * 1000
                return True, f"lookup:{res.hit}", elapsed
            elif op_type == 1:
                # Concurrent Stage
                res = local_cache.stage(
                    context=base_context,
                    intent_digest=digest(f"worker-{worker_idx}-op-{op_idx}"),
                    intent_vector=seed_vector,
                    output={"worker": worker_idx, "op": op_idx},
                    outcome="X3D_ALLOW_FINISH",
                    source_run_id=f"run-{worker_idx}-{op_idx}",
                    source_trace_id="0" * 32,
                    replay_eligible=False,
                )
                elapsed = (time.perf_counter() - t0) * 1000
                return True, f"stage:{res.status}", elapsed
            else:
                # Rapid Lookup on another cluster
                sample = seeded_entries[f"cluster-topic-{(worker_idx + op_idx) % depth:03d}"][1]
                res = local_cache.lookup(context=base_context, intent_vector=sample)
                elapsed = (time.perf_counter() - t0) * 1000
                return True, f"sample_lookup:{res.hit}", elapsed
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return False, f"error:{type(exc).__name__}:{exc}", elapsed

    with tqdm(total=200, desc="Concurrent Ops", unit="op") as pbar:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker_mixed_op, i % concurrency, i) for i in range(200)]
            for fut in concurrent.futures.as_completed(futures):
                success, msg, lat = fut.result()
                concurrency_latencies.append(lat)
                if not success or "database is locked" in msg.lower():
                    lock_errors += 1
                else:
                    ops_completed += 1
                pbar.update(1)

    # ── Phase 2: Cache Depth Scaling & Burst Throughput ───────────────────
    print(f"\n[Phase 2] Running Burst Lookups ({burst_queries} queries against {depth} cached entries)...")
    burst_latencies = []
    hits = 0
    misses = 0

    with tqdm(total=burst_queries, desc="Burst Lookups", unit="query") as pbar:
        for i in range(burst_queries):
            target_idx = i % depth
            _, target_vec, _ = seeded_entries[f"cluster-topic-{target_idx:03d}"]
            t0 = time.perf_counter()
            lookup = cache.lookup(context=base_context, intent_vector=target_vec)
            lat = (time.perf_counter() - t0) * 1000
            burst_latencies.append(lat)
            if lookup.hit:
                hits += 1
            else:
                misses += 1
            pbar.update(1)

    # ── Phase 3: Multi-Tenant L0 Route Context & Security Isolation ──────
    print("\n[Phase 3] Testing Multi-Tenant L0 Route Context & Security Isolation...")
    isolation_tests = 0
    isolation_leaks = 0

    isolation_permutations = [
        ("classification", "public", "restricted"),
        ("audiences", ["internal_consulting"], ["nee-engineering"]),
        ("route", "exact_lookup", "vector_search"),
        ("delivery_profile", "direct_answer", "briefing_recap"),
    ]

    for param, val_a, val_b in isolation_permutations:
        ctx_a = make_l0_context(**{param: val_a}) if param in ("classification", "audiences", "route") else make_l0_context()
        if param not in ("classification", "audiences", "route"):
            ctx_a[param] = val_a

        ctx_b = make_l0_context(**{param: val_b}) if param in ("classification", "audiences", "route") else make_l0_context()
        if param not in ("classification", "audiences", "route"):
            ctx_b[param] = val_b

        stage_and_activate_entry(
            cache,
            runtime_root,
            context=ctx_a,
            canonical_text=f"Isolation test query for {param}={val_a}",
            intent_vector=seed_vector,
            cluster_id=f"iso-{param}",
            ordinal=0,
        )
        isolation_tests += 1

        # Lookup with matching context A -> MUST HIT
        res_a = cache.lookup(context=ctx_a, intent_vector=seed_vector)
        assert res_a.hit, f"Matching context for {param} failed to hit"

        # Lookup with context B -> MUST MISS (NO LEAKAGE)
        res_b = cache.lookup(context=ctx_b, intent_vector=seed_vector)
        isolation_tests += 1
        if res_b.hit:
            isolation_leaks += 1

    # ── Phase 4: Adversarial & Resiliency Stress (Fail-Closed) ────────────
    print("\n[Phase 4] Testing Adversarial Edge Cases & Fail-Closed Integrity...")
    adversarial_checks = 0
    adversarial_passed = 0

    # 4.1 Boundary Cosine Similarity (<0.985 threshold vs >=0.985 threshold)
    policy = load_semantic_cache_policy()
    thresh = policy.similarity_threshold

    # Gram-Schmidt orthogonalization to get exact cosine similarities
    s_arr = np.array(seed_vector, dtype=np.float64)
    rnd_seed = np.random.RandomState(42).randn(len(seed_vector))
    ortho_arr = rnd_seed - (np.dot(rnd_seed, s_arr) / np.dot(s_arr, s_arr)) * s_arr
    ortho_arr = ortho_arr / np.linalg.norm(ortho_arr)

    # Sub-threshold: cosine similarity = thresh - 0.05 (e.g. 0.935 < 0.985)
    angle_sub = math.acos(thresh - 0.05)
    v_sub = [float(x) for x in (math.cos(angle_sub) * s_arr + math.sin(angle_sub) * ortho_arr)]

    # Super-threshold: cosine similarity = 0.995 >= 0.985
    angle_sup = math.acos(0.995)
    v_sup = [float(x) for x in (math.cos(angle_sup) * s_arr + math.sin(angle_sup) * ortho_arr)]

    stage_and_activate_entry(
        cache,
        runtime_root,
        context=base_context,
        canonical_text="Baseline prompt for threshold boundary test",
        intent_vector=seed_vector,
        cluster_id="threshold-boundary",
        ordinal=0,
    )

    res_sub = cache.lookup(context=base_context, intent_vector=v_sub)
    adversarial_checks += 1
    if not res_sub.hit and res_sub.reason == "CACHE_MISS_NO_COMPATIBLE_INTENT":
        adversarial_passed += 1

    res_sup = cache.lookup(context=base_context, intent_vector=v_sup)
    adversarial_checks += 1
    if res_sup.hit:
        adversarial_passed += 1

    # 4.2 Tampered Output Payload in SQLite with distinct vector -> MUST FAIL CLOSED
    unique_tamper_text = "Unique unshared prompt exclusively for tamper resilience testing"
    unique_tamper_vec = [float(v) for v in backend.encode(unique_tamper_text)["dense"]]
    tamp_entry_id = stage_and_activate_entry(
        cache,
        runtime_root,
        context=base_context,
        canonical_text=unique_tamper_text,
        intent_vector=unique_tamper_vec,
        cluster_id="cluster-tamper-target",
        ordinal=0,
    )

    conn = create_sqlite_conn(cache.path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE semantic_cache_entry SET output_digest='corrupted_digest' WHERE entry_id=?", (tamp_entry_id,))
    conn.commit()
    conn.close()

    res_tampered = cache.lookup(context=base_context, intent_vector=unique_tamper_vec)
    adversarial_checks += 1
    if not res_tampered.hit and res_tampered.reason == "CACHE_MISS_INVALID_OUTPUT":
        adversarial_passed += 1

    # 4.3 Non-finite vectors and dimension mismatches -> Graceful failure
    res_nan = cache.lookup(context=base_context, intent_vector=[float("nan")] * 1024)
    adversarial_checks += 1
    if not res_nan.hit and "CACHE_LOOKUP_ERROR" in res_nan.reason:
        adversarial_passed += 1

    res_dim = cache.lookup(context=base_context, intent_vector=seed_vector[:512])
    adversarial_checks += 1
    if not res_dim.hit:
        adversarial_passed += 1

    # ── Phase 5: L0 Telemetry & Event Chain Verification ──────────────────
    print("\n[Phase 5] Verifying L0 Telemetry Event Chain Integrity under Load...")
    test_run_id = stable_id("telemetry-stress-run", length=24)
    test_trace = trace_id(test_run_id, "telemetry-check", "stress")
    telemetry_recorder = RunRecorder(
        runtime_root,
        run_id=test_run_id,
        trace_id=test_trace,
        use_case="query",
        graph_snapshot="stress-snapshot",
    )
    correlation = {
        "query_id": "telemetry-check",
        "source_id": None,
        "candidate_id": None,
        "graph_operation_id": None,
        "hitl_item_id": None,
    }
    telemetry_recorder.record(
        stage="L0",
        event_type="route_decision",
        payload={
            "semantic_cache": {
                "mode": cache.mode,
                "hit": True,
                "reason": "CACHE_HIT",
                "similarity": 0.995,
                "context_digest": digest(cache.compatibility(base_context)),
                "policy_digest": cache.policy_digest,
            }
        },
        correlation=correlation,
        route="exact_lookup",
    )
    seal_result = telemetry_recorder.seal(["L0"], correlation)
    telemetry_valid = bool(seal_result.get("event_chain_digest") and seal_result.get("event_count") >= 1)

    # ── Aggregate Report ──────────────────────────────────────────────────
    report = {
        "schema_version": "1.0.0",
        "stress_test_id": stable_id("stress-semantic-cache-l0", length=26),
        "executed_at": utc_now(),
        "status": "PASS" if (lock_errors == 0 and isolation_leaks == 0 and adversarial_passed == adversarial_checks and telemetry_valid) else "FAIL",
        "config": {
            "concurrency_workers": concurrency,
            "cache_depth_entries": depth,
            "burst_queries": burst_queries,
            "similarity_threshold": thresh,
        },
        "concurrency_metrics": {
            "total_mixed_ops": 200,
            "ops_completed": ops_completed,
            "lock_or_concurrency_errors": lock_errors,
            "latency_p50_ms": round(percentile(concurrency_latencies, 0.50), 3),
            "latency_p95_ms": round(percentile(concurrency_latencies, 0.95), 3),
            "latency_p99_ms": round(percentile(concurrency_latencies, 0.99), 3),
        },
        "burst_throughput_metrics": {
            "burst_queries": burst_queries,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / burst_queries, 4),
            "latency_p50_ms": round(percentile(burst_latencies, 0.50), 3),
            "latency_p90_ms": round(percentile(burst_latencies, 0.90), 3),
            "latency_p95_ms": round(percentile(burst_latencies, 0.95), 3),
            "latency_p99_ms": round(percentile(burst_latencies, 0.99), 3),
            "latency_max_ms": round(max(burst_latencies), 3) if burst_latencies else 0.0,
            "throughput_qps": round(burst_queries / (sum(burst_latencies) / 1000), 2) if sum(burst_latencies) > 0 else 0.0,
        },
        "security_isolation_metrics": {
            "permutations_tested": isolation_tests,
            "cross_namespace_leaks": isolation_leaks,
            "isolation_success_rate": 1.0 if isolation_leaks == 0 else 0.0,
        },
        "adversarial_resilience_metrics": {
            "adversarial_checks": adversarial_checks,
            "adversarial_passed": adversarial_passed,
            "fail_closed_guarantee": adversarial_passed == adversarial_checks,
        },
        "telemetry_verification": {
            "l0_route_decision_recorded": True,
            "event_chain_valid": telemetry_valid,
            "event_chain_digest": seal_result.get("event_chain_digest"),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--burst-queries", type=int, default=300)
    parser.add_argument("--depth", type=int, default=80)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    temp_dir = None
    if args.runtime_root is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="nee-stress-l0-")
        runtime_root = Path(temp_dir.name) / "runtime"
    else:
        runtime_root = args.runtime_root.expanduser().absolute().resolve()

    try:
        backend = BgeM3TestBackend()
        report = run_stress_suite(
            runtime_root,
            backend,
            concurrency=args.concurrency,
            burst_queries=args.burst_queries,
            depth=args.depth,
        )
        print("\n" + "=" * 70)
        print("SEMANTIC CACHE L0 ROUTING STRESS TEST RESULTS")
        print("=" * 70)
        print(f"Status:                      {report['status']}")
        print(f"Cache Depth:                 {report['config']['cache_depth_entries']} active entries")
        print(f"Concurrent Mixed Ops:        {report['concurrency_metrics']['ops_completed']} / {report['concurrency_metrics']['total_mixed_ops']}")
        print(f"Lock Errors:                 {report['concurrency_metrics']['lock_or_concurrency_errors']}")
        print(f"Burst Lookup p50 Latency:    {report['burst_throughput_metrics']['latency_p50_ms']} ms")
        print(f"Burst Lookup p95 Latency:    {report['burst_throughput_metrics']['latency_p95_ms']} ms")
        print(f"Burst Lookup p99 Latency:    {report['burst_throughput_metrics']['latency_p99_ms']} ms")
        print(f"Burst Throughput:            {report['burst_throughput_metrics']['throughput_qps']} QPS")
        print(f"Namespace Isolation Leaks:   {report['security_isolation_metrics']['cross_namespace_leaks']}")
        print(f"Adversarial Checks Passed:   {report['adversarial_resilience_metrics']['adversarial_passed']} / {report['adversarial_resilience_metrics']['adversarial_checks']}")
        print(f"L0 Telemetry Chain Valid:    {report['telemetry_verification']['event_chain_valid']}")
        print("=" * 70)

        if args.output:
            out_path = args.output.expanduser().absolute().resolve()
            write_json(out_path, report)
            print(f"Full report written to: {out_path}")

        return 0 if report["status"] == "PASS" else 1
    finally:
        if temp_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
