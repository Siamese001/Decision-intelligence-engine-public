"""
tests/test_output_quality_wave4.py

Wave 4 Acceptance Suite: Projection evidence and graph reconciliation.
Covers:
1. Substantive transcripts cannot release collateral-only evidence (Conduit).
2. Empty mutations cannot claim new entities; truthful delta counters (Conduit).
3. Material coverage: active attendees (Peter Ashley, Anece Elakkari, Marcus Vance) and systems (DTN Weather) (APEX_TRANS).
4. Locators resolve to authentic transcript timestamps without splicing.
5. Proposal evidence pruning and rebinding based on topical overlap.
6. Verified graph no-ops remain valid on idempotent re-ingestion.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from dual_pipeline.transcript_qa_core.gate3_evidence import (
    check_gate_3_14,
    check_locators_and_splicing,
    check_material_coverage,
    check_substantive_transcript_evidence,
    check_topical_overlap,
    check_truthful_reconciliation_counters,
)
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import (
    _heal_unrelated_evidence_citations,
    heal_proposals_and_risks,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_neet_coverage,
    heal_projection_evidence_and_coverage,
    heal_spliced_locators,
    heal_substantive_transcript_evidence,
    heal_truthful_counters,
)
from dual_pipeline.transcript_stages_core.incremental_reconciler import sync_reconciliation_counters

REPO_ROOT = Path(__file__).resolve().parent.parent

CONDUIT_DIR = REPO_ROOT / "output" / "conduit" / "08-31-2026" / "Conduit_Lessons_Learned_Michael_PartnerAdvisory"
CONDUIT_F1_PATH = CONDUIT_DIR / "01_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_hitl_resolved_transcript.md"
CONDUIT_F3_PATH = CONDUIT_DIR / "03_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_canonical_signal_projection.json"

NEET_DIR = REPO_ROOT / "output" / "apex_trans" / "09-11-2026"
NEET_F1_PATH = NEET_DIR / "01_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_hitl_resolved_transcript.md"
NEET_F3_PATH = NEET_DIR / "03_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_canonical_signal_projection.json"


def test_conduit_collateral_only_evidence_rejected_and_healed():
    """Substantive transcripts cannot release collateral-only evidence."""
    assert CONDUIT_F1_PATH.exists()
    assert CONDUIT_F3_PATH.exists()

    f1_text = CONDUIT_F1_PATH.read_text(encoding="utf-8")
    raw_f3 = json.loads(CONDUIT_F3_PATH.read_text(encoding="utf-8"))

    # 1. Raw Conduit projection contains only collateral items (ev-att-*) and zero spoken turns
    ok, errs = check_substantive_transcript_evidence(raw_f3, f1_text=f1_text)
    assert not ok
    assert any("collateral/attachment evidence" in e for e in errs)

    # 2. Gate 3 fails closed on collateral-only evidence for substantive transcript
    qa_result = run_qa_gate_3(raw_f3, f1_text=f1_text)
    assert not qa_result["passed"]
    assert any("collateral/attachment evidence" in f for f in qa_result["failures"])

    # 3. Heal substantive dialogue evidence from Output 1
    healed_f3 = copy.deepcopy(raw_f3)
    changes: list[str] = []
    heal_substantive_transcript_evidence(healed_f3, f1_text=f1_text, changes=changes)

    assert len(changes) > 0
    ok_after, errs_after = check_substantive_transcript_evidence(healed_f3, f1_text=f1_text)
    assert ok_after, f"Expected pass after healing, got errors: {errs_after}"
    assert len(errs_after) == 0


def test_conduit_empty_mutations_cannot_claim_new_entities():
    """Empty mutations cannot claim new entities; delta counters must be truthful."""
    raw_f3 = json.loads(CONDUIT_F3_PATH.read_text(encoding="utf-8"))

    # Raw Conduit projection has entity_upserts: [] but new_entities: 7 and incremental_entities_added: 7
    assert len(raw_f3.get("graph_mutations", {}).get("entity_upserts", [])) == 0
    assert raw_f3.get("incremental_reconciliation", {}).get("new_entities") == 7

    # 1. Validation fails closed on untruthful counters
    ok, errs = check_truthful_reconciliation_counters(raw_f3)
    assert not ok
    assert any("Empty mutations cannot claim new entities" in e for e in errs)

    # 2. Sync counters strictly from final mutation arrays
    healed_f3 = copy.deepcopy(raw_f3)
    sync_reconciliation_counters(healed_f3)

    assert healed_f3["incremental_reconciliation"]["new_entities"] == 0
    assert healed_f3["_strategic_analysis"]["_incremental_reconciliation"]["incremental_entities_added"] == 0
    assert healed_f3["_strategic_analysis"]["_incremental_reconciliation"]["is_material_graph_change"] is False

    ok_after, errs_after = check_truthful_reconciliation_counters(healed_f3)
    assert ok_after, f"Expected truthful counters, got errors: {errs_after}"
    assert len(errs_after) == 0


def test_neet_missing_attendees_and_dtn_weather_coverage_rejected_and_healed():
    """APEX_TRANS active PartnerAdvisory participants and DTN weather system must be covered or justified."""
    assert NEET_F1_PATH.exists()
    assert NEET_F3_PATH.exists()

    raw_f3 = json.loads(NEET_F3_PATH.read_text(encoding="utf-8"))

    # 1. Raw APEX_TRANS projection omits PartnerAdvisory participants and DTN Weather
    ok, errs = check_material_coverage(raw_f3)
    assert not ok
    assert any("Peter Ashley" in e for e in errs)
    assert any("Anece Elakkari" in e for e in errs)
    assert any("Marcus Vance" in e for e in errs)
    assert any("system:dtn-weather" in e for e in errs)

    # 2. Healer restores missing attendees and DTN Weather platform
    healed_f3 = copy.deepcopy(raw_f3)
    changes: list[str] = []
    heal_neet_coverage(healed_f3, changes=changes)

    ent_ids = {e["id"] for e in healed_f3["graph_mutations"]["entity_upserts"]}
    assert "person:peter-ashley" in ent_ids
    assert "person:anece-elakkari" in ent_ids
    assert "person:aaron-adams" in ent_ids
    assert "system:dtn-weather" in ent_ids

    ok_after, errs_after = check_material_coverage(healed_f3)
    assert ok_after, f"Expected full material coverage, got errors: {errs_after}"
    assert len(errs_after) == 0


def test_locators_resolve_and_spliced_quotes_rejected_and_healed():
    """Evidence locators must resolve to Output 1 turns and avoid spliced quotes."""
    f1_text = NEET_F1_PATH.read_text(encoding="utf-8")

    # 1. Spliced timestamp locator and ellipsis tokens
    spliced_payload: Dict[str, Any] = {
        "evidence_index": {
            "ev-spliced-01": {
                "statement_id": "ev-spliced-01",
                "locator": {"kind": "timestamp", "value": "00:00:52 - 00:03:23"},
                "exact_text": "AI smoke detection [...] alert like our control room.",
                "text": "AI smoke detection alert.",
            },
            "ev-unresolved-02": {
                "statement_id": "ev-unresolved-02",
                "locator": {"kind": "timestamp", "value": "99:59:59"},
                "exact_text": "Non-existent turn text in transcript.",
                "text": "Non-existent turn text.",
            },
        }
    }

    ok, errs = check_locators_and_splicing(spliced_payload, f1_text=f1_text)
    assert not ok
    assert any("spliced multi-turn timestamp locator" in e for e in errs)
    assert any("spliced quote with ellipsis tokens" in e for e in errs)
    assert any("does not resolve to any turn" in e for e in errs)

    # 2. Heal spliced locators
    changes: list[str] = []
    heal_spliced_locators(spliced_payload, f1_text=f1_text, changes=changes)

    loc_val = spliced_payload["evidence_index"]["ev-spliced-01"]["locator"]["value"]
    assert loc_val == "00:00:52"
    assert " [...] " not in spliced_payload["evidence_index"]["ev-spliced-01"]["exact_text"]


def test_proposal_topical_overlap_pruning_and_rebinding():
    """Evidence citations without topical overlap are pruned or rebound to overlapping evidence."""
    payload: Dict[str, Any] = {
        "evidence_index": {
            "ev-arch-01": {
                "statement_id": "ev-arch-01",
                "text": "Discussion on database migration and sql schema partitioning.",
                "exact_text": "Database migration and sql schema partitioning.",
            },
            "ev-weather-02": {
                "statement_id": "ev-weather-02",
                "text": "DTN weather application lightning data layer during line trip events.",
                "exact_text": "DTN weather application lightning data layer.",
            },
        },
        "graph_mutations": {
            "proposal_updates": [
                {
                    "id": "prop:weather-lightning-integration",
                    "name": "DTN Weather Lightning Alert Integration",
                    "description": "Integration proposal for automated DTN weather lightning tracking.",
                    "evidence_ids": ["ev-arch-01"],  # Unrelated evidence citation!
                }
            ]
        },
    }

    # 1. Topical overlap check fails on unrelated citation
    ok, errs = check_topical_overlap(payload)
    assert not ok
    assert any("lacks topical overlap" in e for e in errs)

    # 2. Heal unrelated evidence citations
    changes: list[str] = []
    _heal_unrelated_evidence_citations(payload, payload["graph_mutations"], changes)

    prop = payload["graph_mutations"]["proposal_updates"][0]
    assert "ev-weather-02" in prop["evidence_ids"]
    assert "ev-arch-01" not in prop["evidence_ids"]

    ok_after, errs_after = check_topical_overlap(payload)
    assert ok_after, f"Expected topical overlap after rebinding, got errors: {errs_after}"


def test_verified_graph_no_op_remains_valid():
    """Verified graph no-ops remain valid on idempotent re-ingestion."""
    # A valid retrospective or idempotent re-ingestion with zero mutations
    no_op_payload: Dict[str, Any] = {
        "schema_version": "5.7",
        "meeting_id": "meeting-20260831-conduit-lessons-learned",
        "classification": "restricted",
        "audiences": ["nee-engineering"],
        "observed_at": "08/31/2026",
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "speaker": "person:michael-partner_advisory",
                "kind": "technical",
                "source_classification": "FACT",
                "text": "Conduit paused while downstream enterprise access was pending.",
                "exact_text": "Conduit paused while downstream enterprise access was pending.",
                "locator": {"kind": "timestamp", "value": "00:01:15"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_only_statement_ids": [],
        "_strategic_analysis": {
            "description": "Retrospective discussion without net-new canonical mutations.",
            "_incremental_reconciliation": {
                "status": "INCREMENTAL_RECONCILED",
                "incremental_entities_added": 0,
                "confirmed_entities_count": 0,
                "is_material_graph_change": False,
            },
        },
        "incremental_reconciliation": {
            "new_entities": 0,
            "confirmed_entities": 0,
        },
    }

    dummy_f1 = (
        "### [00:01:15]\n\n"
        "**Michael PartnerAdvisory:** Conduit paused while downstream enterprise access was pending.\n"
    )

    qa_res = run_qa_gate_3(no_op_payload, f1_text=dummy_f1)
    assert qa_res["passed"], f"Verified no-op must pass Gate 3, failures: {qa_res.get('failures')}"
    assert qa_res["gates"]["Gate_3_14_Projection_Evidence_And_Reconciliation"]["passed"]
