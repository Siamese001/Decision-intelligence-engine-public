"""Adversarial and regression tests for Wave 2: Roster Organization Disambiguation & Entity Resolution Graph Binding."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.c0_pa_runner import step_c0_pa_with_resume
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.transcript_qa_core.graph_reference_validator import _check_conflicting_entities
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.graph_post_extractor import sanitize_and_validate_projection


def test_run3_roster_attendees_do_not_inject_role_partner_advisory() -> None:
    """Exact production regression (Run #3): 'Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)' must not emit role:partner_advisory."""
    raw_projection = {
        "schema_version": "5.7",
        "meeting_id": "mtg-09-30-2026-bailey-amit",
        "meeting_date": "09/30/2026",
        "project": "project_nexus",
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "speaker": "Bailey Morton",
                "exact_text": "We are scoping DocIQ Q4 deliverables with PartnerAdvisory and Apex Global.",
                "locator": {"type": "timestamp", "value": "00:01:10"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "org:partner_advisory",
                    "type": "org",
                    "name": "PartnerAdvisory",
                    "description": "Consulting partner organization.",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "person:bailey-morton",
                    "type": "person",
                    "name": "Bailey Morton",
                    "description": "DocIQ Product Owner at PartnerAdvisory.",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "person:amit-ayer",
                    "type": "person",
                    "name": "Amit Ayer",
                    "description": "AI Architect at PartnerAdvisory.",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-01"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel-bailey--partner_advisory--MEMBER_OF",
                    "from_id": "person:bailey-morton",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "predicate": "MEMBER_OF",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "signal_id": "rel-amit--partner_advisory--MEMBER_OF",
                    "from_id": "person:amit-ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "predicate": "MEMBER_OF",
                    "evidence_ids": ["ev-01"],
                },
            ],
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
            "action_updates": [],
        },
    }
    metadata = {
        "meeting_name": "Dociq_Q4_Scope_Bailey_Amit",
        "date": "09/30/2026",
        "project": "project_nexus",
        "attendees": "Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)",
    }

    sanitized = sanitize_and_validate_projection(raw_projection, metadata=metadata, source_text="Bailey Morton: We are scoping DocIQ Q4 deliverables with PartnerAdvisory and Apex Global.")
    entities = sanitized["graph_mutations"]["entity_upserts"]
    entity_ids = {e["id"] for e in entities}
    assert "org:partner_advisory" in entity_ids
    assert "role:partner_advisory" not in entity_ids

    failures: list[str] = []
    _check_conflicting_entities(sanitized, failures)
    assert failures == []


def test_healer_and_entity_resolutions_prune_preexisting_conflicting_role_entities() -> None:
    """Verify DeterministicHealer and entity_resolutions prune role:partner_advisory / custom org roles and rebind HAS_ROLE."""
    poisoned_projection = {
        "schema_version": "5.7",
        "meeting_id": "mtg-09-30-2026-bailey-amit",
        "meeting_date": "09/30/2026",
        "project": "project_nexus",
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "speaker": "Bailey Morton",
                "exact_text": "PartnerAdvisory and Acme Corp are partnering on DocIQ.",
                "locator": {"type": "timestamp", "value": "00:01:10"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "description": "Consulting partner.", "evidence_ids": ["ev-01"]},
                {"id": "role:partner_advisory", "type": "role", "name": "PartnerAdvisory", "description": "Professional role: PartnerAdvisory.", "evidence_ids": ["ev-01"]},
                {"id": "role:acme_corp", "type": "role", "name": "Acme Corp", "description": "Professional role: Acme Corp.", "evidence_ids": ["ev-01"]},
                {"id": "org:acme-corp", "type": "org", "name": "Acme Corp", "description": "Partner org.", "evidence_ids": ["ev-01"]},
                {"id": "person:bailey-morton", "type": "person", "name": "Bailey Morton", "description": "Lead.", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "fact-rel-3--person-bailey-morton--role-partner_advisory--HAS_ROLE",
                    "from_id": "person:bailey-morton",
                    "to_id": "role:partner_advisory",
                    "relationship": "HAS_ROLE",
                    "predicate": "HAS_ROLE",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "signal_id": "fact-rel-3--person-bailey-morton--role-acme-corp--HAS_ROLE",
                    "from_id": "person:bailey-morton",
                    "to_id": "role:acme_corp",
                    "relationship": "HAS_ROLE",
                    "predicate": "HAS_ROLE",
                    "evidence_ids": ["ev-01"],
                },
            ],
        },
    }
    meta = {
        "entity_resolutions": {"PartnerAdvisory": "org:partner_advisory", "Acme Corp": "org:acme-corp"},
        "date": "09/30/2026",
    }
    healed_str, _changes = DeterministicHealer.heal_output_3(
        poisoned_projection,
        metadata=meta,
        output_1="[00:01:10] Bailey Morton: PartnerAdvisory and Acme Corp are partnering on DocIQ.",
    )
    healed = json.loads(healed_str)
    ents = healed["graph_mutations"]["entity_upserts"]
    eids = {e["id"] for e in ents}
    assert "role:partner_advisory" not in eids
    assert "role:acme_corp" not in eids
    assert "org:partner_advisory" in eids
    assert "org:acme-corp" in eids

    failures: list[str] = []
    _check_conflicting_entities(healed, failures)
    assert failures == []


def test_compound_parenthetical_org_and_role_extraction_and_c0_pa_resume_sync(tmp_path: Path) -> None:
    """Verify (Org, Role) and (Role, Org) extract only Role as role:* and step_c0_pa_with_resume syncs entity_resolutions."""
    projection = {
        "schema_version": "5.7",
        "meeting_date": "09/30/2026",
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "speaker": "Bailey Morton",
                "exact_text": "Discussing scope.",
                "locator": {"type": "timestamp", "value": "00:00:10"},
            }
        },
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
    }
    meta = {
        "attendees": "Bailey Morton (PartnerAdvisory, Product Owner), Amit Ayer (Principal Architect, PartnerAdvisory), ()",
        "date": "09/30/2026",
    }
    res = sanitize_and_validate_projection(projection, metadata=meta, source_text="Bailey Morton: Discussing scope.")
    eids = {e["id"] for e in res["graph_mutations"]["entity_upserts"]}
    assert "role:product_owner" in eids
    assert "role:principal_architect" in eids
    assert "role:partner_advisory" not in eids
    assert "org:partner_advisory" in eids

    # Verify step_c0_pa_with_resume syncs entity_resolutions into cached pa_context["metadata"]
    checkpoint = MagicMock()
    checkpoint.has_stage.return_value = True
    checkpoint.get_stage.return_value = {
        "pa_context": {"metadata": {"project": "project_nexus", "attendees": "Bailey Morton (PartnerAdvisory)"}},
        "resolved_transcript": "Bailey Morton: Discussing scope.",
        "speaker_map": {},
    }
    tracker = MagicMock()
    resume_meta = {
        "entity_resolutions": {"PartnerAdvisory": "org:partner_advisory"},
        "entity_clarifications": [{"token": "partner_advisory", "canonical_name": "PartnerAdvisory"}],
    }
    pa_ctx, _ = step_c0_pa_with_resume(
        pipeline=MagicMock(),
        checkpoint=checkpoint,
        should_resume=True,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="Bailey Morton: Discussing scope.",
        input_hash="abc123",
        meta=resume_meta,
        presentation_path=None,
        out_p=tmp_path,
        tracker=tracker,
    )
    assert pa_ctx["metadata"]["entity_resolutions"] == {"PartnerAdvisory": "org:partner_advisory"}
    assert pa_ctx["metadata"]["entity_clarifications"] == [{"token": "partner_advisory", "canonical_name": "PartnerAdvisory"}]
