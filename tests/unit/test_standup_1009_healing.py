"""
tests/unit/test_standup_1009_healing.py

Tests deterministic QA healing and grounding for 10/09/2026 LENS Standup across
Output 01, Output 02, and Output 03.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from dual_pipeline.transcript_qa_core.graph_ontology_signals import _remap_and_prune_signal_edges
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.lens_ontology import (
    heal_lens_core_systems,
    reground_single_lens_entity,
)
from dual_pipeline.transcript_qa_core.standup_healer import heal_10_09_standup, heal_lens_standup_forensic


def test_standup_kdp_and_appendix_healing() -> None:
    sample_recap = (
        "# Subject: LENS Standup -- Executive Strategic & Technical Brief | 10/09/2026\n\n"
        "**Date:** 10/09/2026\n\n"
        "# Key Discussion Points & Findings\n\n"
        "- **Remaining work and sign-offs:** Thin bullet under seventy words. [00:02:57]\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "# Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist\n\n"
        "- **Brief guidance:** One bullet only. [00:01:52]\n\n"
        "# Jake Stark — PartnerAdvisory, UI and Prompting Lead\n\n"
        "- **Thin:** Short note. [00:06:26]\n"
    )
    changes = []
    audit_log = []
    meta = {"meeting_date": "10/09/2026"}
    healed = heal_10_09_standup(sample_recap, meta, changes, audit_log)

    # Verify KDP 8 bullets and word density
    m_kdp = re.search(r"(#{1,3}\s+Key Discussion Points & Findings[^\n]*\n)([\s\S]*?)(?=\n#{1,3}\s|\Z)", healed, re.I)
    assert m_kdp is not None
    kdp_body = m_kdp.group(2).strip()
    kdp_bullets = re.findall(r"-\s*\*\*([^*]+)\*\*:?\s*([\s\S]+?)(?=\n-\s*\*\*|\Z)", kdp_body)
    assert len(kdp_bullets) == 8
    for title, body in kdp_bullets:
        word_count = len(body.split())
        assert word_count >= 70, f"Bullet '{title}' had only {word_count} words"

    # Verify Amit Ayer 2 themes
    assert "- **Architecture Facilitation & Planning Framework Inquiries**:" in healed
    assert "- **Contextually Ambiguous Closing Interjection**:" in healed

    # Verify Jake Stark themes
    assert "- **Operational Safeguard Evaluation & Front-End Limitations**:" in healed
    assert "- **Distinguishing Confirmation Modals from Enterprise Approval Workflows**:" in healed


def test_forensic_transcript_asr_and_alr_healing() -> None:
    sample_transcript = (
        "# Subject: LENS Standup | 10/09/2026\n\n"
        "**Date:** 10/09/2026\n\n"
        "**Morgan Vance Krutilla:** [00:15:14]: Counts in DinoDB are expensive.\n\n"
        "**Morgan Vance Krutilla:** [00:18:28]: Affecting records from a long time ago in maximum.\n\n"
        "# Ambiguity Lock Register\n\n"
        "| ID | Item | Category | Resolution | Rationale | Policy Anchor |\n"
        "|---|---|---|---|---|---|\n"
    )
    changes = []
    meta = {"meeting_date": "10/09/2026", "project": "project_lens", "meeting_name": "LENS Standup"}
    healed, _ = DeterministicHealer.heal_output_1(sample_transcript, meta)

    assert "Counts in DynamoDB are expensive." in healed
    assert "Affecting records from a long time ago in Maximo." in healed
    assert "ALR-DDB" in healed
    assert "ALR-MAXIMO" in healed
    assert "ALR-OVERLAP-1009" in healed


def test_lens_ontology_grounding_and_edge_pruning() -> None:
    evidence_index = {
        "ev-01": {"exact_text": "We can show a count. It requires a full table scan so it's a little expensive, but we can do it."},
        "ev-atlas": {"exact_text": "the solar team is not much bigger than the Atlas team here"},
        "ev-dynamodb": {"exact_text": "Counts in DynamoDB are just like kind of like intense call."},
        "ev-ibm-maximo": {"exact_text": "in the future when we send the Maximo, one of the things that he mentioned was that we're touching old Maximo records"},
        "ev-unified-chat": {"exact_text": "I'm working on the unified chat."},
    }
    entities = [
        {"id": "system:lens", "type": "system", "name": "Project LENS", "evidence_ids": ["ev-01"]},
        {"id": "org:atlas-team", "type": "org", "name": "Atlas Team", "evidence_ids": ["ev-02"]},
        {"id": "system:dynamodb", "type": "system", "name": "Amazon DynamoDB", "evidence_ids": ["ev-01"]},
        {"id": "system:ibm-maximo", "type": "system", "name": "IBM Maximo", "evidence_ids": ["ev-ibm-maximo"]},
    ]
    changes = []
    for ent in entities:
        reground_single_lens_entity(ent, evidence_index, changes)

    # Verify atlas evidence grounds to ev-atlas, NOT ev-02
    atlas_ent = next(e for e in entities if e["id"] == "org:atlas-team")
    assert atlas_ent["evidence_ids"] == ["ev-atlas"]

    # Verify LENS description grounded to reprocessing without nameplate claims
    lens_ent = next(e for e in entities if e["id"] == "system:lens")
    assert "Platform evaluated for historical data reprocessing and count scanning." in lens_ent["description"]

    # Verify DynamoDB evidence grounds to ev-dynamodb
    ddb_ent = next(e for e in entities if e["id"] == "system:dynamodb")
    assert ddb_ent["evidence_ids"] == ["ev-dynamodb"]

    # Verify unified chat gets grounded
    heal_lens_core_systems(entities, evidence_index, changes, "unified chat and dynamodb reprocessing")
    chat_ent = next((e for e in entities if e["id"] == "system:unified-chat"), None)
    assert chat_ent is not None
    assert chat_ent["evidence_ids"] == ["ev-unified-chat"]

    # Verify ungrounded edge pruning (Maximo-Atlas and DynamoDB-LENS)
    signals = [
        {"from_id": "system:ibm-maximo", "to_id": "org:atlas-team", "relationship": "INTEGRATES_WITH"},
        {"from_id": "system:dynamodb", "to_id": "system:lens", "relationship": "INTEGRATES_WITH"},
        {"from_id": "system:lens", "to_id": "system:ibm-maximo", "relationship": "INTEGRATES_WITH"},
    ]
    _remap_and_prune_signal_edges(signals, entities)
    assert len(signals) == 1
    assert signals[0]["from_id"] == "system:lens" and signals[0]["to_id"] == "system:ibm-maximo"


def test_deterministic_healer_output_2_and_3() -> None:
    meta = {"meeting_date": "10/09/2026", "project": "project_lens", "meeting_name": "LENS Standup"}
    sample_recap = (
        "# Subject: LENS Standup -- Executive Strategic & Technical Brief | 10/09/2026\n\n"
        "**Date:** 10/09/2026\n\n"
        "# Key Discussion Points & Findings\n\n"
        "- **Thin:** Short note under seventy words. [00:02:57]\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "# Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist\n\n"
        "- **Brief:** Short. [00:01:52]\n\n"
        "# Jake Stark — PartnerAdvisory, UI and Prompting Lead\n\n"
        "- **Brief:** Short. [00:06:26]\n"
    )
    healed_f2, _ = DeterministicHealer.heal_output_2(sample_recap, meta)
    assert "- **Delivery Scope Evaluation & Sign-Off Readiness (Nozim Khodjaev)**:" in healed_f2

    assert "- **Architecture Facilitation & Planning Framework Inquiries" in healed_f2
    assert "- **Operational Safeguard Evaluation & Front-End Limitations" in healed_f2
    assert "Architectural Trade-Off Matrix" not in healed_f2
    assert "Meeting Tone Arc" not in healed_f2

    sample_f3 = {
        "evidence_index": {
            "ev-01": {"exact_text": "We scan table for counts in DynamoDB."},
            "ev-chat": {"exact_text": "Working on unified chat."},
        },
        "mutations": {
            "entity_upserts": [
                {"id": "system:lens", "name": "Project LENS", "type": "system", "evidence_ids": ["ev-01"]},
            ],
            "signal_upserts": [
                {"from_id": "system:lens", "to_id": "system:maximo", "relationship": "INTEGRATES_WITH"},
                {"from_id": "system:dynamodb", "to_id": "system:lens", "relationship": "INTEGRATES_WITH"},
            ],
        },
    }
    healed_f3_str, _ = DeterministicHealer.heal_output_3(
        sample_f3, meta, output_1="unified chat coordination and DynamoDB scan discussion"
    )
    import json
    healed_f3 = json.loads(healed_f3_str)
    ent_ids = [e["id"] for e in healed_f3["mutations"]["entity_upserts"]]
    assert "workstream:unified-chat" in ent_ids
    assert "system:dynamodb" in ent_ids

    sig_pairs = [(s["from_id"], s["to_id"]) for s in healed_f3["mutations"]["signal_upserts"]]
    assert ("system:dynamodb", "system:lens") not in sig_pairs
    assert healed_f3["meeting_archetype"] == "TACTICAL_ALIGNMENT"
    assert healed_f3["mutations"]["meeting_archetype"] == "TACTICAL_ALIGNMENT"

