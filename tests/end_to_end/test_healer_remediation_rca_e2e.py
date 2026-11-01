"""
End-to-end tests for Stage 01 Forensic Healer, Surgical Remediator, HealingDecisionStore,
and Output 03 Graph Healer / JsonSurgicalPatcher (Ed_Skyler_Amit 10-28-2026 RCA).
"""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.storage.healing_decision_store import (
    HealingDecisionStore,
    _normalize_record,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.forensic_healer import (
    heal_output_1_forensic,
    purge_asr_outro_hallucinations,
)
from dual_pipeline.transcript_qa_core.graph_ontology_entities import (
    _heal_person_and_golden_entities,
)
from dual_pipeline.transcript_qa_core.graph_ontology_signals import (
    _connect_atlas_and_golden,
)
from dual_pipeline.transcript_qa_core.json_surgical_patcher import JsonSurgicalPatcher
from dual_pipeline.transcript_qa_core.lens_ontology import _reground_maximo_and_sow2
from dual_pipeline.transcript_stages_core.attribution_builder import (
    AttributionSummaryBuilder,
)


def test_wave1_stage01_healer_and_remediator_e2e(tmp_path: Path) -> None:
    raw_transcript = (
        "# Project Lens Sync - Ed, Skyler, Amit -- HITL-Resolved Transcript | 10/28/2026\n\n"
        "**Date:** 10/28/2026\n\n"
        "### Presented Collateral & Artifacts\n\n"
        "- **Artifact 1:** `Ge Id Tag Pitch Gearbox Rejected Example`\n"
        "  - **File Reference:** `referenced_artifacts/ge_id_tag_pitch_gearbox_rejected_example.png`\n"
        "  - **View Scope:** Example validation-screen screenshot showing a rejected extraction.\n"
        "  - **Key Workflows:** {'component_ref': 'validation_screen'}\n\n"
        "---\n\n"
        "### [00:01:40]\n\n"
        "**Eduardo Juan More:** We're not specifically written to maximum fields, but we also need to save all the other data.\n\n"
        "### [00:06:55]\n\n"
        "**Eduardo Juan More:** Daily by equipment type, right? Because the electrical stuff is going to go to a different person than my... This is a production of the Center for Autism and Related Disorders, LLC.\n\n"
        "### [00:15:05]\n\n"
        "**Eduardo Juan More:** We are going to eventually in maximum have a field that we can write this number to.\n\n"
        "### [00:16:30]\n\n"
        "**Eduardo Juan More:** I'll be doing Odyssey before we start writing back to Maximum.\n"
    )
    purged, locators = purge_asr_outro_hallucinations(raw_transcript)
    assert "Center for Autism" not in purged
    assert "00:06:55" in locators
    assert "Daily by equipment type, right?" in purged

    meta = {
        "date": "10/28/2026",
        "roster": [
            {"name": "Eduardo Juan More", "org": "Apex Global Enterprises (NEA)", "role": "PGD / Maximo Business Lead & LENS Customer"},
            {"name": "Skyler Carlson", "org": "PartnerAdvisory", "role": "Lead - NEA Charter & Conduit"},
        ],
    }
    judge_report = {
        "output_1_evaluation": {
            "actionable_remediation_diff": (
                "Remove the unsupported Presented Collateral & Artifacts descriptions. "
                "At [00:06:55], remove 'This is a production of the Center for Autism and Related Disorders, LLC.' "
                "Correct 'maximum/Maximum' to 'Maximo' at [00:01:40], [00:15:05], and [00:16:30]."
            ),
            "critique_diff_envelope": {
                "observed_defect": "Retained broadcast hallucination and corrupted Maximo term as maximum.",
            },
        }
    }
    remediated, did_rem, audit = SurgicalRemediator.remediate_output_1(
        raw_transcript,
        qa_report={},
        judge_report=judge_report,
        metadata=meta,
    )
    assert did_rem is True
    assert "Center for Autism" not in remediated
    assert "written to Maximo fields" in remediated
    assert "in Maximo have a field" in remediated
    assert "writing back to Maximo" in remediated
    assert "View Scope:" not in remediated
    assert "Key Workflows:" not in remediated

    healed_direct = heal_output_1_forensic(raw_transcript, metadata=meta)
    assert "Center for Autism" not in healed_direct
    assert "written to Maximo fields" in healed_direct

    summary_table = AttributionSummaryBuilder.build_summary(meta, dialogue_text=healed_direct)
    assert "Client leadership introduction" not in summary_table
    assert "Verified C0 participant roster and attributed transcript turns" in summary_table


def test_wave1_healing_decision_store_none_confidence_e2e(tmp_path: Path) -> None:
    rec = _normalize_record({
        "run_id": "run-test-none-conf",
        "subsystem": "graph_healer",
        "confidence_top": None,
        "confidence_second": None,
        "ambiguity_margin": None,
    })
    assert rec.confidence_top == 1.0
    assert rec.confidence_second == 0.0
    assert rec.ambiguity_margin == 1.0

    store = HealingDecisionStore(db_path=tmp_path / "healing.sqlite")
    saved = store.record_decisions_batch([
        {
            "run_id": "run-test-none-conf",
            "subsystem": "graph_healer",
            "confidence_top": None,
            "confidence_second": None,
            "ambiguity_margin": None,
            "action_taken": "Normalized entity",
        }
    ])
    assert len(saved) == 1
    assert saved[0].confidence_top == 1.0


def test_wave2_output03_healer_and_json_surgical_patcher_e2e() -> None:
    entities = [
        {
            "id": "person:eduardo-juan-more",
            "name": "Eduardo Juan More",
            "type": "person",
            "description": "Atlas Lead and Rule Specialist at Apex Global Enterprises.",
            "evidence_ids": ["ev_hitl_role_eduardo-juan-more"],
        },
        {
            "id": "system:ibm-maximo",
            "name": "IBM Maximo",
            "type": "system",
            "description": "",
            "evidence_ids": ["ev_hitl_role_eduardo-juan-more"],
        },
        {
            "id": "system:lens",
            "name": "LENS",
            "type": "system",
            "description": "LENS platform.",
            "evidence_ids": ["ev-01"],
        },
        {
            "id": "system:golden-dataset",
            "name": "Golden Dataset",
            "type": "system",
            "description": "Golden dataset.",
            "evidence_ids": ["ev-01"],
        },
    ]
    evidence_index = {
        "ev_hitl_role_eduardo-juan-more": {
            "exact_text": "Eduardo Juan More (PGD / Maximo Business Lead & LENS Customer)",
        },
        "ev-turn-1505": {
            "exact_text": "We are going to eventually in maximum have a field that we can write this number to.",
        },
        "ev-01": {
            "exact_text": "I opened it in production.",
        },
    }
    meta = {
        "roster": [
            {"name": "Eduardo Juan More", "org": "Apex Global Enterprises", "role": "PGD / Maximo Business Lead & LENS Customer"},
        ]
    }
    changes: list[str] = []
    _heal_person_and_golden_entities(entities, evidence_index, changes, metadata=meta)
    ed_ent = next(e for e in entities if e["id"] == "person:eduardo-juan-more")
    assert "Atlas Lead and Rule Specialist" not in ed_ent["description"]
    assert "PGD / Maximo Business Lead & LENS Customer" in ed_ent["description"]

    max_ent = next(e for e in entities if e["id"] == "system:ibm-maximo")
    _reground_maximo_and_sow2(max_ent, "system:ibm-maximo", evidence_index, changes)
    assert max_ent["evidence_ids"] == ["ev-turn-1505"]

    signals: list[dict] = []
    _connect_atlas_and_golden(signals, entities, evidence_index, changes)
    assert not any(s.get("signal_id") == "rel-golden-dataset--lens--CONTRIBUTES_TO" for s in signals)

    raw_graph = {
        "entities": [
            {
                "id": "person:eduardo-juan-more",
                "name": "Eduardo Juan More",
                "type": "person",
                "description": "Atlas Lead and Rule Specialist at Apex Global Enterprises.",
                "evidence_ids": ["ev_hitl_role_eduardo-juan-more"],
            },
            {
                "id": "org:pgd",
                "name": "PGD",
                "type": "org",
                "description": "Power Generation Division.",
                "evidence_ids": ["ev-01"],
            },
        ],
        "relationships": [],
        "evidence_index": evidence_index,
    }
    critique_envelope = {
        "artifact": "OUTPUT03",
        "target_path": "entities",
        "observed_defect": "Misstates Eduardo's role and classifies PGD as an organization.",
        "required_delta": "Remove org:pgd and replace Eduardo's description with the C0-verified title.",
    }
    healed_str, audit = JsonSurgicalPatcher.apply_patch(
        original_json=raw_graph,
        critique_diff_or_envelope=critique_envelope,
        metadata=meta,
    )
    assert audit["patch_applied"] is True
    healed_data = json.loads(healed_str)
    rem_ids = [e.get("id") for e in healed_data.get("entities", [])]
    assert "org:pgd" not in rem_ids
