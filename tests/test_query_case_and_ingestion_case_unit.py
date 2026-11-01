"""Unit tests for dual_pipeline.evals.runner_core.query_case and ingestion_case."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.evals.runner_core.constants import EvaluationFailure
from dual_pipeline.evals.runner_core.ingestion_case import _ingestion_case
from dual_pipeline.evals.runner_core.query_case import (
    _query_case,
    _selected_evidence_authorization,
)


def test_selected_evidence_authorization_clean_and_violations() -> None:
    packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec-ok",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "evidence_ids": ["ev-1"],
                }
            },
            {
                "record": {
                    "record_id": "rec-unauth",
                    "classification": "restricted",
                    "audiences": ["executive_only"],
                    "evidence_ids": [],
                }
            },
        ],
        "candidate": [
            {
                "record": {
                    "record_id": "rec-cand",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "evidence_ids": ["ev-2"],
                }
            }
        ],
        "external_public": [],
    }
    claim_map = {
        "claims": [
            {
                "claim_id": "clm-bad",
                "claim_kind": "factual_assertion",
                "evidence": [{"record_id": "rec-cand"}],
            },
            {
                "claim_id": "clm-good",
                "claim_kind": "candidate_hypothesis",
                "evidence": [{"record_id": "rec-cand"}],
            },
        ]
    }
    res = _selected_evidence_authorization(
        packet,
        claim_map,
        requested_classification="internal",
        requested_audiences=["internal_consulting"],
    )
    assert res["selected_record_ids"] == ["rec-cand", "rec-ok", "rec-unauth"]
    assert res["distinct_evidence_ids"] == ["ev-1", "ev-2"]
    assert res["authorization_violation_record_ids"] == ["rec-unauth"]
    assert res["evidence_binding_violation_record_ids"] == ["rec-unauth"]
    assert res["candidate_claim_violation_ids"] == ["clm-bad"]


def test_query_case_rejects_non_fake_quality_control_and_missing_binding(tmp_path: Path) -> None:
    fake_mgr = MagicMock()
    fake_backend = MagicMock()
    case = {
        "case_id": "qc-1",
        "provider_posture": "offline",
        "quality_control": {"mode": "enforced", "judge_verdicts": ["PASS"]},
    }
    with patch(
        "dual_pipeline.evals.runner_core.query_case._bootstrap",
        return_value=(fake_mgr, fake_backend, tmp_path / "g.db", tmp_path / "s.db"),
    ):
        with pytest.raises(EvaluationFailure, match="network-free fake provider posture"):
            _query_case(
                case,
                case_dir=tmp_path,
                case_runtime=tmp_path / "rt",
                seed_graph=tmp_path / "g.db",
                backend=fake_backend,
            )

    case_no_qc = {
        "case_id": "qc-2",
        "provider_posture": "offline",
        "question": "What is the scope?",
        "query_type": "entity_lookup",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "evaluation_scope": "graph_retrieval_qualification",
    }
    with patch(
        "dual_pipeline.evals.runner_core.query_case._bootstrap",
        return_value=(fake_mgr, fake_backend, tmp_path / "g.db", tmp_path / "s.db"),
    ), patch("dual_pipeline.evals.runner_core.query_case.QueryEngine") as mock_qe:
        mock_qe.return_value.ask.return_value = {"status": "COMPLETED", "runtime_binding": None}
        with pytest.raises(EvaluationFailure, match="missing its runtime binding receipt"):
            _query_case(
                case_no_qc,
                case_dir=tmp_path,
                case_runtime=tmp_path / "rt",
                seed_graph=tmp_path / "g.db",
                backend=fake_backend,
            )


def test_ingestion_case_detects_backend_drift(tmp_path: Path) -> None:
    fake_mgr = MagicMock()
    fake_mgr.active_pair.return_value = {"generation_id": "gen-1"}
    fake_backend = MagicMock()
    run_dir = tmp_path / "rt" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "candidate_bundle.json").write_text('{"candidates": [{"entity_mentions": ["a"]}]}', encoding="utf-8")

    case = {
        "case_id": "ing-1",
        "package_path": "README.md",
        "audience": "internal_consulting",
    }
    with patch(
        "dual_pipeline.evals.runner_core.ingestion_case._bootstrap",
        return_value=(fake_mgr, fake_backend, tmp_path / "g.db", tmp_path / "s.db"),
    ), patch(
        "dual_pipeline.evals.runner_core.ingestion_case.capture_runtime_binding",
        side_effect=[
            {"backend": {"id": "b1"}, "policy_digests": {"p": "1"}},
            {"backend": {"id": "b2"}, "policy_digests": {"p": "1"}},
        ],
    ), patch(
        "dual_pipeline.evals.runner_core.ingestion_case.IngestionCompletionGuard"
    ) as mock_guard, patch(
        "dual_pipeline.evals.runner_core.ingestion_case.IntakeEngine"
    ) as mock_intake, patch(
        "dual_pipeline.evals.runner_core.ingestion_case.HybridRetriever"
    ):
        mock_intake.return_value.ingest.return_value = {"run_id": "run-1", "graph_operation_count": 0}
        mock_guard.return_value.classify.return_value = {"classification": {"classification_digest": "d1"}}
        mock_guard.return_value.complete_noop.return_value = {"completion": {"status": "COMPLETED"}}

        from dual_pipeline.drift_guard import DriftError

        with pytest.raises(DriftError, match="ingestion backend changed"):
            _ingestion_case(
                case,
                case_dir=tmp_path,
                case_runtime=tmp_path / "rt",
                seed_graph=tmp_path / "g.db",
                backend=fake_backend,
            )
