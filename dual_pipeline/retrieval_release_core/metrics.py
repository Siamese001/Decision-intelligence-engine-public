"""Metrics and delta calculations for retrieval release evaluation."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Container, Mapping, Sequence, Set

from ..core import canonical_json, connect_sqlite, read_json, stable_id


def _records_by_id(sidecar_path: Path) -> dict[str, str]:
    connection = connect_sqlite(sidecar_path, readonly=True)
    try:
        object_facets: dict[str, list[dict[str, str]]] = {}
        facet_table_present = bool(
            connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table'
                     AND name='retrieval_record_object_facet'"""
            ).fetchone()
        )
        if facet_table_present:
            for row in connection.execute(
                """SELECT record_id,assignment_id,object_type,object_id,
                          vocabulary,value,assignment_method,review_state
                   FROM retrieval_record_object_facet
                   ORDER BY record_id,assignment_id"""
            ):
                object_facets.setdefault(row["record_id"], []).append(dict(row))
        records: dict[str, str] = {}
        for row in connection.execute(
            "SELECT record_id,artifact_json FROM retrieval_record ORDER BY record_id"
        ):
            artifact = json.loads(row["artifact_json"])
            # A pair rebuild stamps every otherwise unchanged record with the
            # new graph snapshot and reseals its artifact digest.  Those are
            # generation bindings, not retrieval changes; treating them as a
            # content delta would require a meaningless test for the entire
            # corpus on every legitimate graph update.
            artifact.pop("graph_snapshot", None)
            artifact.pop("canonical_digest", None)
            records[row["record_id"]] = canonical_json(
                {
                    "retrieval_record": artifact,
                    "object_facets": object_facets.get(row["record_id"], []),
                }
            )
        return records
    finally:
        connection.close()


_MUTATION_TARGET_TYPES = frozenset({
    "assertion", "relationship", "action_item",
    "architecture_relationship", "edge", "decision",
    "proposal", "risk", "relationship_signal",
})


def _is_mutation_added(op_type: Any, target_type: Any, target_ops: set[str]) -> bool:
    if op_type in target_ops or target_type in _MUTATION_TARGET_TYPES:
        return True
    if not isinstance(op_type, str) or op_type == "add_evidence":
        return False
    return op_type.startswith(("add_", "create_", "upsert_"))


def _parse_change_set_ops(
    cs_file: Path,
    target_ops: set[str],
    superseded: set[str],
    added: set[str],
) -> None:
    if not cs_file.is_file():
        return
    try:
        cs = read_json(cs_file)
        ops = cs.get("operations", []) if isinstance(cs, dict) else (cs if isinstance(cs, list) else [])
        for op in ops:
            if not isinstance(op, dict):
                continue
            op_type = op.get("operation") or op.get("op")
            target_id = op.get("target_id")
            if not target_id:
                continue

            if op_type == "supersede_assertion":
                superseded.add(target_id)
            elif _is_mutation_added(op_type, op.get("target_type"), target_ops):
                added.add(target_id)
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        pass



def _extract_assertion_ids_from_runs(
    runtime_root: Path, runs: list[dict[str, Any]]
) -> tuple[set[str], set[str]]:
    superseded: set[str] = set()
    added: set[str] = set()
    target_ops = {
        "add_candidate_assertion",
        "add_canonical_assertion",
        "add_approved_assertion",
        "add_relationship",
        "add_architecture_relationship",
        "add_edge",
        "add_relationship_signal",
        "add_action_item",
        "add_decision",
        "add_decision_update",
        "add_proposal",
        "add_risk",
    }
    change_set_names = (
        "graph_change_set.json",
        "graph_change_set_approved.json",
        "graph_change_set_batch_approved.json",
    )
    for run in runs:
        run_id = run.get("run_id")
        if run_id:
            for cs_name in change_set_names:
                cs_file = runtime_root / "runs" / run_id / cs_name
                if cs_file.is_file():
                    _parse_change_set_ops(
                        cs_file,
                        target_ops,
                        superseded,
                        added,
                    )
    return superseded, added


def _is_matching_facet(value: str, expected_added_rids: set[str]) -> bool:
    try:
        rec = json.loads(value).get("retrieval_record", {})
        if rec.get("record_type") == "entity_facet":
            src_ids = set(
                rec.get("extensions", {}).get(
                    "com.partner_advisory.nee.entity-facet-source-record-ids", []
                )
            )
            return bool(src_ids & expected_added_rids)
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return False


def _filter_added_candidate_records(
    candidate_records: dict[str, str],
    baseline_records: dict[str, str],
    expected_added_rids: set[str],
) -> list[str]:
    added_ids: list[str] = []
    for record_id, value in sorted(candidate_records.items()):
        if baseline_records.get(record_id) == value:
            continue
        if not expected_added_rids or record_id in expected_added_rids:
            added_ids.append(record_id)
            continue
        if _is_matching_facet(value, expected_added_rids):
            added_ids.append(record_id)
    return added_ids


def _scoped_release_delta(
    runtime_root: Path | None,
    receipt: dict[str, Any] | None,
    baseline_records: dict[str, str],
    candidate_records: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Return (added_ids, removed_ids) scoped to genuine release changes."""
    if receipt is not None and receipt.get("component_runs") and runtime_root is not None:
        superseded_aids, added_aids = _extract_assertion_ids_from_runs(
            runtime_root, receipt.get("component_runs", [])
        )
        expected_removed_rids = {
            stable_id("retrieval-record", aid, length=28)
            for aid in superseded_aids
        } | {aid for aid in superseded_aids if aid.startswith("retrieval-record-")}
        expected_added_rids = {
            stable_id("retrieval-record", aid, length=28)
            for aid in added_aids
        } | {aid for aid in added_aids if aid.startswith("retrieval-record-")}
        removed_ids = sorted(
            record_id
            for record_id in (set(baseline_records) - set(candidate_records))
            if record_id in expected_removed_rids
        )
        added_ids = _filter_added_candidate_records(
            candidate_records, baseline_records, expected_added_rids
        )
        return added_ids, removed_ids

    added_ids = sorted(
        record_id
        for record_id, value in candidate_records.items()
        if baseline_records.get(record_id) != value
    )
    removed_ids = sorted(set(baseline_records) - set(candidate_records))
    return added_ids, removed_ids


def _first_rank(record_ids: list[str], expected: set[str]) -> int | None:
    return next(
        (rank for rank, record_id in enumerate(record_ids, 1) if record_id in expected),
        None,
    )


def _max_rank_regression(
    baseline_ids: list[str],
    candidate_ids: list[str],
    expected: set[str],
    top_k: int,
) -> int:
    baseline_ranks = {
        record_id: rank
        for rank, record_id in enumerate(baseline_ids, 1)
        if record_id in expected
    }
    candidate_ranks = {
        record_id: rank
        for rank, record_id in enumerate(candidate_ids, 1)
        if record_id in expected
    }
    return max(
        (
            candidate_ranks.get(record_id, top_k + 1) - baseline_rank
            for record_id, baseline_rank in baseline_ranks.items()
        ),
        default=0,
    )


def _critical_case_failed(result: dict[str, Any], critical_top_k: int) -> bool:
    if not result["critical"]:
        return False
    if result["forbidden_leakage"]:
        return True
    expected = set(result["expected_record_ids"])
    if not expected:
        return False
    candidate_ranks = {
        record_id: rank
        for rank, record_id in enumerate(result["candidate_retrieved_record_ids"], 1)
        if record_id in expected
    }
    return set(candidate_ranks) != expected or any(
        rank > critical_top_k for rank in candidate_ranks.values()
    )


def _repeat_rank_drift(record_id_runs: list[list[str]], top_k: int) -> int:
    record_ids = {
        record_id for result in record_id_runs for record_id in result
    }
    return max(
        (
            max(
                (
                    result.index(record_id) + 1
                    if record_id in result
                    else top_k + 1
                )
                for result in record_id_runs
            )
            - min(
                (
                    result.index(record_id) + 1
                    if record_id in result
                    else top_k + 1
                )
                for result in record_id_runs
            )
            for record_id in record_ids
        ),
        default=0,
    )


def validate_retrieved_ranking(
    record_ids: Sequence[str],
    top_k: int | None = None,
) -> list[str]:
    """Validate that retrieved record IDs are well-formed and contain no duplicate IDs."""
    if not isinstance(record_ids, (list, tuple)):
        raise TypeError(f"record_ids must be a sequence of strings, got {type(record_ids).__name__}")
    seen: set[str] = set()
    duplicates: list[str] = []
    for rid in record_ids:
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError(f"Invalid record_id in ranking: {rid!r}")
        if rid in seen:
            duplicates.append(rid)
        seen.add(rid)
    if duplicates:
        raise ValueError(
            f"Duplicate record IDs detected in retrieved ranking: {duplicates}"
        )
    return list(record_ids[:top_k]) if top_k is not None else list(record_ids)


def _to_judgments_map(judgments: Mapping[str, int] | Set[str]) -> Mapping[str, int]:
    if isinstance(judgments, (set, frozenset)):
        return {rid: 1 for rid in judgments}
    if isinstance(judgments, Mapping):
        return judgments
    raise TypeError(f"judgments must be Mapping or Set, got {type(judgments).__name__}")


def standard_reciprocal_rank(
    record_ids: Sequence[str],
    judgments: Mapping[str, int] | Set[str],
    top_k: int = 10,
    relevance_threshold: int = 1,
    expected_positives: Container[str] | None = None,
) -> float | None:
    """Standard TREC Reciprocal Rank at cutoff k with explicit relevance threshold."""
    valid_ids = validate_retrieved_ranking(record_ids, top_k)
    judg_map = _to_judgments_map(judgments)
    has_positives = any(g >= relevance_threshold for g in judg_map.values())
    if expected_positives is not None and not expected_positives:
        has_positives = False
    if not has_positives:
        return None
    for rank, rid in enumerate(valid_ids, 1):
        if judg_map.get(rid, 0) >= relevance_threshold:
            return 1.0 / rank
    return 0.0


def standard_ndcg_at_k(
    record_ids: Sequence[str],
    query_judgments: Mapping[str, int] | Set[str],
    top_k: int = 10,
    gain_mapping: str = "exponential",
) -> float | None:
    """Standard graded nDCG@k using fixed query judgments for ideal DCG calculation."""
    valid_ids = validate_retrieved_ranking(record_ids, top_k)
    judg_map = _to_judgments_map(query_judgments)
    pos_grades = [g for g in judg_map.values() if g > 0]
    if not pos_grades:
        return None

    def _gain(g: int) -> float:
        return (2.0**g - 1.0) if gain_mapping == "exponential" else float(g)

    dcg = sum(
        _gain(judg_map.get(rid, 0)) / math.log2(rank + 1)
        for rank, rid in enumerate(valid_ids, 1)
        if judg_map.get(rid, 0) > 0
    )
    ideal_grades = sorted(pos_grades, reverse=True)[:top_k]
    idcg = sum(_gain(g) / math.log2(rank + 1) for rank, g in enumerate(ideal_grades, 1))
    return float(dcg / idcg) if idcg > 0.0 else None


def standard_recall_at_k(
    record_ids: Sequence[str],
    query_judgments: Mapping[str, int] | Set[str],
    top_k: int = 10,
    relevance_threshold: int = 1,
) -> float | None:
    """Standard Recall@k against fixed positive judgments in ground truth."""
    valid_ids = validate_retrieved_ranking(record_ids, top_k)
    judg_map = _to_judgments_map(query_judgments)
    positives = {rid for rid, g in judg_map.items() if g >= relevance_threshold}
    if not positives:
        return None
    return len(set(valid_ids) & positives) / len(positives)


def compute_dcg(relevances: Sequence[int], k: int = 5) -> float:
    """Standard discounted cumulative gain at cutoff k."""
    if not relevances:
        return 0.0
    return float(
        sum((2.0**rel - 1.0) / math.log2(idx + 2) for idx, rel in enumerate(relevances[:k]) if rel > 0)
    )


def compute_ndcg(
    relevances: Sequence[int],
    k: int = 5,
    ideal_relevances: Sequence[int] | None = None,
) -> float:
    """Standard graded nDCG at cutoff k using fixed query judgments."""
    if not relevances:
        return 0.0
    dcg = compute_dcg(relevances, k)
    ideal_pool = ideal_relevances if ideal_relevances is not None else relevances
    ideal = sorted([r for r in ideal_pool if r > 0], reverse=True)[:k]
    idcg = compute_dcg(ideal, k)
    return float(dcg / idcg) if idcg > 0.0 else 0.0


def compute_ndcg_at_k(
    judgments: Sequence[int],
    k: int = 10,
    ideal_judgments: Sequence[int] | None = None,
) -> float:
    """Standard graded nDCG@k using fixed query judgments for ideal DCG."""
    return compute_ndcg(judgments, k=k, ideal_relevances=ideal_judgments)


def compute_mrr_at_k(
    judgments: Sequence[int],
    k: int = 10,
    relevance_threshold: int = 1,
) -> float:
    """Standard TREC Reciprocal Rank at cutoff k with explicit relevance threshold."""
    if not judgments:
        return 0.0
    for idx, rel in enumerate(judgments[:k]):
        if rel >= relevance_threshold:
            return 1.0 / (idx + 1)
    return 0.0


def _reciprocal_rank(candidate_rank: int | None, expected: set[str]) -> float | None:
    """Compatibility adapter for reciprocal rank.

    Returns None for empty expected (no-answer query).
    Returns 0.0 if not retrieved within cutoff, or 1.0 / rank.
    """
    if not expected:
        return None
    return 0.0 if candidate_rank is None else 1.0 / candidate_rank


def _ndcg_at_k(
    record_ids: list[str],
    expected: set[str] | Mapping[str, int],
    top_k: int,
    qrel_grades: Mapping[str, int] | None = None,
) -> float | None:
    """Compatibility adapter for nDCG@k using standard fixed query judgments."""
    judgments = qrel_grades if (qrel_grades and any(v > 0 for v in qrel_grades.values())) else expected
    if not judgments:
        return None
    return standard_ndcg_at_k(record_ids, judgments, top_k=top_k)


def evaluate_graded_qrel_ranking(
    record_ids: Sequence[str],
    qrel_grades: Mapping[str, int],
    top_k: int = 10,
) -> dict[str, float | None]:
    """Compute graded 0-3 QREL metrics (exponential nDCG@k, MRR@k, Recall@k, hard-negative intrusion)."""
    valid_ids = validate_retrieved_ranking(record_ids, top_k)
    ndcg = standard_ndcg_at_k(valid_ids, qrel_grades, top_k=top_k, gain_mapping="exponential")
    mrr = standard_reciprocal_rank(valid_ids, qrel_grades, top_k=top_k, relevance_threshold=2)
    auth_recall = standard_recall_at_k(valid_ids, qrel_grades, top_k=top_k, relevance_threshold=3)
    supp_recall = standard_recall_at_k(valid_ids, qrel_grades, top_k=top_k, relevance_threshold=2)
    hard_neg_ids = {rid for rid, grade in qrel_grades.items() if grade == 0}
    intrusion_count = sum(1 for rid in valid_ids if rid in hard_neg_ids)
    intrusion_rate = (intrusion_count / len(valid_ids)) if valid_ids else 0.0
    return {
        "ndcg_at_k": ndcg,
        "mrr_at_k": mrr,
        "authoritative_recall_at_k": auth_recall,
        "supporting_recall_at_k": supp_recall,
        "hard_negative_intrusion_rate": intrusion_rate,
    }


def _calc_positive_metrics(positive_cases: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not positive_cases:
        return 0.0, 0.0, 0.0
    recalls = [
        item["candidate_recall_at_k"]
        for item in positive_cases
        if item.get("candidate_recall_at_k") is not None
    ]
    mrrs = [
        item["candidate_reciprocal_rank"]
        for item in positive_cases
        if item.get("candidate_reciprocal_rank") is not None
    ]
    ndcgs = [
        item["candidate_ndcg_at_k"]
        for item in positive_cases
        if item.get("candidate_ndcg_at_k") is not None
    ]
    pos_recall = sum(recalls) / len(recalls) if recalls else 0.0
    pos_mrr = sum(mrrs) / len(mrrs) if mrrs else 0.0
    pos_ndcg = sum(ndcgs) / len(ndcgs) if ndcgs else 0.0
    return pos_recall, pos_mrr, pos_ndcg


def _calc_rejection_rate(no_answer_cases: list[dict[str, Any]]) -> float:
    if not no_answer_cases:
        return 1.0
    rejections = sum(
        1 for item in no_answer_cases
        if not item.get("candidate_first_relevant_rank")
        and not item.get("forbidden_leakage")
        and not (item.get("expected_abstention") and bool(item.get("candidate_retrieved_record_ids")))
    )
    return rejections / len(no_answer_cases)


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate metrics separating positive-query metrics from no-answer rejection.

    Adding no-answer queries does NOT inflate positive-query Recall/MRR/nDCG.
    Empty required datasets fail closed.
    """
    if not results:
        return {
            "case_count": 0,
            "positive_case_count": 0,
            "recall_at_k": 0.0,
            "mrr": 0.0,
            "ndcg_at_k": 0.0,
            "no_answer_case_count": 0,
            "no_answer_rejection_rate": 0.0,
            "status": "FAIL_EMPTY_DATASET",
        }

    positive_cases: list[dict[str, Any]] = []
    no_answer_cases: list[dict[str, Any]] = []
    for item in results:
        expected = item.get("expected_record_ids", [])
        if bool(expected):
            positive_cases.append(item)
        else:
            no_answer_cases.append(item)

    pos_recall, pos_mrr, pos_ndcg = _calc_positive_metrics(positive_cases)
    rejection_rate = _calc_rejection_rate(no_answer_cases)

    return {
        "case_count": len(results),
        "positive_case_count": len(positive_cases),
        "recall_at_k": pos_recall,
        "mrr": pos_mrr,
        "ndcg_at_k": pos_ndcg,
        "no_answer_case_count": len(no_answer_cases),
        "no_answer_rejection_rate": rejection_rate,
    }

