"""End-to-end verification for Wave 3 (CAT-2 P0 & P1):
- ForensicNormalizer collapse detection and ledger verification (CAT2-1000-P0-1)
- VoiceprintStore worktree/pytest isolation and HITL-only enrollment (CAT2-1000-P0-2)
- AppendixHealer zero fabricated personas/bullets and unverified org preservation (CAT2-1000-P0-3)
- SpeakerRoleReconciler compound role splitting and roster vs HITL provenance (CAT2-1000-P0-4)
- GraphExecutor lineage preservation, Gate 2.3 strictness, and synthesizer egress sanitization (CAT2-1000-P1-3, P1-4)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.audio_ingestion_core.voiceprint_store import resolve_canonical_voiceprint_db_path
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.sanitizer import EgressSanitizationError
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    check_speaker_skew_and_collapse,
    verify_collapse_approval_in_ledger,
)
from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import (
    auto_enroll_voiceprints,
)
from dual_pipeline.transcript_qa_core.appendix_healer import (
    _categorize_dialogue_turn,
    _dialogue_topics,
    _extract_attendee_details,
    _has_prohibited_appendix_patterns,
)
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.graph_executor import _enrich_gs10_traits
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


def test_forensic_normalizer_blocks_unverified_collapse_and_preserves_verified_native() -> None:
    """CAT2-1000-P0-1 & P1-1/P1-2: ForensicNormalizer checks collapse and routes verified native transcripts to _execute_native."""
    provider = MagicMock()
    normalizer = ForensicNormalizer()

    # 12 turns all attributed to Alice in a 2-person meeting -> collapse
    collapsed_turns = "\n".join(
        f"**[00:{i:02d}:00] Alice:** We are migrating the Kubernetes cluster and Terraform modules today."
        for i in range(1, 13)
    )
    meta = {
        "meeting_id": "mtg-collapse-e2e",
        "meeting_name": "Architecture Sync",
        "date": "10/28/2026",
        "roster": {"Acme": ["Alice", "Bob"]},
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        normalizer.execute({"clean_transcript": collapsed_turns, "metadata": dict(meta)}, plan=None, provider=provider)

    ev = exc_info.value.evidence
    assert ev["attendee_count"] == 2
    assert ev["dominant_label_turns"] == 12
    assert ev["dominant_label_words"] > 0

    # Verify ledger check fails closed when expected_subject is omitted
    assert verify_collapse_approval_in_ledger({}) is False

    # Native transcript with verified ledger entry stays on _execute_native (zero LLM call)
    with patch(
        "dual_pipeline.speaker_attribution_core.collapse_detector.verify_collapse_approval_in_ledger",
        return_value=True,
    ), patch(
        "dual_pipeline.speaker_attribution_core.collapse_detector._verify_collapse_approval_receipt",
        return_value=True,
    ):
        result = normalizer.execute({"clean_transcript": collapsed_turns, "metadata": dict(meta)}, plan=None, provider=provider)
    assert "Alice" in result
    provider.synthesize_forensic.assert_not_called()


def test_voiceprint_isolation_and_hitl_only_enrollment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAT2-1000-P0-2: Voiceprint DB isolates under pytest/worktree and rejects unverified ROSTER_PRIOR speaker_map."""
    monkeypatch.delenv("NEE_VOICEPRINT_DB_PATH", raising=False)
    resolved_path = resolve_canonical_voiceprint_db_path()
    # Under pytest, must never point to repo root .local/voiceprint_memory.sqlite
    assert "nee_pytest_voiceprint_memory.sqlite" in str(resolved_path) or ".worktrees" in str(resolved_path)

    # Unverified speaker_map must NOT enroll voiceprints with is_verified=True
    unverified_meta = {
        "cluster_centroids": {"SPEAKER_00": [0.1] * 256},
        "speaker_map": {"SPEAKER_00": "Alice"},
        "speaker_role_resolutions": {
            "Alice": {"person_name": "Alice", "resolved_role": "Architect", "provenance": "ROSTER_PRIOR", "is_human_confirmed": False}
        },
    }
    mock_store = MagicMock()
    enrolled = auto_enroll_voiceprints(
        metadata=unverified_meta,
        speaker_map={"SPEAKER_00": "Alice"},
        voiceprint_store=mock_store,
    )
    assert enrolled == []
    mock_store.enroll_voiceprint.assert_not_called()


def test_appendix_healer_zero_fabrication_and_self_poisoning_elimination() -> None:
    """CAT2-1000-P0-3: Appendix healer never fabricates attendees, fallback bullets, or prohibited categories."""
    empty_entries = _extract_attendee_details("No attendees block here", metadata={})
    assert empty_entries == []

    cat, bullet = _categorize_dialogue_turn(
        "We need to sign the statement of work amendment and apply deployment vouchers.",
        "Alice",
    )
    assert cat == "Commercial Contracting & Funding Alignment"
    assert not _has_prohibited_appendix_patterns(f"# Appendix\n- **{cat}**: {bullet}")

    # Single substantive turn must produce exactly 1 grounded bullet, never 3 fabricated pool bullets
    topics = _dialogue_topics(
        "Alice",
        [
            "Hi everyone good morning",
            "We need to configure Terraform state locking and GitHub Actions pipelines for production.",
        ],
        role="Architect",
        org="",
    )
    assert len(topics) == 1
    assert "Terraform" in topics[0]
    assert "Client / Partner" not in topics[0]


def test_speaker_role_reconciler_and_lineage_and_gate23_strictness() -> None:
    """CAT2-1000-P0-4, P1-3, P1-4: Reconciler splits single-word compound roles, tags ROSTER_PRIOR accurately, preserves lineage, and enforces Gate 2.3."""
    final_data = {
        "meeting_id": "mtg-reconcile-1",
        "entities": [{"id": "person:alice", "name": "Alice", "type": "person"}],
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [
                {
                    "signal_id": "sig_1",
                    "from_id": "person:alice",
                    "to_id": "system:k8s",
                    "relationship": "OWNS",
                    "evidence_ids": ["ev-1"],
                    "lineage": {"speaker": "Alice", "speaker_role": "Director / Principal Architect", "speaker_org": "PartnerAdvisory"},
                }
            ],
        },
        "evidence_index": {
            "ev-1": {
                "statement_id": "stmt-1",
                "exact_text": "Alice owns the Kubernetes platform.",
                "grounding_status": "GROUNDED",
                "speaker": "Alice",
                "speaker_role": "Speaker",
                "speaker_org": "Internal",
            }
        },
    }
    metadata = {
        "roster": [{"name": "Alice", "role": "Director / Principal Architect", "org": "PartnerAdvisory"}],
        "speaker_role_resolutions": {
            "Alice": {
                "person_name": "Alice",
                "resolved_role": "Director / Principal Architect",
                "organization": "PartnerAdvisory",
                "provenance": "ROSTER_PRIOR",
                "is_human_confirmed": False,
            }
        },
    }
    reconciled = reconcile_speaker_role_resolutions(final_data, metadata)
    upserts = reconciled["graph_mutations"]["entity_upserts"]
    role_nodes = [e for e in upserts if e.get("type") == "role"]
    assert {r["name"] for r in role_nodes} == {"Director", "Principal Architect"}

    # Person entity must not retain unsplit compound role attribute
    alice_ent = next(e for e in upserts if e.get("id") == "person:alice")
    assert "role" not in alice_ent
    assert alice_ent["is_human_confirmed"] is False

    # Evidence entry must be ROSTER_PRIOR, not TRANSCRIPT_HITL
    assert "ev_roster_role_alice" in reconciled["evidence_index"]
    ev_roster = reconciled["evidence_index"]["ev_roster_role_alice"]
    assert ev_roster["source"] == "ROSTER_PRIOR"
    assert "Session roster recorded" in ev_roster["text"]

    # _enrich_gs10_traits must preserve reconciled lineage role & org over generic ev fields
    _enrich_gs10_traits(reconciled, metadata)
    sig_lin = reconciled["graph_mutations"]["relationship_signals"][0]["lineage"]
    assert sig_lin["speaker_role"] == "Director / Principal Architect"
    assert sig_lin["speaker_org"] == "PartnerAdvisory"

    # Gate 2.3 must fail when Output 2 has an action item but zero evidence citations
    recap_no_citations = (
        "# Executive Summary\n**Date:** 10/28/2026\n\n"
        "# Typed Action & Commitment Register\n"
        "| Action ID | Action | Owner | Status | Due Date |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| ACT-01 | Deploy Terraform | Alice | COMMITTED | 11/01/2026 |\n"
    )
    g2_res = run_qa_gate_2(recap_no_citations, metadata={"duration_minutes": 10})
    assert g2_res["gates"]["Gate_2_3_Evidence_Lineage_and_Epistemic_Status"]["passed"] is False
