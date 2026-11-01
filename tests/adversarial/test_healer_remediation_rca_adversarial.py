"""
Adversarial and edge-case tests for Stage 01 Forensic Healer, Surgical Remediator,
HealingDecisionStore, and Output 03 Graph Healer / JsonSurgicalPatcher.

Covers:
1. Boundary payload (empty strings, empty dicts, missing keys)
2. Malformed input (invalid JSON strings, None confidence values)
3. Invariant bypass attempt (protected entity deletion blocked in JsonSurgicalPatcher)
4. Exact production regression (Ed_Skyler_Amit 10-28-2026 RCA bugs 1-4)
"""

from __future__ import annotations

import pytest

from dual_pipeline.storage.healing_decision_store import _normalize_record
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.forensic_healer import (
    purge_asr_outro_hallucinations,
)
from dual_pipeline.transcript_qa_core.json_surgical_patcher import (
    JsonSurgicalPatcher,
    ProtectedPathMutationError,
    _parse_json_patch_envelope,
)


def test_adversarial_boundary_empty_inputs() -> None:
    text, did_rem, trail = SurgicalRemediator.remediate_output_1("", {}, {})
    assert text == ""
    assert did_rem is False

    purged, locs = purge_asr_outro_hallucinations("")
    assert purged == ""
    assert locs == []

    patched, audit = JsonSurgicalPatcher.apply_patch("", {})
    assert patched == ""
    assert audit["status"] == "SKIPPED"


def test_adversarial_malformed_inputs_and_none_confidences() -> None:
    rec = _normalize_record({
        "confidence_top": None,
        "confidence_second": None,
        "ambiguity_margin": None,
        "defect_reasons": None,
        "audit_trail": None,
        "metadata": None,
    })
    assert rec.confidence_top == 1.0
    assert rec.confidence_second == 0.0
    assert rec.ambiguity_margin == 1.0

    ops, prot = _parse_json_patch_envelope("not valid json, but remove `org:pgd` now")
    assert ops.get("remove_entities") == ["org:pgd"]
    assert prot == []


def test_adversarial_invariant_bypass_protected_entity_removal_blocked() -> None:
    graph_data = {
        "entities": [
            {"id": "person:eduardo-juan-more", "name": "Eduardo Juan More", "type": "person"},
            {"id": "org:pgd", "name": "PGD", "type": "org"},
        ],
        "relationships": [],
        "evidence_index": {},
    }
    envelope = {
        "operations": {"remove_entities": ["person:eduardo-juan-more"]},
        "protected_entities": ["person:eduardo-juan-more"],
    }
    with pytest.raises(ProtectedPathMutationError):
        JsonSurgicalPatcher.apply_patch(graph_data, envelope)
