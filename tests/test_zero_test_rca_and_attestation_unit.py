"""Hermetic unit and boundary isolation tests for zero-test RCA SSOT checks, DSSE attestation storage, L0 speaker policy, and entity healing."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    clean_non_placeholder_speaker,
    extract_inline_turn_speaker,
    has_inline_speaker_contradiction,
    has_non_promotable_qualifiers,
    has_stale_candidate_qualifiers,
    has_synthetic_or_cross_project_pollution,
    is_degenerate_single_token_claim,
    is_placeholder_speaker,
    is_raw_spoken_dialogue,
    is_spoken_meeting_source,
    is_structured_profile_card_claim,
    normalize_candidate_claim_text,
    parse_qualifiers_dict,
    resolve_deterministic_speaker_or_author,
    speakers_match_identity,
    sync_canonical_qualifiers_json,
)
from dual_pipeline.transcript_stages_core.graph_post_entity_healer import (
    align_canonical_signals,
    heal_and_align_entities,
)
from tools.attestation_core.envelope import (
    PR_REVIEW_PREDICATE_TYPE,
    READINESS_PREDICATE_TYPE,
)
from tools.attestation_core.storage import (
    check_readiness_qualification,
    get_attestation_path,
    load_attestation_by_tree,
    save_readiness_attestation,
)
from tools.scan_ssot_configuration_violations_core.rca_checks import (
    _check_rca_01_duplicate_keys,
    _check_rca_02_shadow_model_registry,
    _check_rca_03_hitl_margin,
    _check_rca_04_wal_timeout,
    _check_rca_05_parameter_defaults,
    _check_rca_06_project_taxonomy,
    _check_rca_07_machine_paths,
    _check_rca_08_gate_count,
    check_rca_ssot_violations,
)


def _seed_valid_ssot_repo(root: Path) -> None:
    """Populate a minimal valid repository tree that passes all RCA_01..RCA_08 checks."""
    (root / "config" / "governance").mkdir(parents=True, exist_ok=True)
    (root / "config" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "dual_pipeline" / "transcript_metadata_core").mkdir(parents=True, exist_ok=True)
    (root / "tools").mkdir(parents=True, exist_ok=True)

    proj_reg = {
        "domains": {
            "NEA": {"workstreams": {"dociq": {"short_id": "dociq"}}},
            "EDO": {"workstreams": {}},
            "APEX_TRANS": {"workstreams": {}},
            "NEE_IT": {"workstreams": {}},
            "ENTERPRISE_OPS": {"workstreams": {}},
            "INTERNAL_CONSULTING": {"workstreams": {}},
            "MARKET_INTELLIGENCE": {"workstreams": {}},
        },
        "aliases": {"dociq_alias": "dociq"},
    }
    (root / "config" / "governance" / "project_registry.json").write_text(
        json.dumps(proj_reg), encoding="utf-8"
    )
    params = {
        "parameters": {
            "hitl": {"ambiguity_margin_threshold": 0.2},
            "storage": {"sqlite_wal_busy_timeout_ms": 30000},
        }
    }
    (root / "config" / "runtime" / "parameters.json").write_text(
        json.dumps(params), encoding="utf-8"
    )
    (root / "config" / "runtime" / "model_registry.json").write_text("{}", encoding="utf-8")
    (
        root
        / "dual_pipeline"
        / "transcript_metadata_core"
        / "canonical_enterprise_directory.json"
    ).write_text("{}", encoding="utf-8")
    (root / "config" / "runtime" / "speaker_memory.json").write_text("{}", encoding="utf-8")
    (root / "dual_pipeline" / "parameters.py").write_text(
        "min_provenance_score: float = 0.15\n", encoding="utf-8"
    )
    (root / "dual_pipeline" / "release_thresholds.py").write_text(
        "output1_pass_threshold: int = 90\n", encoding="utf-8"
    )
    (root / "tools" / "readiness_core.py").write_text(
        "# Gate 32: Direct Graph Write Path Linter\n", encoding="utf-8"
    )
    (root / "config" / "governance" / "gate_manifest.json").write_text(
        json.dumps({"total_registered_stages": 34, "stages": list(range(34))}),
        encoding="utf-8",
    )


def test_rca_checks_clean_and_all_eight_violation_detectors(tmp_path: Path) -> None:
    """Verifies check_rca_ssot_violations passes on a clean tree and catches RCA_01..RCA_08 violations."""
    _seed_valid_ssot_repo(tmp_path)
    assert check_rca_ssot_violations(tmp_path) == []

    # RCA_01: Duplicate JSON key and invalid JSON
    (tmp_path / "config" / "runtime" / "model_registry.json").write_text(
        '{"tier": "a", "tier": "b"}', encoding="utf-8"
    )
    (tmp_path / "config" / "runtime" / "speaker_memory.json").write_text(
        "{malformed", encoding="utf-8"
    )
    v01 = _check_rca_01_duplicate_keys(tmp_path)
    issues_01 = {v["issue"] for v in v01}
    assert "DUPLICATE_JSON_KEY" in issues_01
    assert "INVALID_JSON" in issues_01

    # RCA_02: Shadow model registry
    shadow = tmp_path / "config" / "runtime" / "model_registry.yaml"
    shadow.write_text("models: []\n", encoding="utf-8")
    v02 = _check_rca_02_shadow_model_registry(tmp_path)
    assert len(v02) == 1 and v02[0]["issue"] == "SHADOW_MODEL_REGISTRY"

    # RCA_03 & RCA_04: HITL margin drift and WAL timeout drift
    (tmp_path / "config" / "runtime" / "parameters.json").write_text(
        json.dumps(
            {
                "parameters": {
                    "hitl": {"ambiguity_margin_threshold": 0.15},
                    "storage": {"sqlite_wal_busy_timeout_ms": 5000},
                }
            }
        ),
        encoding="utf-8",
    )
    v03 = _check_rca_03_hitl_margin(tmp_path)
    v04 = _check_rca_04_wal_timeout(tmp_path)
    assert len(v03) == 1 and v03[0]["issue"] == "HITL_MARGIN_DRIFT"
    assert len(v04) == 1 and v04[0]["issue"] == "STORAGE_WAL_TIMEOUT_DRIFT"

    # RCA_05: Parameter default mismatch
    (tmp_path / "dual_pipeline" / "parameters.py").write_text(
        "min_provenance_score: float = 0.3\n", encoding="utf-8"
    )
    (tmp_path / "dual_pipeline" / "release_thresholds.py").write_text(
        "output1_pass_threshold = 95\n", encoding="utf-8"
    )
    v05 = _check_rca_05_parameter_defaults(tmp_path)
    assert {v["issue"] for v in v05} == {
        "PARAMETER_DEFAULT_MISMATCH",
        "RELEASE_THRESHOLD_DEFAULT_MISMATCH",
    }

    # RCA_06: Missing categories and missing dociq workstream
    (tmp_path / "config" / "governance" / "project_registry.json").write_text(
        json.dumps({"domains": {"NEA": {"workstreams": {}}}, "aliases": {}}),
        encoding="utf-8",
    )
    v06 = _check_rca_06_project_taxonomy(tmp_path)
    assert {v["issue"] for v in v06} == {"MISSING_CATEGORIES", "MISSING_DOCIQ_PROJECT"}

    # RCA_07: Machine absolute path in runtime config
    synthetic_user_prefix = "/" + "Users" + "/testuser/local/cache"
    (tmp_path / "config" / "runtime" / "model_registry.json").write_text(
        json.dumps({"cache_dir": synthetic_user_prefix}), encoding="utf-8"
    )
    v07 = _check_rca_07_machine_paths(tmp_path)
    assert len(v07) == 1 and v07[0]["issue"] == "MACHINE_ABSOLUTE_PATH"

    # RCA_08: Gate count mismatch
    (tmp_path / "tools" / "readiness_core.py").write_text("# Gate 31\n", encoding="utf-8")
    (tmp_path / "config" / "governance" / "gate_manifest.json").write_text(
        json.dumps({"total_registered_stages": 30, "stages": list(range(30))}),
        encoding="utf-8",
    )
    v08 = _check_rca_08_gate_count(tmp_path)
    assert {v["issue"] for v in v08} == {
        "GATE_COUNT_MISMATCH",
        "GATE_MANIFEST_COUNT_MISMATCH",
    }

    all_v = check_rca_ssot_violations(tmp_path)
    assert len(all_v) >= 10


def test_attestation_storage_save_load_and_disjoint_qualification(tmp_path: Path) -> None:
    """Verifies get_attestation_path, save_readiness_attestation, load_attestation_by_tree, and check_readiness_qualification."""
    p_readiness = get_attestation_path(tmp_path, "tree123", READINESS_PREDICATE_TYPE)
    p_review = get_attestation_path(tmp_path, "tree123", PR_REVIEW_PREDICATE_TYPE)
    p_common = get_attestation_path(tmp_path, "tree123", "urn:custom:predicate")
    assert "readiness" in p_readiness.parts
    assert "pr_review" in p_review.parts
    assert "common" in p_common.parts

    with (
        patch(
            "tools.attestation_core.storage.compute_tree_digest",
            autospec=True,
            return_value="digest_abc123",
        ),
        patch(
            "tools.attestation_core.storage.compute_git_tree_oid",
            autospec=True,
            return_value="oid_main_999",
        ),
        patch(
            "tools.attestation_core.storage.compute_branch_delta_digest",
            autospec=True,
            return_value="delta_xyz789",
        ),
        patch(
            "tools.attestation_core.storage.subprocess.run",
            autospec=True,
            return_value=subprocess.CompletedProcess(
                args=["git"], returncode=0, stdout="", stderr=""
            ),
        ),
    ):
        saved_path = save_readiness_attestation(
            tmp_path,
            predicate={
                "branch": "feat/unit-test",
                "head_commit_sha": "sha_old",
                "base_commit_sha": "base_old",
                "terminal_outcome": "PASS",
            },
            tree_digest="digest_abc123",
            head_sha="sha_old",
        )
        assert saved_path.is_file()
        delta_file = get_attestation_path(
            tmp_path, "delta_delta_xyz789", READINESS_PREDICATE_TYPE
        )
        assert delta_file.is_file()

        loaded = load_attestation_by_tree(
            tmp_path,
            "digest_abc123",
            head_sha="sha_old",
            predicate_type=READINESS_PREDICATE_TYPE,
        )
        assert loaded is not None
        assert loaded["predicate"]["branch_delta_digest"] == "delta_xyz789"

        # Direct tree-digest qualification match
        ok, stmt, src = check_readiness_qualification(
            tmp_path, "feat/unit-test", "sha_old", "feat_unit_test"
        )
        assert ok is True
        assert stmt is not None
        assert "out-of-tree DSSE attestation" in src

    # Disjoint rebase delta qualification match when tree_digest changed after rebase
    with (
        patch(
            "tools.attestation_core.storage.compute_tree_digest",
            autospec=True,
            return_value="new_tree_digest_after_rebase",
        ),
        patch(
            "tools.attestation_core.storage.compute_branch_delta_digest",
            autospec=True,
            return_value="delta_xyz789",
        ),
        patch(
            "tools.attestation_core.storage._is_disjoint_rebase_equivalent",
            autospec=True,
            return_value=(True, "new_main_sha_777"),
        ),
        patch(
            "tools.attestation_core.storage.subprocess.run",
            autospec=True,
            return_value=subprocess.CompletedProcess(
                args=["git"], returncode=1, stdout="", stderr=""
            ),
        ),
    ):
        ok_reb, stmt_reb, src_reb = check_readiness_qualification(
            tmp_path, "feat/unit-test", "sha_new_rebased", "feat_unit_test"
        )
        assert ok_reb is True
        assert stmt_reb is not None
        assert stmt_reb["predicate"]["head_commit_sha"] == "sha_new_rebased"
        assert stmt_reb["predicate"]["base_commit_sha"] == "new_main_sha_777"
        assert "delta_" in src_reb


def test_l0_speaker_and_qualifier_policy_boundaries() -> None:
    """Verifies L0 speaker hygiene rules, nickname matching, defect classification, and qualifier sync."""
    assert normalize_candidate_claim_text(": **Downstream Production Integration:** **Jake Stark:**** Hello") == "**Jake Stark:** Hello"
    assert is_degenerate_single_token_claim("ESTABLISHED") is True
    assert is_degenerate_single_token_claim(" Jake Stark reviewed the transformer telemetry ") is False

    assert is_placeholder_speaker("Speaker 12") is True
    assert is_placeholder_speaker("person:jake-stark") is True
    assert is_placeholder_speaker("Forensic Transcript Participant Roster") is True
    assert is_placeholder_speaker("Jake Stark") is False
    assert clean_non_placeholder_speaker("  Speaker 1  ") is None
    assert clean_non_placeholder_speaker("  Jake Stark  ") == "Jake Stark"

    # Inline speaker extraction ignores concept headings
    assert extract_inline_turn_speaker("**Project Nexus:** Overview of milestones") is None
    assert extract_inline_turn_speaker("**Jake Stark:** We completed the LENS validation run") == "Jake Stark"

    # Nickname equivalence and Operator matching
    assert speakers_match_identity("Ed Smith", "Eduardo Smith (Apex Global)") is True
    assert speakers_match_identity("Amit Ayer", "Operator") is True
    assert speakers_match_identity("Jake Stark", "Marcus Vance") is False
    assert has_inline_speaker_contradiction("**Jake Stark:** We are ready", "Marcus Vance") is True
    assert has_inline_speaker_contradiction("**Ed Smith:** We are ready", "Eduardo Smith") is False

    # Deterministic speaker resolution priority
    assert (
        resolve_deterministic_speaker_or_author(
            raw_speaker="Speaker 1",
            scope_text="**Jake Stark:** Confirmed the SOW timeline.",
            subject_canonical_name="Marcus Vance",
            subject_node_type="person",
        )
        == "Jake Stark"
    )
    assert (
        resolve_deterministic_speaker_or_author(
            raw_speaker="Speaker 2",
            scope_text="Reviewed the substation transformer maintenance schedule.",
            subject_canonical_name="Marcus Vance",
            subject_node_type="stakeholder",
        )
        == "Marcus Vance"
    )

    # Synthetic and cross-project persona pollution
    assert (
        has_synthetic_or_cross_project_pollution(record_id="cnode_123")
        == "synthetic_cnode_prefix"
    )
    assert (
        has_synthetic_or_cross_project_pollution(
            scope_text="Thirty legacy platforms identified for consolidation"
        )
        == "synthetic_fixture_marker"
    )
    assert (
        has_synthetic_or_cross_project_pollution(
            subject_node_id="workstream_nexus",
            scope_text="Alex Rivera reviewed the deployment.",
        )
        == "cross_project_persona_contamination"
    )

    # Spoken dialogue vs structured profile card
    assert is_structured_profile_card_claim("Jake Stark: Role: Principal Architect") is True
    assert is_raw_spoken_dialogue("Yeah, I think we're ready to deploy next week.") is True
    assert is_spoken_meeting_source("dual_pipeline_intake", "Meeting Nexus Sync") is True
    assert is_spoken_meeting_source("dual_pipeline_intake", "Document Nexus Charter") is False

    # Defect classification
    assert (
        classify_speaker_attribution_defect(
            predicate="HAS_CLAIM_TEXT",
            scope_text=": Stripped colon text",
            speaker_or_author="Jake Stark",
        )
        == "stripped_empty_speaker_colon"
    )
    assert (
        classify_speaker_attribution_defect(
            predicate="HAS_CLAIM_TEXT",
            scope_text="Yeah, I think we should ship this now.",
            speaker_or_author=None,
            source_type="dual_pipeline_intake",
            source_name="Meeting LENS Weekly",
        )
        == "missing_meeting_speaker"
    )
    assert (
        classify_speaker_attribution_defect(
            predicate="HAS_CLAIM_TEXT",
            scope_text="Yeah, I think we should ship this now.",
            speaker_or_author="Speaker 4",
            source_type="dual_pipeline_intake",
            source_name="Meeting LENS Weekly",
        )
        == "placeholder_speaker_label"
    )

    # Qualifiers eligibility and sync
    assert has_non_promotable_qualifiers({"answer_use": "citation_only"}) is True
    assert has_non_promotable_qualifiers({"retrieval_kind": "evidence_span"}) is True
    stale_q = json.dumps(
        {
            "dual_pipeline_disposition": "CANDIDATE",
            "fact_promotion_eligible": False,
            "answer_use": "candidate_hypothesis",
            "dual_pipeline_candidate_id": "cand-1",
            "speaker_hygiene_quarantined": True,
        }
    )
    assert has_stale_candidate_qualifiers(stale_q) is True
    synced = parse_qualifiers_dict(sync_canonical_qualifiers_json(stale_q))
    assert synced["dual_pipeline_disposition"] == "APPROVED"
    assert synced["fact_promotion_eligible"] is True
    assert synced["answer_use"] == "factual_answer"
    assert "speaker_hygiene_quarantined" not in synced


def test_graph_post_entity_healer_discovery_and_signal_alignment() -> None:
    """Verifies heal_and_align_entities and align_canonical_signals for LENS and document modes."""
    mutations = {
        "entity_upserts": [
            {"id": "system:lens-backend", "type": "system", "name": "Banned"},
            {"id": "system:lens_rule_builder", "type": "system", "name": "Builder"},
            {"id": "system:maximo", "type": "system", "name": "Maximo"},
            {"id": "person:jake-stark", "type": "person", "name": "Jake Stark", "description": "attendee"},
            {"id": "org:apex_global_energy", "type": "org", "name": "Apex Global Enterprises"},
        ],
        "relationship_signals": [
            {"from_id": "system:lens_rule_builder", "to_id": "system:lens", "relationship": "USES"},
            {"from_id": "person:christian-moberg", "to_id": "org:partner_advisory", "relationship": "MEMBER_OF"},
            {"from_id": "person:jake-stark", "to_id": "role:lead-architect", "relationship": "MEMBER_OF"},
        ],
    }
    ev_index = {
        "ev-05": {"exact_text": "Maximo write-back is not ready for production readiness."},
        "ev-rule-builder": {"exact_text": "We use the rule builder to define rules."},
        "ev-01": {"exact_text": "Jake Stark leads the architecture."},
    }
    heal_and_align_entities(
        mutations,
        ev_index=ev_index,
        fallback_ev=["ev-01"],
        source_text="We discussed Confluence, ServiceNow, rule registry, and the 20.8 baseline ratio in NEA.",
        project="project_lens",
        is_document=False,
    )
    eids = {e["id"]: e for e in mutations["entity_upserts"]}
    assert "system:lens-backend" not in eids
    assert "system:lens-rule-builder" in eids
    assert "system:confluence" in eids
    assert "system:servicenow" in eids
    assert "metric:lens-baseline-ratio" in eids
    assert "org:apex_global-energy" in eids
    assert eids["system:maximo"]["attributes"]["receiving_readiness"] == "REPORTED_NOT_READY"
    assert "stakeholder" in eids["person:jake-stark"]["description"]

    align_canonical_signals(
        mutations,
        ev_index=ev_index,
        fallback_ev=["ev-01"],
        project="project_lens",
    )
    rels = mutations["relationship_signals"]
    # Christian Moberg MEMBER_OF banned edge must be filtered out
    assert not any("christian-moberg" in str(r.get("from_id")) for r in rels)
    # Jake Stark MEMBER_OF role:* converted to HAS_ROLE
    jake_rel = next(r for r in rels if r.get("from_id") == "person:jake-stark")
    assert jake_rel["relationship"] == "HAS_ROLE"
    # Proposal decoupling acceptance from accuracy injected
    assert any(
        p.get("proposal_id") == "prop-decouple-acceptance-from-accuracy"
        for p in mutations.get("proposal_updates", [])
    )
