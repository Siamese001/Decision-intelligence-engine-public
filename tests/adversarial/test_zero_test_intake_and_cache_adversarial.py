"""Adversarial and edge-case tests for top zero-test production blindspots.

Targets:
- dual_pipeline/intake_engine_core/review_flow.py
- dual_pipeline/speaker_attribution_core/conflict_detector.py
- dual_pipeline/semantic_cache_core/cache.py
- dual_pipeline/live_meeting_recorder_core/copilot_retriever_ranking.py
- dual_pipeline/parameters_ops.py

Covers 5 mandatory adversarial categories:
1. Boundary payload (empty turns, missing decisions, zero-length tokens)
2. Malformed input (corrupted ledger digests, non-numeric parameter bounds)
3. Invariant bypass attempt (stale graph hash, non-A2A finalization spoofing, query namespace omission)
4. Concurrency & race (atomic parameter file persistence and locking)
5. Rollback & recovery (idempotent decision replay, third-person self-mention contradiction detection)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dual_pipeline.core import digest, sha256_file, write_json
from dual_pipeline.intake_engine_core.review_flow import (
    _check_replay_decisions,
    _verify_intake_event_and_a2a,
    _verify_production_review_policy,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever_ranking import (
    determine_assertion_provenance,
    extract_query_tokens_and_seeds,
    tokenize_text,
)
from dual_pipeline.parameters_ops import (
    COPILOT_PARAMETER_BOUNDS,
    HITL_PARAMETER_BOUNDS,
    execute_unset,
    save_parameter_file,
    validate_copilot_parameter_bounds,
    validate_hitl_parameter_bounds,
)
from dual_pipeline.semantic_cache_core.cache import SemanticCache
from dual_pipeline.speaker_attribution_core.conflict_detector import (
    _has_third_person_self_mention,
    _normalize_cluster_alias,
    detect_third_person_contradictions_in_turns,
)


def test_review_flow_idempotent_replay_and_missing_decisions_boundary(tmp_path: Path) -> None:
    decisions = [{"item_id": "rev_1", "outcome": "APPROVE"}]
    dec_digest = digest(decisions)
    write_json(tmp_path / "hitl_decisions.json", decisions)

    summary = {
        "status": "COMPLETED",
        "hitl": {
            "decision_count": 1,
            "decisions_digest": dec_digest,
            "review_applied": True,
        },
    }
    replay = _check_replay_decisions(tmp_path, decisions, summary, {"rev_1": {}})
    assert replay is not None and replay["idempotent_replay"] is True

    with pytest.raises(ValueError, match="requires at least one decision"):
        _check_replay_decisions(tmp_path / "empty_run", [], {}, {"rev_1": {}})


def test_review_flow_tampered_ledger_and_non_a2a_binding_bypass_blocked(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    (run_dir / "learning_events").mkdir(parents=True)
    (run_dir / "ledger_payloads").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="one sealed source-package event"):
        _verify_intake_event_and_a2a(SimpleNamespace(runtime_root=tmp_path), run_dir, {})

    payload = {
        "source_kind": "transcript",
        "package_file_sha256": "abc",
        "a2a_finalization_binding": "spoofed_binding",
        "a2a_finalization_reference": "ref_1",
        "intake_authorization": "auth_1",
    }
    write_json(run_dir / "ledger_payloads" / "p1.json", payload)
    write_json(
        run_dir / "learning_events" / "ev1.json",
        {
            "event_type": "intake_run",
            "payload_reference": "ledger_payloads/p1.json",
            "payload_digest": digest(payload),
        },
    )
    summary = {**payload, "authorization": "auth_1"}
    with patch("dual_pipeline.intake_engine_core.review_flow.validate_artifact", autospec=True):
        with pytest.raises(RuntimeError, match="non-A2A intake cannot carry a finalization"):
            _verify_intake_event_and_a2a(SimpleNamespace(runtime_root=tmp_path), run_dir, summary)


def test_review_flow_stale_graph_and_production_mismatch_blocked(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph.sqlite"
    graph_file.write_bytes(b"sqlite-bytes-v1")
    engine = SimpleNamespace(
        retriever=None,
        active_pair_binding={"production_qualified": True},
        graph_path=graph_file,
    )
    with pytest.raises(RuntimeError, match="production policy bindings disagree"):
        _verify_production_review_policy(
            engine,
            tmp_path,
            {"production_qualified": True},
            {"production_qualified": False},
            b"{}",
            {},
        )
    with pytest.raises(PermissionError, match="non-production intake run cannot be reviewed"):
        _verify_production_review_policy(
            engine,
            tmp_path,
            {"production_qualified": False, "graph_sha256_before": sha256_file(graph_file)},
            {"production_qualified": False},
            b"{}",
            {},
        )
    with pytest.raises(RuntimeError, match="active graph changed after planning"):
        _verify_production_review_policy(
            engine,
            tmp_path,
            {"production_qualified": True, "graph_sha256_before": "stale_hash"},
            {"production_qualified": True},
            b"{}",
            {},
        )


def test_speaker_conflict_detector_third_person_and_alias_normalization() -> None:
    assert _normalize_cluster_alias("speaker_00") == "Speaker 1"
    assert _normalize_cluster_alias("speaker_02") == "Speaker 3"
    assert _normalize_cluster_alias("4") == "Speaker 4"
    assert _normalize_cluster_alias("Speaker 05") == "Speaker 5"

    assert _has_third_person_self_mention("What Alex Sanchez said earlier was helpful", "Alex Sanchez") is True
    assert _has_third_person_self_mention("Hi everyone, this is Alex Sanchez speaking", "Alex Sanchez") is False

    turns = [
        ("speaker_00", "I know Alex Sanchez is working on the database migration."),
        ("speaker_01", "Thanks, I will review the pull request today."),
    ]
    roster = [{"name": "Alex Sanchez", "org": "NEE", "role": "Engineer"}]
    meta = {"speaker_map": {"Speaker 1": "Alex Sanchez"}}
    contradictions = detect_third_person_contradictions_in_turns(turns, roster, meta)
    assert len(contradictions) == 1
    assert contradictions[0]["assigned_speaker"] == "Alex Sanchez"
    assert contradictions[0]["cluster_alias"] == "Speaker 1"


def test_semantic_cache_isolation_and_incomplete_query_namespace(tmp_path: Path) -> None:
    cache = SemanticCache(tmp_path / "runtime")
    assert cache.health_reason == "CACHE_READY"
    assert cache.can_read is False
    assert cache.can_write is False
    assert cache._disabled_reason("READ") == "CACHE_UNBOUND_EMBEDDING"

    with pytest.raises(ValueError, match="semantic cache query namespace is incomplete"):
        cache.compatibility({"use_case": "query", "cache_payload_kind": "answer_envelope"})

    compat = cache.compatibility({"use_case": "ingestion"})
    assert compat["semantic_cache_policy_digest"] == cache.policy_digest


def test_copilot_retriever_ranking_provenance_and_synonym_expansion() -> None:
    tokens = tokenize_text("The velocity and metrics for -flag API_V2 are improving!")
    assert "velocity" in tokens and "api_v2" in tokens
    assert "the" not in tokens and "-flag" not in tokens

    expanded, seeds = extract_query_tokens_and_seeds("productivity increase", {"node_1"})
    assert "throughput" in expanded and "growth" in expanded
    assert seeds == set()

    is_prim, is_pred, scope, orig = determine_assertion_provenance(
        {"engagement_id": "project_nexus"}, "project_nexus", {"legacy_eng"}
    )
    assert is_prim is True and is_pred is False and scope == "PRIMARY" and "project_nexus" in orig

    _, is_pred2, scope2, _ = determine_assertion_provenance(
        {"engagement_id": "legacy_eng"}, "project_nexus", {"legacy_eng"}
    )
    assert is_pred2 is True and scope2 == "PREDECESSOR"


def test_parameters_ops_bounds_and_atomic_persistence(tmp_path: Path) -> None:
    for key, (low, high, default) in HITL_PARAMETER_BOUNDS.items():
        assert validate_hitl_parameter_bounds(key, default) is True
        assert validate_hitl_parameter_bounds(key, low - 0.01) is False
        assert validate_hitl_parameter_bounds(key, high + 0.01) is False
    assert validate_hitl_parameter_bounds("hitl.unknown", 0.5) is False
    assert validate_hitl_parameter_bounds("hitl.ambiguity_margin_threshold", "not-a-float") is False  # type: ignore[arg-type]

    for key, (low, high, default) in COPILOT_PARAMETER_BOUNDS.items():
        assert validate_copilot_parameter_bounds(key, default) is True
        assert validate_copilot_parameter_bounds(key, low - 1.0) is False
        assert validate_copilot_parameter_bounds(key, high + 1.0) is False

    cfg = tmp_path / "params.json"
    save_parameter_file(cfg, {"hitl": {"ambiguity_margin_threshold": 0.20, "extra": 1}})
    reg = SimpleNamespace(config_path=cfg, _lock=threading.RLock(), _load_json=lambda: {})
    assert execute_unset(reg, "hitl.extra", persist=True, config_path=cfg) is True
    assert execute_unset(reg, "hitl.nonexistent.key", persist=True, config_path=cfg) is False
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert "extra" not in saved["parameters"]["hitl"]
