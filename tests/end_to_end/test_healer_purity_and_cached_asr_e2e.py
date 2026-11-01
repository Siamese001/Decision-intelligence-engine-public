"""End-to-end tests for Wave 4 Healer Purity, U0 Normalization & Cached ASR Alignment (CAT-2)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_dangling_evidence_citations,
    _heal_entities,
    _heal_evidence_index,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.recap_healer import (
    _clamp_locators_to_duration,
    heal_output_2_recap,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    _default_attendees_and_roster,
    _normalize_speaker_headers,
)


def test_graph_healer_preserves_authentic_system_descriptions_and_prunes_dangling_citations() -> None:
    """CAT2-P0-1 & CAT2-P0-2: Authentic descriptions on system:maximo/sap are preserved and dangling citations are pruned."""
    entities = [
        {"id": "system:maximo", "type": "system", "name": "Maximo", "description": "Authentic APEX work order system."},
        {"id": "system:sap", "type": "system", "name": "SAP", "description": "Authentic ERP ledger."},
    ]
    changes: list[str] = []
    _heal_entities(entities, changes)
    assert entities[0]["description"] == "Authentic APEX work order system."
    assert entities[1]["description"] == "Authentic ERP ledger."

    data = {
        "evidence_index": {
            "ev-01": {"speaker": "Alice Smith", "text": "We reviewed the budget.", "locator": "[00:01:00]"},
            "ev-gis": {"speaker": "", "text": "GIS map layer.", "locator": "[00:02:00]"},
            "ev-empty": {"speaker": "Bob", "text": "", "grounding_status": "MISSING_QUOTE"},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:gis", "type": "system", "evidence_ids": ["ev-01", "ev-nonexistent-99"]}
            ],
            "relationship_signals": [],
            "decision_signals": [],
        },
    }
    _heal_evidence_index(data, changes)
    assert data["evidence_index"]["ev-gis"]["speaker"] == "Forensic Context"
    assert "ev-empty" not in data["evidence_index"]

    _heal_dangling_evidence_citations(data, data["graph_mutations"], changes)
    assert data["graph_mutations"]["entity_upserts"][0]["evidence_ids"] == ["ev-01"]


def test_frozen_output_3_is_returned_untouched_by_healing_mesh() -> None:
    """CAT2-P0-3: When output_3 is frozen in state_machine, heal_output_3 returns it byte-for-byte untouched."""
    frozen_sm = SimpleNamespace(is_frozen=lambda stage: stage == "output_3")
    raw_json = '{"frozen_key": "untouched_value", "evidence_index": {}}'
    out_str, changes = DeterministicHealer.heal_output_3(
        raw_json,
        metadata={"state_machine": frozen_sm},
        output_1="[00:00:01] Alice: Hello",
    )
    assert out_str == raw_json
    assert changes == []


def test_recap_healer_preserves_same_first_name_attendees_and_unbounded_timestamps() -> None:
    """CAT2-P1-2: Attendees sharing a first name (e.g. Drew Oakley and Dan Brankin) are both retained."""
    recap_md = (
        "# Attendees\n"
        "- Drew Oakley (Client Service Lead, PartnerAdvisory)\n"
        "- Dan Brankin (Grid Architect, Apex Global)\n\n"
        "## Meeting Objective\n\n"
        "Review grid architecture at [01:15:00].\n\n"
        "## Action & Governance Ledger\n\n"
        "No formal actions were established in this session; open initiatives remain unresolved questions.\n"
    )
    healed = heal_output_2_recap(recap_md, metadata={}, output_1="", changes=[], audit_log=[])
    assert "Drew Oakley" in healed
    assert "Dan Brankin" in healed
    assert "[01:15:00]" in healed


def test_u0_ingestion_anchors_speaker_header_normalization_and_merges_shared_room_mics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAT2-P1-3: Bold inline emphasis in dialogue body is not rewritten as a speaker header; shared_room_mics merge into roster."""
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, **kw: SimpleNamespace(canonical_name="Alice Wonderland", source="TEST") if name == "Alice" else None,
    )
    raw_tx = "[00:00:05] **Alice:** Welcome everyone, **Alice** is presenting the **Project Plan** today.\n"
    clean_tx, norms = _normalize_speaker_headers(raw_tx, {})
    assert clean_tx.startswith("[00:00:05] **Alice Wonderland:**")
    assert "**Alice** is presenting" in clean_tx
    assert len(norms) == 1

    meta = {
        "attendees": ["Alice Wonderland (VP, PartnerAdvisory)"],
        "shared_room_mics": {"Room Mic 1": ["Bob Builder (Director, PartnerAdvisory)", "Carol Danvers (Lead, PartnerAdvisory)"]},
    }
    _default_attendees_and_roster(meta, raw_tx)
    roster_names = {r["name"] for r in meta["roster"]}
    assert "Bob Builder" in roster_names
    assert "Carol Danvers" in roster_names
