"""Unit tests for evals.qrels_query_generator and evals.adversarial_weight_sensitivity."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from evals.adversarial_weight_sensitivity import (
    component_rank_spread,
    generate_queries_for_record,
    retrieve_with_diagnostics,
    rrf_score,
    sweep_weights,
)
from evals.qrels_query_generator import (
    GROUNDED_QUERY_DEFINITIONS,
    cosine_similarity,
    generate_and_stratify_queries,
)


def test_cosine_similarity_vectors_and_zero_norm() -> None:
    assert abs(cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) - 1.0) < 1e-6
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-6
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_generate_and_stratify_queries_with_fake_backend(tmp_path: Path) -> None:
    fake_backend = MagicMock()
    fake_backend.model_id = "BAAI/bge-m3"
    fake_backend.backend_id = "bge-m3-dev"
    fake_backend.model_revision = "rev-unit-test"

    def _encode_many(batch: list[str]) -> list[dict[str, list[float]]]:
        out = []
        for idx, _text in enumerate(batch):
            vec = [0.1] * 16
            vec[idx % 16] = 0.9
            out.append({"dense": vec})
        return out

    fake_backend.encode_many.side_effect = _encode_many
    output_file = tmp_path / "qrels_query_set.json"

    with patch("evals.qrels_query_generator.get_backend", return_value=fake_backend):
        payload = generate_and_stratify_queries(
            runtime_root=tmp_path,
            output_path=output_file,
        )

    assert output_file.is_file()
    receipt_file = tmp_path / "wave3a_qrels_generation_receipt.json"
    assert receipt_file.is_file()
    assert payload["total_queries"] == sum(len(t["variants"]) for t in GROUNDED_QUERY_DEFINITIONS)
    assert "tier1_paraphrase_clusters" in payload["stratification_metrics"]
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved["total_queries"] == payload["total_queries"]


def test_generate_queries_for_record_variants() -> None:
    artifact = {
        "title": "Project Beacon ROUTES_TO AI Hub workstream",
        "retrieval_text": "Project Beacon ROUTES_TO AI Hub workstream for field telemetry",
        "record_type": "assertion",
    }
    queries = generate_queries_for_record(artifact)
    assert queries["title_exact"] == artifact["title"]
    assert "What do we know about" in queries["paraphrase"]
    assert "overall program landscape" in queries["diluted"]
    assert "partial" in queries
    assert "Digital Guide" in queries["synonym_drift"]


def test_component_rank_spread_and_rrf_score() -> None:
    result = {
        "results": [
            {
                "record_id": "rec-1",
                "fused_rank": 2,
                "dense_rank": 1,
                "sparse_rank": 9,
                "colbert_rank": 3,
                "bm25_rank": 5,
            }
        ]
    }
    found = component_rank_spread(result, "rec-1")
    assert found["found"] is True
    assert found["spread"] == 8
    assert found["fused_rank"] == 2

    missing = component_rank_spread(result, "rec-missing")
    assert missing["found"] is False
    assert missing["spread"] is None

    score = rrf_score({"dense": 1, "sparse": 2}, {"dense": 0.6, "sparse": 0.4}, k=60)
    assert abs(score - (0.6 / 61.0 + 0.4 / 62.0)) < 1e-9


def test_sweep_weights_and_retrieve_with_diagnostics(tmp_path: Path) -> None:
    cases = [{"ranks": {"dense": 1, "sparse": 5, "bm25": 3, "colbert": 2}}]
    swept = sweep_weights(cases, step=0.25)
    assert len(swept) == 1
    assert swept[0]["count"] == 1
    assert abs(sum(swept[0]["weights"].values()) - 1.0) < 1e-6

    retriever = MagicMock()
    retriever.sidecar_path = tmp_path / "sidecar.sqlite"
    retriever.retrieve_lane.return_value = {"results": [{"record_id": "r1"}]}
    with patch(
        "evals.adversarial_weight_sensitivity.governed_bm25_assertion_ranks",
        return_value=({"approved": {"r1": 1}}, True),
    ):
        diag = retrieve_with_diagnostics(retriever, "test query", "approved", tmp_path / "graph.sqlite")
    assert diag == {"results": [{"record_id": "r1"}]}

    retriever.retrieve_lane.side_effect = RuntimeError("db error")
    with patch(
        "evals.adversarial_weight_sensitivity.governed_bm25_assertion_ranks",
        return_value=({}, False),
    ):
        assert retrieve_with_diagnostics(retriever, "test query", "approved", tmp_path / "graph.sqlite") is None
