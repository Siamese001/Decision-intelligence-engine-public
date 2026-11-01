"""Unit tests for Grouped Speaker Review, Conflicting Anchor Guards, and Legacy Crosswalk.

Validates Wave 4 Acceptance Criteria:
1. Review items group homogeneous speaker clusters or bounded mixed passages.
2. Operator decisions explicitly bind scope utterance and word IDs.
3. Conflicting anchor guard blocks silent overwrites via ConflictingAnchorError.
4. Actions supported: confirm_all, confirm_with_exceptions, reject_split, cannot_tell.
5. Legacy crosswalk covers 100% of legacy fragments with zero unmapped records.
6. Review denominator migration from raw fragments (426) to proposed review items.
7. Schema validation against speaker_utterance.schema.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import jsonschema
import pytest

from dual_pipeline.speaker_attribution_core.review_units import (
    ConflictingAnchorError,
    LegacyCrosswalkEntry,
    ReviewDecision,
    ReviewItem,
    ReviewUnitManager,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = ROOT_DIR / "contracts/dual_pipeline/speaker_utterance.schema.json"
INVENTORY_PATH = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"
LOG_PATH = ROOT_DIR / "reports/lens_hitl_adjudication_log.json"


@pytest.fixture
def schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_generate_review_items_groups_clusters_and_passages(schema: Dict[str, Any]):
    """Verifies that review items group utterances into cluster cards and uncertain passages."""
    manager = ReviewUnitManager()
    utterances = [
        {"utterance_id": "utt_001", "speaker": "SPEAKER_00", "start_time_sec": 0.0, "end_time_sec": 5.0, "text": "First words.", "word_ids": ["w1", "w2"]},
        {"utterance_id": "utt_002", "speaker": "SPEAKER_00", "start_time_sec": 5.5, "end_time_sec": 10.0, "text": "Second words.", "word_ids": ["w3", "w4"]},
        {"utterance_id": "utt_003", "speaker": "SPEAKER_01", "start_time_sec": 12.0, "end_time_sec": 15.0, "text": "Third words.", "word_ids": ["w5"]},
        {"utterance_id": "utt_004", "speaker": "Unknown", "start_time_sec": 16.0, "end_time_sec": 18.0, "text": "Unclear words.", "word_ids": ["w6"], "overlap_context": True},
    ]

    items = manager.generate_review_items(utterances)
    assert len(items) == 3, "Expected 2 cluster items + 1 uncertain passage item"

    cl_00 = next(it for it in items if "utt_001" in it.target_utterance_ids)
    assert cl_00.item_type == "homogeneous_cluster"
    assert cl_00.target_utterance_ids == ["utt_001", "utt_002"]
    assert cl_00.word_ids == ["w1", "w2", "w3", "w4"]

    passage = next(it for it in items if "utt_004" in it.target_utterance_ids)
    assert passage.item_type == "uncertain_passage"

    # Schema Draft 2020-12 validation
    sub_validator = jsonschema.Draft202012Validator(schema["$defs"]["reviewItem"])
    for it in items:
        sub_validator.validate(it.to_dict())


def test_conflicting_anchor_guard_blocks_silent_overwrite():
    """Verifies that an operator decision cannot silently overwrite a confirmed anchor."""
    manager = ReviewUnitManager()
    utterances = [
        {"utterance_id": "utt_001", "speaker": "SPEAKER_00", "start_time_sec": 0.0, "end_time_sec": 5.0, "text": "First words.", "word_ids": ["w1"]},
        {"utterance_id": "utt_002", "speaker": "SPEAKER_00", "start_time_sec": 5.5, "end_time_sec": 10.0, "text": "Second words.", "word_ids": ["w2"]},
    ]
    items = manager.generate_review_items(utterances)

    verified_anchors = {"utt_001": "Jake Stark"}

    # Attempt to apply decision assigning all to Amit Ayer
    decision = ReviewDecision(
        decision_id="dec_test_001",
        review_id=items[0].review_id,
        action="confirm_all",
        assigned_speaker="Amit Ayer",
        scope_utterance_ids=["utt_001", "utt_002"],
        scope_word_ids=["w1", "w2"],
        context_digest=items[0].context_digest,
    )

    with pytest.raises(ConflictingAnchorError, match="Conflicting anchor guard triggered"):
        manager.apply_decision(decision, items, utterances, verified_anchors=verified_anchors)

    # With allow_anchor_override=True, it succeeds
    res = manager.apply_decision(
        decision, items, utterances, verified_anchors=verified_anchors, allow_anchor_override=True
    )
    assert res["action"] == "confirm_all"
    assert utterances[0]["speaker"] == "Amit Ayer"


def test_decision_actions_confirm_with_exceptions_and_cannot_tell():
    """Verifies partial confirmation with exceptions and 'cannot tell' preservation."""
    manager = ReviewUnitManager()
    utterances = [
        {"utterance_id": "utt_010", "speaker": "SPEAKER_01", "start_time_sec": 0.0, "end_time_sec": 2.0, "text": "Target uncertain.", "word_ids": ["w1"]},
        {"utterance_id": "utt_011", "speaker": "SPEAKER_01", "start_time_sec": 2.5, "end_time_sec": 5.0, "text": "First words.", "word_ids": ["w2"]},
    ]
    items = manager.generate_review_items(utterances)

    # 1. confirm_with_exceptions
    decision_exc = ReviewDecision(
        decision_id="dec_test_002",
        review_id=items[0].review_id,
        action="confirm_with_exceptions",
        assigned_speaker="Jennifer Pan",
        scope_utterance_ids=["utt_010", "utt_011"],
        scope_word_ids=["w1", "w2"],
        context_digest=items[0].context_digest,
        exceptions={"utt_010": "Morgan Vance Krutilla"},
    )
    res_exc = manager.apply_decision(decision_exc, items, utterances)
    assert res_exc["action"] == "confirm_with_exceptions"
    assert utterances[0]["speaker"] == "Morgan Vance Krutilla"
    assert utterances[1]["speaker"] == "Jennifer Pan"

    # Rebuild the evidence card after the transcript attribution changed.
    items = manager.generate_review_items(utterances)
    # 2. cannot_tell
    decision_ct = ReviewDecision(
        decision_id="dec_test_003",
        review_id=items[0].review_id,
        action="cannot_tell",
        assigned_speaker=None,
        scope_utterance_ids=items[0].target_utterance_ids,
        scope_word_ids=items[0].word_ids,
        context_digest=items[0].context_digest,
    )
    res_ct = manager.apply_decision(decision_ct, items, utterances)
    assert res_ct["resolution_status"] == "cannot_tell"
    assert utterances[0]["speaker"] == "Morgan Vance Krutilla", "cannot_tell must preserve current attribution without changing it"


def test_build_legacy_crosswalk_covers_all_426_turns(schema: Dict[str, Any]):
    """Verifies that the legacy crosswalk achieves 100% coverage across the 426 baseline turns."""
    assert INVENTORY_PATH.exists(), f"Missing ground truth inventory at {INVENTORY_PATH}"
    with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
        inv = json.load(f)

    legacy_turns = inv.get("turns", [])
    assert len(legacy_turns) == 426

    # Build canonical complete-thought utterances using Wave 3 assembler
    from dual_pipeline.audio_ingestion_core.utterance_assembler import assemble_utterances
    elements = [
        {
            "speaker": t.get("reference_speaker"),
            "start": t.get("start_time_sec", 0.0),
            "end": t.get("end_time_sec", 0.0),
            "text": t.get("utterance_text", ""),
        }
        for t in legacy_turns
    ]
    canonical_utts = assemble_utterances(elements)

    manager = ReviewUnitManager()
    crosswalk = manager.build_legacy_crosswalk(legacy_turns, canonical_utts)

    assert len(crosswalk) == 426, "Every single legacy fragment must have an entry in the crosswalk"
    unmapped = [cw for cw in crosswalk if cw.mapping_status == "unresolved" or cw.canonical_utterance_id is None]
    assert len(unmapped) == 0, f"Found {len(unmapped)} unmapped legacy fragments"

    # Schema Draft 2020-12 validation
    sub_validator = jsonschema.Draft202012Validator(schema["$defs"]["legacyCrosswalkEntry"])
    for cw in crosswalk:
        sub_validator.validate(cw.to_dict())


def test_workload_reduction_and_log_preservation():
    """Verifies workload reduction calculation and preservation of authentic Wave 4 log."""
    assert LOG_PATH.exists(), f"Missing adjudication log at {LOG_PATH}"
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        log = json.load(f)

    decisions_applied = log.get("decisions_applied", [])
    assert len(decisions_applied) >= 8, "Authentic Wave 4 decisions must be preserved"

    manager = ReviewUnitManager()
    # Mock 8 proposed review items vs 426 legacy fragments
    mock_items = [
        ReviewItem(
            review_id=f"rev_{i}",
            item_type="homogeneous_cluster",
            target_utterance_ids=[f"utt_{i}"],
            context_start_sec=0.0,
            context_end_sec=10.0,
            candidate_speakers=["Amit Ayer"],
        )
        for i in range(8)
    ]

    metrics = manager.compute_workload_metrics(
        legacy_count=426,
        review_items=mock_items,
        applied_decisions_count=len(decisions_applied),
    )

    assert metrics["legacy_fragment_denominator"] == 426
    assert metrics["proposed_review_items"] == 8
    assert metrics["review_item_reduction_pct"] >= 95.0, "Workload reduction from 426 to 8 must exceed 95%"
    assert metrics["workload_status"] == "QUALIFIED_REDUCTION"
