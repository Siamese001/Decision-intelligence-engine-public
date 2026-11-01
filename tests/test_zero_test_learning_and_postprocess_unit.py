"""Hermetic unit tests for zero-test hotspots (learning_commit, meeting_postprocess) and HIST-01 escapes (clearance_cache, capability_bridge)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.graph_retrieval_core import capability_bridge, clearance_cache
from dual_pipeline.normalization_core import meeting_postprocess
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.uwg_core import learning_commit


def _build_gateway_stub(tmp_path: Path) -> Any:
    class _GatewayStub:
        def __init__(self, root: Path) -> None:
            self.runtime_root = root

        def _snapshot_active_for_learning(self) -> tuple[dict[str, Any], str, str]:
            return ({"generation_id": "gen_test_01"}, "b" * 64, "gen_test_01")

        def _verify_active_pair_unchanged(
            self, active_before: Any, graph_sha256: str, generation_id: str
        ) -> None:
            assert active_before["generation_id"] == generation_id
            assert graph_sha256 == "b" * 64

    return create_autospec(_GatewayStub(tmp_path), instance=True, spec_set=True)


def test_uwg_learning_commit_and_bidirectional_rollback_unit(tmp_path: Path) -> None:
    """Verify governed UWG learning parameter commit, CAS enforcement, duplicate rejection, and absent-key rollback."""
    cfg_path = tmp_path / "runtime_parameters.json"
    cfg_path.write_text(
        json.dumps({"hitl": {"ambiguity_margin_threshold": 0.20}}, indent=2),
        encoding="utf-8",
    )
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    gw = _build_gateway_stub(tmp_path)
    gw.runtime_root = tmp_path
    gw._snapshot_active_for_learning.return_value = (
        {"generation_id": "gen_test_01"},
        "b" * 64,
        "gen_test_01",
    )

    proposal = {
        "proposal_id": "prop_learn_001",
        "proposal_type": "hitl_calibration",
        "canonical_digest": {"value": "a" * 64},
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "parameter_name": "hitl.ambiguity_margin_threshold",
                "current_value": 0.20,
                "proposed_value": 0.18,
            }
        },
    }
    approval_receipt = {
        "receipt_id": "rcpt_human_001",
        "proposal_id": "prop_learn_001",
        "approved_by": "operator_human",
        "approval_origin": "human_interactive",
    }

    with (
        patch(
            "dual_pipeline.hitl_core.envelope.validate_approval_origin",
            autospec=True,
            return_value=(True, "human_verified"),
        ),
        patch(
            "dual_pipeline.calibration.verify_calibration_approval",
            autospec=True,
            return_value=True,
        ),
    ):
        res = learning_commit.commit_learning_parameters(
            gw,
            {
                "proposal": proposal,
                "approval_receipt": approval_receipt,
                "proposals_dir": proposals_dir,
                "config_path": cfg_path,
                "skip_decision_clearance": True,
                "applied_parameters": {
                    "hitl.ambiguity_margin_threshold": 0.18,
                    "copilot.max_speakable_words": 40,
                },
            },
        )

        assert res["status"] == "APPLIED"
        assert res["graph_delta_counts"]["total_delta"] == 0
        reg_after = ParameterRegistry(config_path=cfg_path)
        assert reg_after.get("hitl.ambiguity_margin_threshold") == pytest.approx(0.18)
        assert reg_after.get("copilot.max_speakable_words") == 40

        # Duplicate apply must fail closed
        with pytest.raises(RuntimeError, match="DUPLICATE_APPLY_REJECTED"):
            learning_commit.commit_learning_parameters(
                gw,
                {
                    "proposal": proposal,
                    "approval_receipt": approval_receipt,
                    "proposals_dir": proposals_dir,
                    "config_path": cfg_path,
                    "skip_decision_clearance": True,
                },
            )

        # Rollback restores previous value and prunes absent key
        rb_res = learning_commit.rollback_learning_parameters(
            gw, res["rollback_receipt_path"]
        )
        assert rb_res["status"] == "ROLLED_BACK"
        assert "copilot.max_speakable_words" in rb_res["pruned_parameters"]
        reg_restored = ParameterRegistry(config_path=cfg_path)
        assert reg_restored.get("hitl.ambiguity_margin_threshold") == pytest.approx(0.20)
        raw_restored = reg_restored._load_json()
        found_absent, _ = reg_restored._lookup_dotted(raw_restored, "copilot.max_speakable_words")
        assert found_absent is False


def test_uwg_learning_commit_rejects_out_of_bounds_and_tampered_replay(tmp_path: Path) -> None:
    """Verify parameter bounds validation and replay receipt tamper detection."""
    proposal = {
        "proposal_id": "prop_bounds_002",
        "proposal_type": "retrieval_weight",
        "canonical_digest": {"value": "expected_digest_abc"},
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "parameter_name": "hitl.ambiguity_margin_threshold",
                "proposed_value": 0.99,
            }
        },
    }
    with pytest.raises(ValueError, match="UWG_COMMIT_REJECTED"):
        learning_commit._resolve_and_validate_learning_params(proposal, {})

    valid_prop = {
        "proposal_id": "prop_replay_003",
        "proposal_type": "retrieval_weight",
        "canonical_digest": {"value": "expected_digest_abc"},
        "extensions": {"com.partner_advisory.nee.learning": {"category": "retrieval_weight"}},
    }
    with pytest.raises(ValueError, match="REPLAY_RECEIPT_REQUIRED"):
        learning_commit._verify_replay_qualification(valid_prop, "prop_replay_003", tmp_path, {})

    tampered_sim = {
        "status": "SIMULATED_PASS",
        "success": True,
        "proposal_id": "prop_replay_003",
        "proposal_digest": "wrong_digest_xyz",
    }
    with pytest.raises(ValueError, match="REPLAY_TAMPERED"):
        learning_commit._validate_simulation_receipt(tampered_sim, valid_prop, "prop_replay_003")


def test_meeting_postprocess_source_version_actions_and_deduplication() -> None:
    """Verify deterministic source version derivation, action extraction, and candidate deduplication."""
    pkg = {
        "extensions": {
            "com.partner_advisory.nee.inline-canonical-promotion": True,
            "com.partner_advisory.nee.action-evidence-ids": {"act_1": ["stmt_extra_1"]},
        },
        "speakers": [
            {
                "speaker_ref": "spk_1",
                "reported_name": "Eduardo",
                "statements": [{"statement_id": "st_1", "text": "Deploy S3 ingestion pipeline."}],
            }
        ],
        "actions": [
            {
                "action_id": "act_1",
                "action": "Finalize S3 bucket permissions.",
                "reported_owner": "Eduardo",
                "locator": {"kind": "timestamp", "value": "00:05:10"},
            },
            {
                "action_id": "act_2",
                "action": "finalize S3 bucket permissions.",
                "reported_owner": "Eduardo",
                "locator": {"kind": "timestamp", "value": "00:06:10"},
            },
        ],
    }
    meeting = {"source_hash": "hash_abc123"}
    c_dig, sv_id = meeting_postprocess.resolve_meeting_source_version(
        pkg, meeting, "src_001", "pkg_dig_fallback"
    )
    assert len(c_dig) == 64
    assert sv_id.startswith("source-version-")

    inline_promo, posture, auth_rank, conf, _ = meeting_postprocess.resolve_meeting_posture_and_authority(
        pkg, {"governed_internal": 8}
    )
    assert inline_promo is True
    assert posture == "governed_internal"
    assert auth_rank == 8
    assert conf == pytest.approx(0.85)

    candidates: list[dict[str, Any]] = []
    ev_calls: list[str] = []

    def _add_ev(aid: str, txt: str, loc: dict[str, Any]) -> str:
        ev_calls.append(aid)
        return f"ev_{aid}"

    meeting_postprocess.extract_action_candidates(
        pkg,
        run_id="run_01",
        meeting_title="LENS Architecture Review",
        classification="internal",
        default_audiences=["internal_consulting"],
        add_evidence=_add_ev,
        evidence_by_identifier={"stmt_extra_1": "ev_stmt_extra_1"},
        candidates=candidates,
    )
    assert len(candidates) == 1
    assert candidates[0]["normalized_text"] == "Eduardo will finalize S3 bucket permissions."
    assert set(candidates[0]["evidence_ids"]) == {"ev_act_1", "ev_stmt_extra_1", "ev_act_2"}

    dup_list = [
        {
            "candidate_id": "c1",
            "candidate_type": "assertion",
            "normalized_text": "Amazon S3 stores inspection imagery in LENS Sync.",
            "evidence_ids": ["e1"],
            "entity_mentions": [{"reported_text": "Amazon S3", "stable_identifier": None}],
        },
        {
            "candidate_id": "c2",
            "candidate_type": "decision",
            "normalized_text": "amazon s3 stores inspection imagery.",
            "evidence_ids": ["e2"],
            "entity_mentions": [{"reported_text": "LENS", "stable_identifier": None}],
        },
    ]
    deduped = meeting_postprocess.deduplicate_candidates(dup_list)
    assert len(deduped) == 1
    assert deduped[0]["evidence_ids"] == ["e1", "e2"]
    assert len(deduped[0]["entity_mentions"]) == 2


def test_clearance_cache_and_capability_bridge_hist01_regressions(tmp_path: Path) -> None:
    """Verify HIST-01 regressions in clearance_cache (missing sidecar guard & lane checks) and capability_bridge."""
    clearance_cache.clear_clearance_caches()
    missing_sidecar = tmp_path / "nonexistent_sidecar.sqlite"
    missing_graph = tmp_path / "nonexistent_graph.sqlite"
    assert clearance_cache.record_tokens_cached(missing_sidecar, missing_graph) == {}

    assert clearance_cache._check_lane_and_currentness(
        "approved", {"validation_state": "superseded", "currentness_state": "current"}
    ) is False
    assert clearance_cache._check_lane_and_currentness(
        "approved",
        {
            "validation_state": "verified",
            "currentness_state": "current",
            "retrieval_eligible": 1,
            "canonical_status": "canonical",
            "canonical_eligible": 1,
        },
    ) is True
    assert clearance_cache._check_lane_and_currentness(
        "candidate",
        {
            "validation_state": "pending",
            "currentness_state": "historical",
            "canonical_status": "historical_candidate",
        },
    ) is True

    # Capability bridge alias & virtual edge resolution
    assert capability_bridge.resolve_canonical_tech_alias("amazon s3") == "cloud_s3_storage"
    assert capability_bridge.resolve_canonical_tech_alias("aws s3 bucket") == "cloud_s3_storage"
    assert capability_bridge._phonetic_surname_key("Reiss") == capability_bridge._phonetic_surname_key("Ries")

    db_path = tmp_path / "bridge_graph.sqlite"
    conn = connect_sqlite(db_path)
    try:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, active INTEGER)")
        conn.execute(
            "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, object_node_id TEXT, object_literal TEXT, currentness_state TEXT)"
        )
        conn.executemany(
            "INSERT INTO node VALUES (?, ?, ?, 1)",
            [
                ("n_s3_a", "Amazon S3", "technical_anchor"),
                ("n_s3_b", "AWS S3", "system"),
                ("n_drone", "Skydio Drone", "technical_anchor"),
                ("n_cv", "Buzz Solutions Vision Model", "technical_anchor"),
            ],
        )
        conn.execute(
            "INSERT INTO assertion VALUES ('a1', 'n_drone', NULL, 'aerial drone inspection imagery', 'current')"
        )
        conn.commit()
        pairs = capability_bridge.collect_csr_virtual_edge_pairs(conn)
        assert ("n_s3_a", "n_s3_b") in pairs
        assert any("n_drone" in p for p in pairs)
    finally:
        conn.close()
