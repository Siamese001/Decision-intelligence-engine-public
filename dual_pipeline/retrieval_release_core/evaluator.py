"""Evaluator and activation validation for BGE-M3 retrieval release."""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..authorization import AuthorizationContext
from ..contracts import create_artifact, validate_artifact
from ..core import (
    assert_safe_mutable_path, digest, read_json, sha256_file, stable_id, utc_now, write_json_exclusive,
)
from ..graph_retrieval import governed_bm25_assertion_ranks
from ..index import CLASSIFICATION_RANK, EmbeddingBackend, HybridRetriever
from ..release_core.helpers import _verify_receipt
from ..release_core.manager import ReleaseManager
from .baseline_cache import BaselineQueryCache
from .constants import _POSITIVE_QUERY_KINDS, _QUERY_KINDS
from .metrics import (
    _critical_case_failed, _first_rank, _max_rank_regression, _metrics, _ndcg_at_k,
    _reciprocal_rank, _records_by_id, _repeat_rank_drift, _scoped_release_delta,
)
from .policies import (
    _assert_bge_m3_model_binding, _assert_real_bge_backend, _assert_suite_authorized,
    _load_policy, _pair_binding, _policy_binding, _same_pair,
)
from .preflight import preflight_retrieval_release_schema
from .query_encoding_cache import QueryEncodingCache


def _evaluate_case(
    retriever: HybridRetriever,
    case: dict[str, Any],
    graph_path: Path | None = None,
    encoded_query: dict[str, Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]], dict[str, bool], float]:
    started = time.monotonic()
    bm25_assertion_ranks: dict[str, int] | None = None
    if graph_path is not None:
        ranks_by_lane, bm25_executed = governed_bm25_assertion_ranks(
            question=case["query"],
            graph_path=graph_path,
            sidecar_path=retriever.sidecar_path,
            allowed_lanes=[case["lane"]],
            retrieval_mode=case["retrieval_mode"],
            classification=case["classification"],
            audiences=case["audiences"],
            allowed_assertion_ids=case.get("allowed_assertion_ids"),
        )
        if bm25_executed:
            bm25_assertion_ranks = ranks_by_lane.get(
                case["lane"],
                {},
            )
    result = retriever.retrieve_lane(
        case["query"],
        case["lane"],
        classification=case["classification"],
        audiences=case["audiences"],
        top_k=case["top_k"],
        retrieval_mode=case["retrieval_mode"],
        encoded_query=encoded_query,
        facet_filters=case.get("facet_filters"),
        allowed_assertion_ids=case.get("allowed_assertion_ids"),
        bm25_assertion_ranks=bm25_assertion_ranks,
    )
    latency_ms = (time.monotonic() - started) * 1000
    record_ids = [item["record_id"] for item in result["results"]]
    rank_diagnostics = [
        {
            "record_id": item["record_id"],
            "fused_rank": item["fused_rank"],
            "dense_rank": item["dense_rank"],
            "sparse_rank": item["sparse_rank"],
            "bm25_rank": item.get("bm25_rank"),
            "colbert_rank": item["colbert_rank"],
        }
        for item in result["results"]
    ]
    # The three counters are present only after each BGE-M3 retrieval component
    # has executed.  A sparse result may be empty for a paraphrase, but it was
    # still evaluated and must be represented in the result payload.
    components = {
        "dense": "dense_candidate_count" in result,
        "sparse": "sparse_candidate_count" in result,
        "bm25": bool(result.get("bm25_executed")),
        "colbert": "colbert_candidate_count" in result,
    }
    return record_ids, rank_diagnostics, components, latency_ms


class RetrievalReleaseEvaluator:
    """Evaluate a prepared BGE-M3 release without changing the active pair."""

    def __init__(self, runtime_root: Path, seed_graph: Path | None = None):
        self.manager = ReleaseManager(runtime_root, seed_graph)
        self.runtime_root = self.manager.runtime_root
        self.evaluations_dir = self.runtime_root / "retrieval_evaluations"

    def _evaluation_path(self, receipt_id: str) -> Path:
        path = self.evaluations_dir / f"retrieval-release-evaluation-{receipt_id}.json"
        return assert_safe_mutable_path(
            self.runtime_root, path, label="retrieval release evaluation path"
        )

    def _failed_evaluation_path(self, receipt_id: str, evaluation_id: str) -> Path:
        attempt = digest([evaluation_id])[:16]
        path = (
            self.evaluations_dir
            / "failed"
            / (f"retrieval-release-evaluation-{receipt_id}-attempt-{attempt}.json")
        )
        return assert_safe_mutable_path(
            self.runtime_root,
            path,
            label="failed retrieval release evaluation path",
        )

    @staticmethod
    def _load_suite(path: Path, policy: dict[str, Any]) -> dict[str, Any]:
        suite_path = Path(path).expanduser().resolve()
        suite = read_json(suite_path)
        validate_artifact(suite)
        if suite["contract_id"] != "urn:nee:dual-pipeline:retrieval-release-suite:1.0.0":
            raise ValueError("retrieval release suite has the wrong contract")
        if suite["acceptance_policy"] != _policy_binding(policy):
            raise PermissionError("retrieval release suite is not bound to the current policy")
        case_ids = [case["case_id"] for case in suite["cases"]]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("retrieval release suite has duplicate case IDs")
        if not any(case["scope"] == "core" for case in suite["cases"]):
            raise ValueError("retrieval release suite requires at least one core case")
        if any(
            not case["expected_record_ids"] and not case["forbidden_record_ids"]
            for case in suite["cases"]
        ):
            raise ValueError("retrieval release case must assert presence or absence")
        if any(case["query_kind"] not in _QUERY_KINDS for case in suite["cases"]):
            raise ValueError("retrieval release suite has an unsupported query kind")
        return suite

    @staticmethod
    def _coverage(
        suite: dict[str, Any],
        baseline_records: dict[str, str],
        candidate_records: dict[str, str],
        policy: dict[str, Any],
        receipt: dict[str, Any] | None = None,
        runtime_root: Path | None = None,
    ) -> dict[str, Any]:
        added_or_changed, removed = _scoped_release_delta(
            runtime_root,
            receipt,
            baseline_records,
            candidate_records,
        )
        case_by_id = {case["case_id"]: case for case in suite["cases"]}
        declared = suite["coverage"]
        declared_ids = [item["record_id"] for item in declared]
        if len(declared_ids) != len(set(declared_ids)):
            raise ValueError("retrieval release coverage has duplicate record IDs")
        actual = {
            **{record_id: "added_or_changed" for record_id in added_or_changed},
            **{record_id: "removed" for record_id in removed},
        }
        declared_map = {item["record_id"]: item for item in declared}
        complete = set(actual) == set(declared_map)
        if complete:
            for record_id, change_kind in actual.items():
                declaration = declared_map[record_id]
                if declaration["change_kind"] != change_kind:
                    complete = False
                    break
                query_kind_counts: Counter[str] = Counter()
                query_kind_queries: dict[str, set[str]] = {}
                record_projection = json.loads(
                    (candidate_records if change_kind == "added_or_changed" else baseline_records)[
                        record_id
                    ]
                )
                retrieval_record = record_projection["retrieval_record"]
                retrieval_expected = declaration.get("retrieval_expected", True)
                record_is_relationship = retrieval_record.get("record_type") in {
                    "edge", "architecture_relationship", "relationship_assertion", "relationship"
                }
                if record_is_relationship and not declaration["relationship_applicable"]:
                    complete = False
                    break
                for case_id in declaration["case_ids"]:
                    case = case_by_id.get(case_id)
                    if case is None or case["scope"] != "delta":
                        complete = False
                        break
                    query_kind = case["query_kind"]
                    query_kind_counts[query_kind] += 1
                    query_kind_queries.setdefault(query_kind, set()).add(
                        " ".join(case["query"].casefold().split())
                    )
                    target = (
                        case["expected_record_ids"]
                        if (
                            change_kind == "added_or_changed"
                            and retrieval_expected
                            and query_kind in _POSITIVE_QUERY_KINDS
                        )
                        else case["forbidden_record_ids"]
                    )
                    if record_id not in target:
                        complete = False
                        break
                    classification_admitted = (
                        CLASSIFICATION_RANK[retrieval_record["classification"]]
                        <= CLASSIFICATION_RANK[case["classification"]]
                    )
                    audience_admitted = bool(
                        set(retrieval_record["audiences"]) & set(case["audiences"])
                    )
                    same_lane = case["lane"] == retrieval_record["lane"]
                    if query_kind in (
                        _POSITIVE_QUERY_KINDS | {"hard_negative", "stale_or_superseded"}
                    ) and not (same_lane and classification_admitted and audience_admitted):
                        complete = False
                        break
                    if query_kind == "lane_isolation" and (
                        same_lane or not classification_admitted or not audience_admitted
                    ):
                        complete = False
                        break
                    if query_kind == "acl_boundary" and (
                        not same_lane or (classification_admitted and audience_admitted)
                    ):
                        complete = False
                        break
                if not complete:
                    break
                required_counts = dict(policy["delta_case_requirements"][change_kind])
                if declaration["relationship_applicable"]:
                    required_counts["relationship"] = 1
                if declaration["stale_or_superseded_applicable"]:
                    required_counts["stale_or_superseded"] = 1
                if change_kind == "removed" and not declaration["stale_or_superseded_applicable"]:
                    complete = False
                    break
                if any(
                    query_kind_counts[query_kind] < required_count
                    or len(query_kind_queries.get(query_kind, set())) < required_count
                    for query_kind, required_count in required_counts.items()
                ):
                    complete = False
                    break
        return {
            "added_or_changed_record_ids": added_or_changed,
            "removed_record_ids": removed,
            "declared_record_ids": sorted(declared_map),
            "complete": complete,
        }

    def evaluate(
        self,
        receipt_path: Path,
        suite_path: Path,
        *,
        backend: EmbeddingBackend,
        evaluation_id: str | None = None,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        receipt_file = Path(receipt_path).expanduser().resolve()
        try:
            receipt_file.relative_to(self.manager.releases_dir.resolve())
        except ValueError as exc:
            raise ValueError(
                "retrieval release receipt must be under the runtime releases directory"
            ) from exc
        receipt = read_json(receipt_file)
        _verify_receipt(receipt)
        if receipt.get("status") != "PREPARED":
            raise ValueError("only a prepared release can be evaluated")
        preflight_retrieval_release_schema()
        baseline_pair = self.manager.active_pair()

        if not _same_pair(baseline_pair, receipt["previous_pair"]):
            raise RuntimeError("prepared release no longer starts from the active graph/index pair")
        candidate_pair = dict(receipt["next_pair"])
        self.manager.validate_pair(baseline_pair)
        self.manager.validate_pair(candidate_pair)
        _assert_real_bge_backend(baseline_pair, backend)
        _assert_real_bge_backend(candidate_pair, backend)
        if any(
            baseline_pair[field] != candidate_pair[field]
            for field in (
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        ):
            raise RuntimeError(
                "retrieval release comparison requires like-for-like BGE-M3 bindings"
            )

        policy = _load_policy()
        suite = self._load_suite(suite_path, policy)
        suite_file = Path(suite_path).expanduser().resolve()
        if candidate_pair["production_qualified"]:
            if authorization_context is None:
                raise PermissionError(
                    "production retrieval evaluation requires signed authorization"
                )
            authorization_context.require("knowledge:query")
            _assert_suite_authorized(suite, authorization_context)
            if (
                authorization_context.approved_model_manifest_digest
                != candidate_pair["model_manifest_digest"]
            ):
                raise PermissionError(
                    "retrieval evaluation authorization is not model-manifest bound"
                )
            authorization_context.require_resources(
                {
                    "retrieval_release_receipt": receipt["receipt_digest"],
                    "retrieval_release_suite": sha256_file(suite_file),
                    "retrieval_release_policy": _policy_binding(policy)["policy_digest"],
                }
            )
        canonical_output = self._evaluation_path(receipt["receipt_id"])
        if canonical_output.is_file():
            existing = read_json(canonical_output)
            validate_artifact(existing)
            if (
                existing.get("contract_id")
                == "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
                and existing.get("status") == "PASS"
                and existing.get("prepared_receipt_id") == receipt["receipt_id"]
                and existing.get("prepared_receipt_digest") == receipt["receipt_digest"]
                and existing.get("suite_id") == suite["suite_id"]
                and existing.get("suite_digest") == suite["canonical_digest"]["value"]
                and existing.get("suite_file_sha256") == sha256_file(suite_file)
                and existing.get("acceptance_policy") == _policy_binding(policy)
                and _same_pair(existing["baseline_pair"], baseline_pair)
                and _same_pair(existing["candidate_pair"], candidate_pair)
                and all(existing["gates"].values())
            ):
                return {
                    "evaluation": existing,
                    "evaluation_path": str(canonical_output),
                    "idempotent_replay": True,
                }
            raise FileExistsError(
                "canonical retrieval evaluation exists but is not the exact "
                "passing receipt/suite binding"
            )
        baseline_sidecar = self.manager._resolve(baseline_pair["sidecar_path"])
        candidate_sidecar = self.manager._resolve(candidate_pair["sidecar_path"])
        baseline_graph = self.manager._resolve(baseline_pair["graph_path"])
        candidate_graph = self.manager._resolve(candidate_pair["graph_path"])
        coverage = self._coverage(
            suite,
            _records_by_id(baseline_sidecar),
            _records_by_id(candidate_sidecar),
            policy,
            receipt=receipt,
            runtime_root=self.manager.runtime_root,
        )
        baseline_retriever = HybridRetriever(baseline_sidecar, backend)
        candidate_retriever = HybridRetriever(candidate_sidecar, backend)

        from dual_pipeline.l0.ingestion_timing import timed_child_span
        query_encoding_cache = QueryEncodingCache(self.evaluations_dir / ".query_encoding_cache.json")
        query_cache: dict[str, dict[str, Any]] = {}

        # Pre-batch query encodings to avoid slow serial encoding
        missing_queries: list[str] = []
        for case in suite["cases"]:
            q = case["query"]
            cached = query_encoding_cache.get(backend, q) if q not in query_cache else query_cache[q]
            if cached is not None:
                query_cache[q] = cached
            elif q not in missing_queries:
                missing_queries.append(q)

        if missing_queries:
            with timed_child_span("query_encoding_batch", {"batch_size": len(missing_queries)}):
                encs = backend.encode_many(missing_queries) if hasattr(backend, "encode_many") else [backend.encode(q) for q in missing_queries]
                query_encoding_cache.set_many(backend, list(zip(missing_queries, encs)))
                query_cache.update(zip(missing_queries, encs))

        def _get_encoded(query: str) -> dict[str, Any]:
            if query in query_cache:
                return query_cache[query]
            cached = query_encoding_cache.get(backend, query)
            if cached is not None:
                query_cache[query] = cached
                return cached
            with timed_child_span("query_encoding"):
                encoded = backend.encode(query)
            query_encoding_cache.set(backend, query, encoded)
            query_cache[query] = encoded
            return encoded

        thresholds = policy["thresholds"]
        if suite["cases"]:
            from concurrent.futures import ThreadPoolExecutor
            from config.request_deadline import remaining_timeout
            _init_case = suite["cases"][0]
            _init_encoded = _get_encoded(_init_case["query"])
            with ThreadPoolExecutor(max_workers=2) as _warm_pool:
                _t = remaining_timeout(60.0)
                _fb = _warm_pool.submit(_evaluate_case, baseline_retriever, _init_case, baseline_graph, encoded_query=_init_encoded)
                _fc = _warm_pool.submit(_evaluate_case, candidate_retriever, _init_case, candidate_graph, encoded_query=_init_encoded)
                _fb.result(timeout=_t), _fc.result(timeout=_t)
        baseline_cache = BaselineQueryCache(self.evaluations_dir / ".baseline_query_cache.json")
        baseline_graph_sha = baseline_pair["graph_sha256"]
        baseline_sidecar_sha = baseline_pair["sidecar_sha256"]
        case_results: list[dict[str, Any]] = []
        total_cases = len(suite["cases"])
        eval_started = last_progress_ts = time.monotonic()
        with timed_child_span("retrieval_repeats", {"case_count": total_cases}):
            for case_idx, case in enumerate(suite["cases"], 1):
                encoded_query = _get_encoded(case["query"])
                cached_baseline = baseline_cache.get(
                    baseline_graph_sha,
                    baseline_sidecar_sha,
                    case,
                )
                if cached_baseline is not None:
                    (
                        baseline_ids,
                        baseline_rank_diagnostics,
                        baseline_components,
                    ) = cached_baseline
                else:
                    (
                        baseline_ids,
                        baseline_rank_diagnostics,
                        baseline_components,
                        _,
                    ) = _evaluate_case(
                        baseline_retriever,
                        case,
                        baseline_graph,
                        encoded_query=encoded_query,
                    )
                    baseline_cache.set(
                        baseline_graph_sha,
                        baseline_sidecar_sha,
                        case,
                        (baseline_ids, baseline_rank_diagnostics, baseline_components),
                    )
                candidate_runs: list[list[str]] = []
                candidate_component_runs: list[dict[str, bool]] = []
                candidate_latencies: list[float] = []
                candidate_rank_diagnostics: list[dict[str, Any]] = []
                for repeat_index in range(thresholds["repeat_run_count"]):
                    (
                        run_ids,
                        run_rank_diagnostics,
                        run_components,
                        run_latency,
                    ) = _evaluate_case(
                        candidate_retriever,
                        case,
                        candidate_graph,
                        encoded_query=encoded_query,
                    )
                    candidate_runs.append(run_ids)
                    candidate_component_runs.append(run_components)
                    candidate_latencies.append(run_latency)
                    if repeat_index == 0:
                        candidate_rank_diagnostics = run_rank_diagnostics
                warmup_components = candidate_component_runs[0]
                warmup_latency = candidate_latencies[0]
                candidate_ids = candidate_runs[0]
                candidate_components = {
                    name: all(components[name] for components in candidate_component_runs)
                    for name in ("dense", "sparse", "bm25", "colbert")
                }
                latency_ms = max(candidate_latencies)
                now_ts = time.monotonic()
                if case_idx == total_cases or case_idx % 50 == 0 or (now_ts - last_progress_ts) >= 10.0:
                    last_progress_ts = now_ts
                    pct = (case_idx / total_cases * 100.0) if total_cases else 100.0
                    print(
                        f"  [BGE-M3 Eval] {case_idx}/{total_cases} ({pct:.1f}%) "
                        f"elapsed={now_ts - eval_started:.1f}s",
                        flush=True,
                    )
                expected = set(case["expected_record_ids"])
                forbidden = set(case["forbidden_record_ids"])
                baseline_rank = _first_rank(baseline_ids, expected)
                candidate_rank = _first_rank(candidate_ids, expected)
                rank_regression = 0
                if case["scope"] == "core":
                    rank_regression = max(
                        0,
                        _max_rank_regression(
                            baseline_ids,
                            candidate_ids,
                            expected,
                            case["top_k"],
                        ),
                    )
                relevant = expected & set(candidate_ids)
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "scope": case["scope"],
                        "query_kind": case["query_kind"],
                        "critical": case["critical"],
                        "expected_record_ids": sorted(expected),
                        "forbidden_record_ids": sorted(forbidden),
                        "baseline_retrieved_record_ids": baseline_ids,
                        "candidate_retrieved_record_ids": candidate_ids,
                        "baseline_rank_diagnostics": baseline_rank_diagnostics,
                        "candidate_rank_diagnostics": candidate_rank_diagnostics,
                        "candidate_repeat_retrieved_record_ids": candidate_runs,
                        "baseline_first_relevant_rank": baseline_rank,
                        "candidate_first_relevant_rank": candidate_rank,
                        "candidate_recall_at_k": (
                            None if not expected else len(relevant) / len(expected)
                        ),
                        "candidate_reciprocal_rank": _reciprocal_rank(candidate_rank, expected),
                        "candidate_ndcg_at_k": _ndcg_at_k(candidate_ids, expected, case["top_k"], case.get("qrel_grades")),
                        "rank_regression": rank_regression,
                        "repeat_rank_drift": _repeat_rank_drift(candidate_runs, case["top_k"]),
                        "forbidden_leakage": sorted(forbidden & set(candidate_ids)),
                        "component_execution": {
                            name: (
                                baseline_components[name]
                                and warmup_components[name]
                                and candidate_components[name]
                            )
                            for name in ("dense", "sparse", "bm25", "colbert")
                        },
                        "candidate_warmup_latency_ms": warmup_latency,
                        "latency_ms": latency_ms,
                    }
                )
        baseline_cache.flush()
        query_encoding_cache.flush()

        core = [item for item in case_results if item["scope"] == "core"]
        delta = [item for item in case_results if item["scope"] == "delta"]
        latencies = sorted(item["latency_ms"] for item in case_results)
        p95_idx = int(math.ceil(0.95 * len(latencies))) - 1 if latencies else 0
        p95_latency = latencies[max(0, p95_idx)] if latencies else 0.0
        core_metrics = _metrics(core)
        delta_metrics = _metrics(delta)
        aggregate = {
            "core": {
                "case_count": core_metrics["case_count"],
                "recall_at_k": core_metrics["recall_at_k"],
                "mrr": core_metrics["mrr"],
                "ndcg_at_k": core_metrics["ndcg_at_k"],
            },
            "delta": {
                "case_count": delta_metrics["case_count"],
                "recall_at_k": delta_metrics["recall_at_k"],
                "mrr": delta_metrics["mrr"],
                "ndcg_at_k": delta_metrics["ndcg_at_k"],
            },
            "max_latency_ms": max(item["latency_ms"] for item in case_results)
            if case_results
            else 0.0,
            "forbidden_leakage_count": sum(len(item["forbidden_leakage"]) for item in case_results),
            "critical_failure_count": 0,
            "rank_regression_count": 0,
            "repeat_stability_failure_count": 0,
        }
        critical_failures = [
            item
            for item in case_results
            if _critical_case_failed(item, thresholds["critical_top_k"])
        ]
        rank_regressions = [
            item for item in core if item["rank_regression"] > thresholds["max_rank_regression"]
        ]
        aggregate["critical_failure_count"] = len(critical_failures)
        aggregate["rank_regression_count"] = len(rank_regressions)
        repeat_failures = [
            item
            for item in case_results
            if item["repeat_rank_drift"] > thresholds["max_repeat_rank_drift"]
        ]
        aggregate["repeat_stability_failure_count"] = len(repeat_failures)
        gates = {
            "real_bge_m3": True,
            "pair_binding": True,
            "coverage_complete": coverage["complete"],
            "core_thresholds": (
                aggregate["core"]["recall_at_k"] >= thresholds["min_core_recall_at_k"]
                and aggregate["core"]["mrr"] >= thresholds["min_core_mrr"]
                and aggregate["core"]["ndcg_at_k"] >= thresholds["min_core_ndcg_at_k"]
            ),
            "delta_thresholds": (
                (
                    not coverage["added_or_changed_record_ids"]
                    and not coverage["removed_record_ids"]
                    and not delta
                )
                or (
                    bool(delta)
                    and aggregate["delta"]["recall_at_k"] >= thresholds["min_delta_recall_at_k"]
                    and aggregate["delta"]["mrr"] >= thresholds["min_delta_mrr"]
                    and aggregate["delta"]["ndcg_at_k"] >= thresholds["min_delta_ndcg_at_k"]
                )
            ),
            "critical_cases": not critical_failures,
            "rank_stability": not rank_regressions,
            "repeat_stability": not repeat_failures,
            "forbidden_leakage": (
                aggregate["forbidden_leakage_count"] <= thresholds["max_forbidden_leakage_count"]
            ),
            "latency": aggregate["max_latency_ms"] <= thresholds["max_latency_ms"],
            "bm25_execution": all(item["component_execution"]["bm25"] for item in case_results),
        }
        if any(not all(item["component_execution"].values()) for item in case_results):
            gates["real_bge_m3"] = False
        status = "PASS" if all(gates.values()) else "FAIL"
        evaluated_at = utc_now()
        evaluation_id = evaluation_id or stable_id(
            "retrieval-release-evaluation",
            receipt["receipt_id"],
            receipt["receipt_digest"],
            suite["canonical_digest"]["value"],
            evaluated_at,
            time.time_ns(),
            length=28,
        )
        evaluation = create_artifact(
            "retrieval-release-evaluation",
            evaluation_id,
            evaluation_id=evaluation_id,
            execution_tier=suite["execution_tier"],
            prepared_receipt_id=receipt["receipt_id"],
            prepared_receipt_digest=receipt["receipt_digest"],
            suite_id=suite["suite_id"],
            suite_digest=suite["canonical_digest"]["value"],
            suite_file_sha256=sha256_file(suite_file),
            acceptance_policy=_policy_binding(policy),
            baseline_pair=_pair_binding(baseline_pair),
            candidate_pair=_pair_binding(candidate_pair),
            coverage=coverage,
            case_results=case_results,
            aggregate=aggregate,
            gates=gates,
            status=status,
            evaluated_at=evaluated_at,
            extensions={
                "com.partner_advisory.nee.retrieval-release": {
                    "external_l2_calls": 0,
                    "backend": backend.backend_id,
                    "model_manifest_digest": backend.model_manifest_digest,
                    "authorization": (
                        authorization_context.audit_record() if authorization_context else None
                    ),
                    "p95_latency_ms": p95_latency,
                    "repeat_run_count": thresholds["repeat_run_count"],
                    "evaluation_tier": "routine_single_pass"
                    if thresholds["repeat_run_count"] == 1
                    else "multi_pass_qualification",
                }
            },
        )
        output = (
            canonical_output
            if status == "PASS"
            else self._failed_evaluation_path(receipt["receipt_id"], evaluation_id)
        )
        write_json_exclusive(output, evaluation)
        return {
            "evaluation": evaluation,
            "evaluation_path": str(output),
            "idempotent_replay": False,
        }


def validate_activation_evaluation(runtime_root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a prepared BGE-M3 receipt has an exact passing gate."""

    _verify_receipt(receipt)
    for label, pair in (
        ("prepared release baseline pair", receipt["previous_pair"]),
        ("prepared release candidate pair", receipt["next_pair"]),
    ):
        _pair_binding(pair)
        _assert_bge_m3_model_binding(pair, label=label)
    root = Path(runtime_root).expanduser().resolve()
    path = (
        root
        / "retrieval_evaluations"
        / (f"retrieval-release-evaluation-{receipt['receipt_id']}.json")
    )
    assert_safe_mutable_path(root, path, label="retrieval release activation evidence")
    if not path.is_file():
        raise RuntimeError("BGE-M3 activation requires a sealed retrieval release evaluation")
    evaluation = read_json(path)
    validate_artifact(evaluation)
    if evaluation["contract_id"] != "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0":
        raise ValueError("activation evaluation has the wrong contract")
    policy = _load_policy()
    if (
        evaluation["status"] != "PASS"
        or evaluation["prepared_receipt_id"] != receipt["receipt_id"]
        or evaluation["prepared_receipt_digest"] != receipt["receipt_digest"]
        or not _same_pair(evaluation["baseline_pair"], receipt["previous_pair"])
        or not _same_pair(evaluation["candidate_pair"], receipt["next_pair"])
        or evaluation["acceptance_policy"] != _policy_binding(policy)
        or not all(evaluation["gates"].values())
    ):
        raise RuntimeError("BGE-M3 retrieval release evaluation is stale, incomplete, or failed")
    return {
        "required": True,
        "evaluation_id": evaluation["evaluation_id"],
        "evaluation_digest": evaluation["canonical_digest"]["value"],
        "evaluation_path": str(path),
    }
