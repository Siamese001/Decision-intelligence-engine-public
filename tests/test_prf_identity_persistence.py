"""Tests for PRF identity binding, company affiliation, and scoped human corrections.

Verifies Wave 3 acceptance criteria:
1. Noemi Monnerville is PartnerAdvisory across roster, entity resolution, and deliverables;
   her job title does not determine her company.
2. Venkat receives full name only from verified evidence; missing identity stays
   visibly incomplete as a candidate; surname guessing and false substitution of
   Krishnakumar Venkataraman are strictly prevented.
3. Acoustic cluster number/order is never treated as roster position.
4. Attendance, role, employer, cluster identity, dominant-speaker share, and
   individual utterance approvals have separate signed scopes.
5. One coherent review decision can cover a consistent cluster while preserving
   exceptions, 'cannot_tell', and undo.
6. Restart and persistence from identity corrections reference record.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.speaker_attribution_core.review_units import (
    VALID_DECISION_SCOPES,
    ReviewDecision,
    ReviewItem,
    ReviewUnitManager,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_company_for_person,
    resolve_person_entity,
)
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    build_roster_from_attendees,
    clean_attendee_entry,
    format_company_grouped_attendees,
)
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
ID_CORRECTIONS_PATH = REPO_ROOT / "data/reference/prf_20261006_identity_corrections.json"


def test_noemi_monnerville_partner_advisory_affiliation() -> None:
    """Acceptance Criterion 1: Noemi is PartnerAdvisory across roster, resolver, and deliverables."""
    # 1. Entity resolver check
    resolver = PreSynthesisEntityResolver(
        repo_root=REPO_ROOT,
        identity_corrections_path=ID_CORRECTIONS_PATH,
    )
    ent = resolver.resolve("Noemi Monnerville")
    assert ent is not None
    assert ent.canonical_name == "Noemi Monnerville"
    assert ent.org == "PartnerAdvisory"
    assert ent.role == "Web User Experience Designer"
    assert ent.source in ("authoritative_correction", "directory_prior")

    # 2. Company resolver check
    company = resolve_company_for_person("Noemi Monnerville")
    assert company == "PartnerAdvisory"

    # 3. Clean attendee entry with Contractor header
    c_name, c_role, c_org = clean_attendee_entry("Contractor: Noemi Monnerville (Web User Experience Designer)")
    assert c_name == "Noemi Monnerville"
    assert c_org == "PartnerAdvisory"
    assert c_role == "Web User Experience Designer"

    # 4. Roster building from dict with Contractor header
    raw_attendees = {
        "Contractor": ["Noemi Monnerville (Web User Experience Designer)"],
        "PartnerAdvisory": ["Amit Ayer (Principal Architect & Technology Strategist)"],
    }
    roster = build_roster_from_attendees(raw_attendees)
    noemi_roster = next((r for r in roster if r["name"] == "Noemi Monnerville"), None)
    assert noemi_roster is not None
    assert noemi_roster["org"] == "PartnerAdvisory"
    assert noemi_roster["role"] == "Web User Experience Designer"


def test_venkat_candidate_preservation_and_anti_guessing() -> None:
    """Acceptance Criterion 2: Venkat receives full name only from verified evidence.

    Missing identity stays visibly incomplete; never guess a surname or substitute
    Krishnakumar Venkataraman.
    """
    resolver = PreSynthesisEntityResolver(
        repo_root=REPO_ROOT,
        identity_corrections_path=ID_CORRECTIONS_PATH,
    )

    # 1. Single token "Venkat" lookup
    ent = resolver.resolve("Venkat")
    assert ent is not None
    # Canonical name must remain "Venkat" without guessed surname
    assert ent.canonical_name == "Venkat"
    assert ent.org == "PartnerAdvisory"
    assert ent.status == "UNRESOLVED_CANDIDATE_ONLY"
    assert ent.candidate_name == "Venkat Puppala"
    assert ent.requires_operator_confirmation is True

    # 2. Never substitute Krishnakumar Venkataraman for "Venkat"
    assert ent.canonical_name != "Krishnakumar Venkataraman"
    assert "Venkataraman" not in ent.canonical_name

    # 3. Distinct lookup for Krishnakumar Venkataraman
    kk_ent = resolver.resolve("Krishnakumar Venkataraman")
    assert kk_ent is not None
    assert kk_ent.canonical_name == "Krishnakumar Venkataraman"
    assert kk_ent.org == "Apex Global Enterprises (PGD)"
    assert kk_ent != ent


def test_cluster_order_decoupled_from_roster_position() -> None:
    """Acceptance Criterion 3: Acoustic cluster order is never treated as roster position."""
    utterances = [
        {"utterance_id": "utt_1", "speaker": "SPEAKER_00", "start_time_sec": 1.0, "end_time_sec": 2.0, "text": "Hi."},
        {"utterance_id": "utt_2", "speaker": "SPEAKER_01", "start_time_sec": 2.5, "end_time_sec": 4.0, "text": "Hello."},
    ]

    # Order A
    attendees_a = {"Clement", "Amit Ayer", "Venkat"}
    mgr_a = ReviewUnitManager(confirmed_attendees=attendees_a)
    items_a = mgr_a.generate_review_items(utterances)

    # Order B (reversed / permuted)
    attendees_b = {"Venkat", "Amit Ayer", "Clement"}
    mgr_b = ReviewUnitManager(confirmed_attendees=attendees_b)
    items_b = mgr_b.generate_review_items(utterances)

    # Review item cluster IDs and assigned speakers must remain strictly pending and acoustic-bound
    assert len(items_a) == len(items_b) == 2
    for it_a, it_b in zip(items_a, items_b):
        assert it_a.review_id == it_b.review_id
        assert it_a.item_type == it_b.item_type == "homogeneous_cluster"
        # Neither cluster may be auto-assigned to Clement or Venkat based on roster order
        assert it_a.assigned_speaker is None
        assert it_b.assigned_speaker is None
        assert it_a.resolution_status == "pending"
        assert it_b.resolution_status == "pending"


def test_separate_signed_decision_scopes() -> None:
    """Acceptance Criterion 4: 6 distinct signed decision scopes are enforced."""
    expected_scopes = {
        "attendance",
        "role",
        "employer",
        "cluster_identity",
        "dominant_speaker_share",
        "individual_utterance",
    }
    assert expected_scopes == VALID_DECISION_SCOPES

    # Valid scopes instantiate cleanly
    for scope in expected_scopes:
        dec = ReviewDecision(
            decision_id=f"dec_{scope}",
            review_id="rev_001",
            action="confirm_all",
            assigned_speaker="Amit Ayer",
            scope_utterance_ids=["utt_1"],
            scope_word_ids=["w_1"],
            decision_scope=scope,
            signature="ed25519:test_sig",
            recorded_at="2026-10-08T10:00:00Z",
        )
        assert dec.decision_scope == scope

    # Invalid scope fails fast
    with pytest.raises(ValueError, match="Invalid decision scope 'global_blanket'"):
        ReviewDecision(
            decision_id="dec_invalid",
            review_id="rev_001",
            action="confirm_all",
            assigned_speaker="Amit Ayer",
            scope_utterance_ids=["utt_1"],
            scope_word_ids=["w_1"],
            decision_scope="global_blanket",
        )

    # Autonomous bypass without signature fails fast
    with pytest.raises(ValueError, match="Autonomous bypass decisions without valid signature are barred"):
        ReviewDecision(
            decision_id="dec_bypass",
            review_id="rev_001",
            action="confirm_all",
            assigned_speaker="Amit Ayer",
            scope_utterance_ids=["utt_1"],
            scope_word_ids=["w_1"],
            operator_origin="autonomous_bypass",
            signature=None,
        )


def test_grouped_review_decision_actions_and_undo() -> None:
    """Acceptance Criterion 5: Coherent review decisions support confirm, exceptions, cannot_tell, undo."""
    utterances = [
        {"utterance_id": "u_1", "speaker": "SPEAKER_00", "start_time_sec": 0.0, "end_time_sec": 3.0, "text": "Opening remarks."},
        {"utterance_id": "u_2", "speaker": "SPEAKER_00", "start_time_sec": 3.1, "end_time_sec": 6.0, "text": "Second sentence."},
    ]
    mgr = ReviewUnitManager(confirmed_attendees={"Amit Ayer", "Venkat"})
    items = mgr.generate_review_items(utterances)
    assert len(items) == 1
    item = items[0]

    # 1. Apply confirm_all
    dec_confirm = ReviewDecision(
        decision_id="d_conf",
        review_id=item.review_id,
        action="confirm_all",
        assigned_speaker="Amit Ayer",
        scope_utterance_ids=["u_1", "u_2"],
        scope_word_ids=[],
        decision_scope="cluster_identity",
        context_digest=item.context_digest,
        previous_state={"u_1": "SPEAKER_00", "u_2": "SPEAKER_00"},
    )
    res = mgr.apply_decision(dec_confirm, items, utterances)
    assert res["resolution_status"] == "resolved"
    assert utterances[0]["speaker"] == "Amit Ayer"
    assert utterances[1]["speaker"] == "Amit Ayer"

    # 2. Undo decision
    dec_undo = ReviewDecision(
        decision_id="d_undo",
        review_id=item.review_id,
        action="undo",
        assigned_speaker=None,
        scope_utterance_ids=["u_1", "u_2"],
        scope_word_ids=[],
        decision_scope="cluster_identity",
        context_digest=item.context_digest,
        previous_state={"u_1": "SPEAKER_00", "u_2": "SPEAKER_00"},
    )
    res_undo = mgr.apply_decision(dec_undo, items, utterances)
    assert res_undo["resolution_status"] == "pending"
    assert utterances[0]["speaker"] == "SPEAKER_00"
    assert utterances[1]["speaker"] == "SPEAKER_00"

    # 3. Cannot tell
    dec_ct = ReviewDecision(
        decision_id="d_ct",
        review_id=item.review_id,
        action="cannot_tell",
        assigned_speaker=None,
        scope_utterance_ids=["u_1", "u_2"],
        scope_word_ids=[],
        decision_scope="cluster_identity",
        context_digest=item.context_digest,
    )
    res_ct = mgr.apply_decision(dec_ct, items, utterances)
    assert res_ct["resolution_status"] == "cannot_tell"


def test_multi_cluster_linking_requires_rationale() -> None:
    """Acceptance Criterion 3b: Multiple clusters per person require supported linking."""
    item_1 = ReviewItem(
        review_id="rev_c01",
        item_type="homogeneous_cluster",
        target_utterance_ids=["u1"],
        context_start_sec=0.0,
        context_end_sec=5.0,
        candidate_speakers=["Amit Ayer"],
    )
    item_2 = ReviewItem(
        review_id="rev_c02",
        item_type="homogeneous_cluster",
        target_utterance_ids=["u2"],
        context_start_sec=10.0,
        context_end_sec=15.0,
        candidate_speakers=["Amit Ayer"],
    )

    # Empty rationale fails
    with pytest.raises(ValueError, match="requires explicit supporting rationale"):
        ReviewUnitManager.link_clusters(item_1, item_2, rationale="")

    # Valid rationale links both items bidirectionally
    ReviewUnitManager.link_clusters(
        item_1,
        item_2,
        rationale="Acoustic pitch and formant continuity match Amit Ayer across microphone transition.",
    )
    assert "rev_c02" in item_1.linked_cluster_ids
    assert "rev_c01" in item_2.linked_cluster_ids


def test_identity_corrections_file_persistence() -> None:
    """Acceptance Criterion 6: Identity corrections persist deterministically from file."""
    assert ID_CORRECTIONS_PATH.is_file()
    with open(ID_CORRECTIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("contract_version") == "1.0.0"
    corrections = data.get("corrections", [])
    assert len(corrections) >= 2

    # Check Noemi entry
    noemi = next((c for c in corrections if c["entity_id"] == "noemi_monnerville"), None)
    assert noemi is not None
    assert noemi["corrected_affiliation"] == "PartnerAdvisory"
    assert noemi["role"] == "Web User Experience Designer"

    # Check Venkat candidate entry
    venkat = next((c for c in corrections if c["entity_id"] == "venkat_candidate"), None)
    assert venkat is not None
    assert venkat["candidate_canonical_name"] == "Venkat Puppala"
    assert venkat["status"] == "UNRESOLVED_CANDIDATE_ONLY"
    assert "DO_NOT_INVENT_OR_GUESS_SURNAME" in venkat["prohibited_actions"]
    assert "DO_NOT_SUBSTITUTE_KRISHNAKUMAR_VENKATARAMAN" in venkat["prohibited_actions"]
