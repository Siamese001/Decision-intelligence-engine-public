"""Adversarial and edge-case tests for Wave 3 transcript extraction, identity matching, and coverage healing."""
from __future__ import annotations

from pathlib import Path

from types import SimpleNamespace

from dual_pipeline.graph_consolidation_core.detect.nodes import (
    _evaluate_fuzzy_matches,
    normalize_name,
)
from dual_pipeline.identity_core.matching import (
    determine_resolution_outcome,
    rank_mention_candidates,
)
from dual_pipeline.identity_core.records import EntityRecord
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_substantive_transcript_evidence,
)


def test_boundary_payload_empty_and_short_filler_transcript_healing() -> None:
    """Category 1 (Boundary payload): Empty transcripts or <40 char filler turns do not inject noise into reconciliation."""
    f1_text = (
        "### [00:00:05]\n\n**Speaker_Beta Tharakan:** Sounds good, thanks!\n\n"
        "### [00:00:10]\n\n**Andy Kilinskis:** Agreed.\n\n"
        "### [00:00:15]\n\n**Egle Tamasevice:** Bye.\n"
    )
    data = {
        "evidence_index": {
            "ev-01": {"speaker": "person:andy-kilinskis", "attribution_mode": "SPOKEN_DIALOGUE", "text": "Long substantive statement one that is already covered."},
            "ev-02": {"speaker": "person:andy-kilinskis", "attribution_mode": "SPOKEN_DIALOGUE", "text": "Long substantive statement two that is already covered."},
            "ev-03": {"speaker": "person:andy-kilinskis", "attribution_mode": "SPOKEN_DIALOGUE", "text": "Long substantive statement three that is already covered."},
        }
    }

    heal_substantive_transcript_evidence(data, f1_text)
    assert len(data["evidence_index"]) == 3


def test_malformed_input_role_suffixed_mention_and_ambiguous_first_name() -> None:
    """Category 2 (Malformed input): Role-suffixed mentions strip '-- <Role>' cleanly while ambiguous first names do not false-link."""
    entities = {
        "entity-egle": EntityRecord(
            entity_id="entity-egle",
            node_type="person",
            canonical_name="Egle Tamasevice",
            description="Apex Global Tech Lead on Project Beacon",
            normalized_name="egle tamasevice",
            status="canonical",
        ),
        "entity-michael-a": EntityRecord(
            entity_id="entity-michael-a",
            node_type="person",
            canonical_name="Michael Romagnolo",
            description="PartnerAdvisory Engineer",
            normalized_name="michael romagnolo",
            status="canonical",
        ),
        "entity-michael-b": EntityRecord(
            entity_id="entity-michael-b",
            node_type="person",
            canonical_name="Michael Scott",
            description="PartnerAdvisory Manager",
            normalized_name="michael scott",
            status="canonical",
        ),
    }
    catalog = SimpleNamespace(entities=entities)
    cids = list(entities.keys())

    # Role-suffixed mention "Egle Tamasevice -- Apex Global Tech Lead" resolves cleanly to entity-egle
    suffixed_alts = rank_mention_candidates(
        catalog, "Egle Tamasevice -- Apex Global Tech Lead", "person", "Project Beacon architecture review", "project_beacon", cids
    )
    assert suffixed_alts[0]["entity_id"] == "entity-egle"
    assert suffixed_alts[0]["match_score"] >= 0.95

    # Unambiguous first name "Egle" resolves cleanly with high confidence
    first_name_alts = rank_mention_candidates(
        catalog, "Egle", "person", "stakeholder reference in Project Beacon", "project_beacon", cids
    )
    assert first_name_alts[0]["entity_id"] == "entity-egle"
    assert first_name_alts[0]["match_score"] >= 0.90

    # Ambiguous first name "Michael" (two Michaels in catalog) has 0 margin and must NOT auto-link
    ambiguous_alts = rank_mention_candidates(
        catalog, "Michael", "person", "stakeholder reference in Project Beacon", "project_beacon", cids
    )
    outcome, _ = determine_resolution_outcome(
        type_conflict=False,
        exact_matches=[],
        profile={"entity_intent": "resolve_only", "allow_high_confidence_link": True},
        declared_type_valid=True,
        stable_identifier=None,
        alternatives=ambiguous_alts,
    )
    assert outcome != "AUTO_LINK_HIGH_CONFIDENCE"


def test_overflow_exhaustion_per_speaker_healer_caps_at_24_speakers() -> None:
    """Category 4 (Overflow & exhaustion): Per-speaker healer caps supplementary quotes at 24 speakers without unbounded growth."""
    blocks = []
    for i in range(35):
        blocks.append(
            f"### [00:{i:02d}:10]\n\n"
            f"**SpeakerNumber{i:02d} Lastname{i:02d}:** "
            f"This is a substantive technical update about microservice component {i:02d} and database indexing."
        )
    f1_text = "\n\n".join(blocks) + "\n"
    data: dict[str, dict] = {"evidence_index": {}}

    heal_substantive_transcript_evidence(data, f1_text)
    assert len(data["evidence_index"]) == 24


def test_incompatible_node_types_never_consolidate() -> None:
    """Category 5 (Invariant bypass attempt): Incompatible node types (person vs deliverable) never consolidate even on identical tokens."""
    nodes = [
        {"node_id": "person-sherpa", "node_type": "person", "canonical_name": "Sherpa Migration", "status": "canonical"},
        {"node_id": "deliv-sherpa", "node_type": "deliverable", "canonical_name": "Sherpa Migration", "status": "canonical"},
    ]
    norm_map = {n["node_id"]: normalize_name(n["canonical_name"]) for n in nodes}
    assert _evaluate_fuzzy_matches(nodes, norm_map, set(), {}, similarity_threshold=0.75) == []
