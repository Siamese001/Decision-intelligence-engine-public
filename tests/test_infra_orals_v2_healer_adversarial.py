"""
tests/test_infra_orals_v2_healer_adversarial.py

Adversarial and boundary test suite verifying Wave 2 healer de-poisoning,
GraphDB SSOT protection against str(meta) contamination, endpoint typing,
terminal candidate rollback persistence, and UWG auth token forwarding.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.uwg_deployer import build_publish_args
from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_qa_core.forensic_healer import heal_output_1_forensic
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_qa_core.recap_key_discussion_healer import heal_key_discussion_anchors
from dual_pipeline.transcript_stages_core.graph_post_extractor import sanitize_and_validate_projection
from dual_pipeline.transcript_stages_core.graph_post_roster import ensure_roster_attendees


# ==============================================================================
# Category 1: Exact Production Regression (Drew Oakley vs "Conduit" poisoning)
# ==============================================================================

def test_production_regression_conduit_mention_never_corrupts_dan_okray():
    """Verify that mentioning 'the conduit team' or conduit metadata never rewrites Drew Oakley to Dan Okay."""
    adversarial_transcript = (
        "# Forensic Transcript\n\n"
        "**Drew Oakley:** [00:01:10] Good morning everyone, we are presenting the infrastructure orals.\n\n"
        "**Jason Nicholas:** [00:11:00] We collaborated with the conduit team on several integration patterns.\n\n"
        "**Drew Oakley:** [00:11:25] That is correct, and our delivery model remains accountable.\n"
    )
    poisoned_metadata = {
        "project": "conduit_project",
        "meeting_name": "Infrastructure Orals Conduit Review",
        "attendees": ["Drew Oakley (PartnerAdvisory / Client Service Lead)", "Jason Nicholas (PartnerAdvisory / Solution Architect)"],
    }

    healed_f1 = heal_output_1_forensic(adversarial_transcript, metadata=poisoned_metadata)
    assert "Dan Okay" not in healed_f1
    assert "**Drew Oakley:**" in healed_f1
    assert healed_f1.count("**Drew Oakley:**") == 2


# ==============================================================================
# Category 2: Invariant Bypass Attempt (str(meta) poisoning & synthetic injection)
# ==============================================================================

def test_poisoned_metadata_cannot_inject_synthetic_recap_expansions():
    """Verify that adversarial metadata with 'conduit', '08-27-2026', and 'pd_conduit' cannot inject synthetic text."""
    clean_recap = (
        "# Executive Recap\n\n"
        "## Key Discussion Points\n\n"
        "- **Modular infrastructure architecture:** The team presented modular Terraform patterns. [00:05:00]\n\n"
        "## Attendance & Persona Index\n\n"
        "# Drew Oakley — PartnerAdvisory, Client Service Lead\n"
        "- **Delivery Leadership**: Accountable for delivery team structure.\n"
    )
    poisoned_meta = {
        "meeting_name": "pd_conduit_catch_up_08-27-2026",
        "project": "conduit",
        "date": "08-27-2026",
    }

    healed_f2 = heal_output_2_recap(clean_recap, metadata=poisoned_meta)
    # Ensure synthetic hardcoded paragraphs are NEVER injected
    assert "Jay Lenner details broad ownership" not in healed_f2
    assert "Carlos Salazar confirms he will contact Tyler McCord" not in healed_f2
    assert "Cameron Mercer" not in healed_f2


def test_healing_mesh_domain_ontology_never_overwrites_with_synthetic_triples():
    """Verify that NormalizationMesh does not overwrite graph mutations based on str(data) matches."""
    f3_payload = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:dan_okray", "type": "person", "name": "Drew Oakley", "description": "CSL at PartnerAdvisory."}
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "risk_updates": [],
        }
    }
    changes = []
    DeterministicHealer._apply_domain_ontology_healers(f3_payload, {"project": "jarvis_charter"}, changes)
    assert len(changes) == 0
    assert len(f3_payload["graph_mutations"]["entity_upserts"]) == 1
    assert f3_payload["graph_mutations"]["entity_upserts"][0]["id"] == "person:dan_okray"


# ==============================================================================
# Category 3: Boundary Payload (MOLE-VC Endpoint Typing & Empty Project Defaults)
# ==============================================================================

def test_conference_room_endpoint_typed_as_system_not_person():
    """Verify conference room endpoints (MOLE-VC, NEA-MOLE-VC) are typed as system/location, not person."""
    mutations: Dict[str, Any] = {"entity_upserts": [], "relationship_signals": []}
    metadata = {
        "attendees": [
            "Drew Oakley (PartnerAdvisory)",
            "MOLE-VC (Conference Room Endpoint)",
            "NEA-MOLE-VC",
        ]
    }
    ensure_roster_attendees(mutations, metadata)

    entities_by_id = {e["id"]: e for e in mutations["entity_upserts"]}
    assert "person:dan_okray" in entities_by_id
    assert entities_by_id["person:dan_okray"]["type"] == "person"

    # Endpoints MUST NOT be typed as person
    for ep_id in ["system:mole-vc", "system:nea-mole-vc"]:
        assert ep_id in entities_by_id
        assert entities_by_id[ep_id]["type"] == "system"


def test_empty_project_string_never_defaults_to_project_lens():
    """Verify that an empty or missing project string in metadata does not default to is_lens=True."""
    raw_payload = {
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
        }
    }
    sanitized = sanitize_and_validate_projection(raw_payload, metadata={"project": ""})
    # Should not inject system:lens when project is empty
    upserts = sanitized.get("graph_mutations", {}).get("entity_upserts", [])
    assert not any(e.get("id") == "system:lens" for e in upserts)


# ==============================================================================
# Category 4: Rollback & Recovery (Terminal Remediation State Machine Persistence)
# ==============================================================================

def test_terminal_attempt_rollback_persists_to_disk(tmp_path: Path):
    """Verify that when a stage fails on the terminal attempt, the best candidate is restored and written to disk."""
    ckpt_dir = tmp_path / "checkpoints"
    ckpt_dir.mkdir()

    sm = SelectiveRemediationStateMachine(max_attempts=2, checkpoint_dir=ckpt_dir)

    # Attempt 1: Output 2 passes with score 92
    c2_att1 = "# Executive Recap\n\nHigh scoring candidate text from Attempt 1."
    sm.evaluate_attempt(
        artifacts={"output_2": c2_att1},
        qa_report={"output_02_executive_recap": {"passed": True}},
        judge_report={"output_2_evaluation": {"composite_score": 92, "verdict": "PASS"}},
        final_decision={"status": "FAIL", "output_2_deterministic_gates_pass": True},
    )
    assert sm.get_best_candidate_content("output_2") == c2_att1

    # Attempt 2 (Terminal): Output 2 regresses to score 70 and fails
    c2_att2 = "# Executive Recap\n\nRegressed candidate text from Attempt 2."
    c2_file = tmp_path / "02_executive_recap.md"
    c2_file.write_text(c2_att2, encoding="utf-8")

    artifacts = {"output_2": c2_att2, str(c2_file): c2_att2}
    sm.advance_attempt()
    all_passed, failed = sm.evaluate_attempt(
        artifacts=artifacts,
        qa_report={"output_02_executive_recap": {"passed": False}},
        judge_report={"output_2_evaluation": {"composite_score": 70, "verdict": "FAIL"}},
        final_decision={"status": "FAIL", "output_2_deterministic_gates_pass": False},
    )

    assert not all_passed
    assert "output_1" in failed
    # Rollback must restore Attempt 1 content both in memory and on disk
    assert artifacts["output_2"] == c2_att1
    assert c2_file.read_text(encoding="utf-8") == c2_att1
    # Checkpoint seal file must exist and reflect Attempt 1 content
    seal_file = ckpt_dir / "output_2.ckpt.md"
    assert seal_file.is_file()
    assert seal_file.read_text(encoding="utf-8") == c2_att1


# ==============================================================================
# Category 5: Invariant Bypass Attempt (UWG Auth Token Forwarding)
# ==============================================================================

def test_uwg_deployer_forwards_auth_token_from_metadata(tmp_path: Path):
    """Verify build_publish_args forwards auth_token from metadata and approval_receipt."""
    package_path = tmp_path / "package"
    package_path.mkdir()

    meta_with_token = {
        "auth_token": "cryptographic_auth_token_12345",
        "auth_public_key": "pubkey_abc",
        "auth_issuer": "operator_admin",
        "auth_audience": "nee-engineering",
    }

    args = build_publish_args(
        package_path=package_path,
        dir_id="dir-test-1",
        meeting_disp="Test Meeting",
        reviewer="operator",
        rationale="Ingest Test Meeting",
        date_disp="10-18-2026",
        project_disp="Apex Global",
        embedding_backend="bge-m3-dev",
        metadata=meta_with_token,
        canon_runtime_root=tmp_path,
        root_path=tmp_path,
        resolve_model_asset_fn=lambda p, env: tmp_path / p,
    )

    assert args.auth_token == "cryptographic_auth_token_12345"
    assert args.auth_public_key == "pubkey_abc"
    assert args.auth_issuer == "operator_admin"
    assert args.auth_audience == "nee-engineering"
