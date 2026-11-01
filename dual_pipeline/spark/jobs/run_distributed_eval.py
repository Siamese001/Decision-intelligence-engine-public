"""Dataproc Serverless batch job entrypoint for distributed 11-gate retrieval release evaluation.

Evaluates benchmark QRELS queries across cluster partitions and emits the final sealed receipt.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from dual_pipeline.spark.retrieval_eval_spark import SparkRetrievalReleaseEvaluator

logger = logging.getLogger("run_distributed_eval")


def execute_evaluation_job(
    queries_table: str,
    output_receipt_path: str,
    eval_id: str = "spark-retrieval-eval-001",
    num_partitions: int = 8,
) -> int:
    """Execute distributed retrieval evaluation job."""
    evaluator = SparkRetrievalReleaseEvaluator(mock_mode=False)

    queries = []
    qp = Path(queries_table)
    if qp.is_file():
        try:
            data = json.loads(qp.read_text(encoding="utf-8"))
            queries = data if isinstance(data, list) else data.get("queries", [])
        except Exception as e:
            raise ValueError(f"QUERIES_LOAD_FAILED: Failed to load queries from '{queries_table}': {e}") from e
    else:
        raise ValueError(f"QUERIES_TABLE_NOT_FOUND: Queries table or file not found at '{queries_table}'.")

    res = evaluator.evaluate_queries_distributed(
        queries=queries,
        eval_id=eval_id,
        num_partitions=num_partitions,
    )

    out_p = Path(output_receipt_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    receipt_dict = {
        "eval_id": res.eval_id,
        "is_passing": res.is_passing,
        "gate_results": res.gate_results,
        "passed_gate_count": res.passed_gate_count,
        "total_gate_count": res.total_gate_count,
        "evaluated_query_count": res.evaluated_query_count,
        "metrics": res.metrics,
        "runtime_seconds": res.runtime_seconds,
        "is_safe": res.is_safe,
    }

    out_p.write_text(json.dumps(receipt_dict, indent=2))
    logger.info("Evaluation complete! Passing: %s. Saved receipt to %s", res.is_passing, output_receipt_path)
    return 0 if res.is_passing else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distributed 11-Gate Retrieval Release Evaluator")
    parser.add_argument("--queries-table", default="graph_db.qrels_queries")
    parser.add_argument("--output-receipt-path", required=True)
    parser.add_argument("--eval-id", default="spark-retrieval-eval-001")
    parser.add_argument("--num-partitions", type=int, default=8)

    args = parser.parse_args(argv)
    return execute_evaluation_job(
        queries_table=args.queries_table,
        output_receipt_path=args.output_receipt_path,
        eval_id=args.eval_id,
        num_partitions=args.num_partitions,
    )


if __name__ == "__main__":
    sys.exit(main())
