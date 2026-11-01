"""LLM Judge & Retrieval Evaluation MCP Server (Rank 4).

Exposes token-budgeted multi-rubric judge execution, 11-gate BGE-M3 retrieval
qualification benchmarking, sealed evaluation receipt generation, and hybrid weight
calibration as native Model Context Protocol (MCP) tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config.request_deadline import deadline_scope
from mcp.server.mcpserver import MCPServer

from tools.mcp_judge_eval_core import (
    CANONICAL_11_GATES,
    _build_legacy_aliases,
    _evaluate_live_gates,
    _load_sealed_evaluation,
    _resolve_evaluation_paths,
)
from tools.mcp_judge_eval_core import (
    calibrate_hybrid_weights as _core_calibrate_hybrid_weights,
)
from tools.mcp_judge_eval_core import (
    eval_retrieval_11gates as _core_eval_retrieval_11gates,
)
from tools.mcp_judge_eval_core import (
    judge_evaluate_candidate as _core_judge_evaluate_candidate,
)
from tools.mcp_judge_eval_core import (
    seal_retrieval_receipt as _core_seal_retrieval_receipt,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]

server = MCPServer(
    name="judge-retrieval-eval",
    instructions=(
        "LLM Judge & Retrieval Evaluation Server — provides token-budgeted judge evaluation, "
        "11-gate BGE-M3 retrieval benchmark qualification, sealed receipt generation, and hybrid search calibration."
    ),
)


@server.tool(name="judge_evaluate_candidate")
def judge_evaluate_candidate(
    rubric_id: str,
    candidate_text: str,
    context_envelope_json: str = "{}",
    token_budget: int = 4000,
) -> str:
    """Evaluate candidate text against a governed rubric with smart token-budget truncation protection."""
    with deadline_scope(seconds=60.0):
        return _core_judge_evaluate_candidate(
            rubric_id, candidate_text, context_envelope_json, token_budget
        )


@server.tool(name="eval_retrieval_11gates")
def eval_retrieval_11gates(
    runtime_root: str = "",
    synthetic_allowed: bool = False,
    evaluation_receipt_path: str = "",
) -> str:
    """Evaluate the active retrieval pair against all 11 non-compensating release gates."""
    with deadline_scope(seconds=120.0):
        return _core_eval_retrieval_11gates(
            runtime_root=runtime_root,
            synthetic_allowed=synthetic_allowed,
            evaluation_receipt_path=evaluation_receipt_path,
            workspace_root=WORKSPACE_ROOT,
        )


@server.tool(name="seal_retrieval_receipt")
def seal_retrieval_receipt(
    evaluation_results_json: str,
    output_dir: str = "",
    receipt_id: str = "",
) -> str:
    """Generate a sealed, cryptographically bound retrieval evaluation receipt."""
    with deadline_scope(seconds=60.0):
        return _core_seal_retrieval_receipt(
            evaluation_results_json=evaluation_results_json,
            output_dir=output_dir,
            receipt_id=receipt_id,
            workspace_root=WORKSPACE_ROOT,
        )


@server.tool(name="calibrate_hybrid_weights")
def calibrate_hybrid_weights(
    dense_weight: float = 0.05,
    sparse_weight: float = 0.15,
    colbert_weight: float = 0.10,
    bm25_weight: Optional[float] = None,
) -> str:
    """Validate and normalize dense, sparse, BM25, and ColBERT weights to sum to 1.0 for hybrid scoring."""
    with deadline_scope(seconds=30.0):
        return _core_calibrate_hybrid_weights(
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            colbert_weight=colbert_weight,
            bm25_weight=bm25_weight,
        )


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

__all__ = [
    "CANONICAL_11_GATES",
    "_build_legacy_aliases",
    "_evaluate_live_gates",
    "_load_sealed_evaluation",
    "_resolve_evaluation_paths",
    "calibrate_hybrid_weights",
    "eval_retrieval_11gates",
    "judge_evaluate_candidate",
    "seal_retrieval_receipt",
    "server",
]
