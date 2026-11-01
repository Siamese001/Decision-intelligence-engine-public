#!/usr/bin/env python3
"""Benchmark real BGE-M3 semantic-cache hit quality and p50/p95 latency."""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    stable_id,
    trace_id,
    utc_now,
    write_json_exclusive,
)
from dual_pipeline.index import get_backend  # noqa: E402
from dual_pipeline.semantic_cache import (  # noqa: E402
    SemanticCache,
    configure_semantic_cache,
)
from dual_pipeline.telemetry import RunRecorder  # noqa: E402


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _validate_cases(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "1.0.0"
        or not isinstance(value.get("benchmark_id"), str)
        or not isinstance(value.get("clusters"), list)
        or not value["clusters"]
        or not isinstance(value.get("negative_queries"), list)
    ):
        raise ValueError("semantic-cache benchmark cases have an invalid shape")
    for cluster in value["clusters"]:
        if (
            not isinstance(cluster, dict)
            or not isinstance(cluster.get("cluster_id"), str)
            or not isinstance(cluster.get("canonical"), str)
            or not isinstance(cluster.get("paraphrases"), list)
            or not cluster["paraphrases"]
            or any(not isinstance(item, str) or not item for item in cluster["paraphrases"])
        ):
            raise ValueError("semantic-cache benchmark cluster is invalid")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "benchmarks" / "semantic_cache_bge_m3_cases.json",
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--warmup",
        type=int,
        default=2,
        help="BGE-M3 encodes to run before measuring.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cases = _validate_cases(read_json(args.cases.resolve()))
    runtime = args.runtime_root.expanduser().absolute().resolve()
    configure_semantic_cache(runtime, "read_write")
    cache = SemanticCache(runtime)
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path=str(args.model_path),
        model_revision=args.model_revision,
        model_manifest=str(args.model_manifest),
    )
    warmup_text = cases["clusters"][0]["canonical"]
    for _ in range(max(0, args.warmup)):
        backend.encode(warmup_text)

    context = {
        "use_case": "query",
        "cache_payload_kind": "benchmark_query_output_v1",
        "benchmark_id": cases["benchmark_id"],
        "active_pair": {
            "generation_id": "benchmark-generation",
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "production_qualified": backend.production_qualified,
        },
        "authorization_context_digest": "benchmark-isolated",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "route": "exact_lookup",
        "retrieval_mode": "approved_only",
    }
    expected_entries: dict[str, str] = {}
    write_latencies: list[float] = []
    benchmark_nonce = utc_now()
    for ordinal, cluster in enumerate(cases["clusters"]):
        started = time.perf_counter()
        encoded = backend.encode(cluster["canonical"])
        run_id = stable_id(
            "semantic-cache-benchmark-source",
            cases["benchmark_id"],
            benchmark_nonce,
            ordinal,
            length=26,
        )
        current_trace = trace_id(run_id, cluster["cluster_id"], "benchmark")
        recorder = RunRecorder(
            runtime,
            run_id=run_id,
            trace_id=current_trace,
            use_case="query",
            graph_snapshot="benchmark",
            model_or_index_generation="benchmark-generation",
        )
        correlation = {
            "query_id": cluster["cluster_id"],
            "source_id": None,
            "candidate_id": None,
            "graph_operation_id": None,
            "hitl_item_id": None,
        }
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload={"benchmark_cluster_id": cluster["cluster_id"]},
            correlation=correlation,
            route="exact_lookup",
        )
        result = cache.stage(
            context=context,
            intent_digest=digest(cluster["canonical"]),
            intent_vector=[float(value) for value in encoded["dense"]],
            output={
                "cluster_id": cluster["cluster_id"],
                "query_output": {"status": "COMPLETED"},
            },
            outcome="X3D_ALLOW_FINISH",
            source_run_id=run_id,
            source_trace_id=current_trace,
            replay_eligible=True,
        )
        if result.entry is None:
            raise RuntimeError(f"benchmark cache stage failed: {result.reason}")
        recorder.record(
            stage="L4",
            event_type="pipeline_event",
            payload={"entry_id": result.entry.entry_id, "status": result.status},
            correlation=correlation,
            route="exact_lookup",
        )
        seal = recorder.seal(
            ["L0", "L4"],
            correlation,
        )
        activated = cache.activate(
            result.entry,
            source_event_count=seal["event_count"],
            source_event_chain_digest=seal["event_chain_digest"],
        )
        if activated.status != "ACTIVE":
            raise RuntimeError(f"benchmark cache activation failed: {activated.reason}")
        expected_entries[cluster["cluster_id"]] = result.entry.entry_id
        write_latencies.append((time.perf_counter() - started) * 1000)

    observations: list[dict[str, Any]] = []
    lookup_latencies: list[float] = []
    end_to_end_latencies: list[float] = []
    correct_hits = 0
    expected_hit_count = 0
    for cluster in cases["clusters"]:
        for text in cluster["paraphrases"]:
            expected_hit_count += 1
            overall_started = time.perf_counter()
            encoded = backend.encode(text)
            lookup_started = time.perf_counter()
            lookup = cache.lookup(
                context=context,
                intent_vector=[float(value) for value in encoded["dense"]],
            )
            lookup_ms = (time.perf_counter() - lookup_started) * 1000
            total_ms = (time.perf_counter() - overall_started) * 1000
            correct = lookup.entry_id == expected_entries[cluster["cluster_id"]]
            correct_hits += int(correct)
            lookup_latencies.append(lookup_ms)
            end_to_end_latencies.append(total_ms)
            observations.append(
                {
                    "expected_cluster_id": cluster["cluster_id"],
                    "query_digest": digest(text),
                    "hit": lookup.hit,
                    "correct_hit": correct,
                    "matched_entry_id": lookup.entry_id,
                    "similarity": lookup.similarity,
                    "reason": lookup.reason,
                    "lookup_latency_ms": round(lookup_ms, 3),
                    "encode_and_lookup_latency_ms": round(total_ms, 3),
                }
            )

    false_hits = 0
    negative_latencies: list[float] = []
    for text in cases["negative_queries"]:
        encoded = backend.encode(text)
        started = time.perf_counter()
        lookup = cache.lookup(
            context=context,
            intent_vector=[float(value) for value in encoded["dense"]],
        )
        latency = (time.perf_counter() - started) * 1000
        negative_latencies.append(latency)
        false_hits += int(lookup.hit)
        observations.append(
            {
                "expected_cluster_id": None,
                "query_digest": digest(text),
                "hit": lookup.hit,
                "correct_hit": not lookup.hit,
                "matched_entry_id": lookup.entry_id,
                "similarity": lookup.similarity,
                "reason": lookup.reason,
                "lookup_latency_ms": round(latency, 3),
            }
        )

    report = {
        "schema_version": "1.0.0",
        "benchmark_id": cases["benchmark_id"],
        "generated_at": utc_now(),
        "status": "PASS",
        "backend": {
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "production_qualified": backend.production_qualified,
        },
        "cache": cache.status(),
        "quality": {
            "expected_hit_count": expected_hit_count,
            "correct_hit_count": correct_hits,
            "correct_hit_rate": round(correct_hits / expected_hit_count, 6),
            "negative_query_count": len(cases["negative_queries"]),
            "false_hit_count": false_hits,
            "false_hit_rate": round(
                false_hits / max(1, len(cases["negative_queries"])), 6
            ),
        },
        "latency_ms": {
            "write_mean": round(statistics.fmean(write_latencies), 3),
            "lookup_p50": round(percentile(lookup_latencies, 0.50), 3),
            "lookup_p95": round(percentile(lookup_latencies, 0.95), 3),
            "encode_and_lookup_p50": round(
                percentile(end_to_end_latencies, 0.50), 3
            ),
            "encode_and_lookup_p95": round(
                percentile(end_to_end_latencies, 0.95), 3
            ),
            "negative_lookup_p50": round(percentile(negative_latencies, 0.50), 3),
            "negative_lookup_p95": round(percentile(negative_latencies, 0.95), 3),
        },
        "observations": observations,
    }
    write_json_exclusive(args.output.expanduser().absolute().resolve(), report)
    print(args.output.expanduser().absolute().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
