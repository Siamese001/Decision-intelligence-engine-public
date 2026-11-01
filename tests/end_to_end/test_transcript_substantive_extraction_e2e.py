"""End-to-end verification of Wave 3 substantive transcript extraction, quote binding, and entity consolidation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.graph_consolidation_core.detect.nodes import (
    _evaluate_fuzzy_matches,
    normalize_name,
)
from dual_pipeline.normalization_core.meeting import normalize_copilot
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_substantive_transcript_evidence,
)


def test_normalize_meeting_binds_direct_quotes_and_unambiguous_first_name_speakers() -> None:
    """Verify normalize_copilot populates evidence_quotes and links first-name transcript speakers to declared upserts."""
    projection = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "entity_id": "entity-ninan",
                    "canonical_name": "Speaker_Beta Tharakan",
                    "node_type": "person",
                    "organization": "PartnerAdvisory",
                    "confidence": 0.95,
                },
                {
                    "entity_id": "entity-egle",
                    "canonical_name": "Egle Tamasevice",
                    "node_type": "person",
                    "organization": "Apex Global Enterprises",
                    "confidence": 0.95,
                },
                {
                    "entity_id": "deliverable-opensearch",
                    "canonical_name": "Sherpa AWS OpenSearch Migration",
                    "node_type": "deliverable",
                    "confidence": 0.92,
                },
            ],
            "relationship_signals": [
                {
                    "source_entity_id": "entity-ninan",
                    "target_entity_id": "deliverable-opensearch",
                    "relationship_type": "OWNS_OR_LEADS",
                    "rationale": "Speaker_Beta leads the AWS OpenSearch migration.",
                    "confidence": 0.94,
                }
            ],
            "action_updates": [
                {
                    "owner": "Speaker_Beta Tharakan",
                    "action_text": "Complete AWS OpenSearch index mapping cutover before Friday.",
                    "extraction_confidence": 0.91,
                }
            ],
        }
    }
    package = create_artifact(
        "copilot-intake",
        "pkg-sherpa-sprint-0001",
        meeting={
            "meeting_id": "mtg-sherpa-sprint-0001",
            "title": "Project Beacon Sprint Review",
            "started_at": "2026-10-16T14:00:00Z",
            "organizer": "Andy Kilinskis",
            "attendees": ["Speaker_Beta Tharakan", "Egle Tamasevice"],
            "teams_reference": "teams://sherpa-sprint-review",
            "source_location": "test_sherpa_sprint_review_transcript.md",
            "source_hash": "b" * 64,
            "template_version": "1.0.0",
            "classification": "confidential",
            "audiences": ["engagement_team"],
        },
        speakers=[
            {
                "speaker_ref": "spk-ninan",
                "reported_name": "Speaker_Beta",
                "email": None,
                "role_or_org": "PartnerAdvisory",
                "statements": [
                    {
                        "statement_id": "stmt-ninan-01",
                        "kind": "technical",
                        "text": "I am leading the Elasticsearch to AWS OpenSearch migration and fixing the ingestion pipeline.",
                        "locator": {"kind": "timestamp", "value": "00:01:10"},
                        "copilot_confidence": 0.95,
                    }
                ],
            },
            {
                "speaker_ref": "spk-egle",
                "reported_name": "Egle",
                "email": None,
                "role_or_org": "Apex Global Enterprises",
                "statements": [
                    {
                        "statement_id": "stmt-egle-01",
                        "kind": "technical",
                        "text": "As the Apex Global Tech Lead on Project Beacon, I will review the agent-to-agent protocol design.",
                        "locator": {"kind": "timestamp", "value": "00:02:15"},
                        "copilot_confidence": 0.95,
                    }
                ],
            },
        ],
        actions=[],
        adapter={"name": "nee-copilot-intake", "version": "1.0.0"},
        extensions={"com.partner_advisory.nee.projection": projection},
    )

    bundle = normalize_copilot(package, run_context="wave3-e2e")

    src_ext = bundle["extensions"]["com.partner_advisory.nee.source"]
    evidence_quotes = src_ext.get("evidence_quotes") or {}
    assert evidence_quotes, "Expected evidence_quotes map in com.partner_advisory.nee.source extension"
    assert any("Elasticsearch to AWS OpenSearch migration" in q for q in evidence_quotes.values())

    # Verify first-name speaker "Speaker_Beta" and "Egle" in dialogue turns bound to declared stable_identifiers
    dialogue_candidates = [
        c for c in bundle["candidates"]
        if c["candidate_type"] == "assertion" and "Elasticsearch to AWS OpenSearch" in c["normalized_text"]
    ]
    assert dialogue_candidates, "Expected dialogue candidate for Speaker_Beta's turn"
    mentions = dialogue_candidates[0]["entity_mentions"]
    assert mentions[0]["stable_identifier"] == "entity-ninan"

    # Verify action_updates emitted a discrete action candidate
    action_candidates = [c for c in bundle["candidates"] if c["candidate_type"] == "action"]
    assert any("OpenSearch index mapping cutover" in c["normalized_text"] for c in action_candidates)


def test_per_speaker_coverage_healer_and_node_consolidation() -> None:
    """Verify heal_substantive_transcript_evidence covers every substantive speaker and _evaluate_fuzzy_matches merges 1-to-2 token names."""
    f1_text = (
        "## Section 4: Chronological Verbatim Transcript\n\n"
        "### [00:01:00]\n\n"
        "**Andy Kilinskis:** We reviewed the sprint backlog and velocity metrics for the current release.\n\n"
        "### [00:02:00]\n\n"
        "**Speaker_Beta Tharakan:** I completed the Elasticsearch to OpenSearch cluster migration and validated shard routing.\n\n"
        "### [00:03:00]\n\n"
        "**Egle Tamasevice:** We need strict schema contracts on the agent-to-agent protocol handoff.\n"
    )
    recon_data = {
        "evidence_index": {
            "ev-01": {
                "evidence_id": "ev-01",
                "speaker": "person:andy-kilinskis",
                "attribution_mode": "SPOKEN_DIALOGUE",
                "text": "We reviewed the sprint backlog and velocity metrics for the current release.",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
            }
        }
    }

    heal_substantive_transcript_evidence(recon_data, f1_text)
    speakers = {ev["speaker"] for ev in recon_data["evidence_index"].values()}
    assert "person:ninan-tharakan" in speakers
    assert "person:egle-tamasevice" in speakers

    # Verify node consolidation merges "Speaker_Beta" into "Speaker_Beta Tharakan" and "OpenSearch" into "Sherpa AWS OpenSearch Migration"
    nodes = [
        {"node_id": "entity-2a26", "node_type": "person", "canonical_name": "Speaker_Beta", "status": "candidate"},
        {"node_id": "entity-6a26", "node_type": "person", "canonical_name": "Speaker_Beta Tharakan", "status": "canonical"},
        {"node_id": "tech-opensearch", "node_type": "technical_anchor", "canonical_name": "OpenSearch", "status": "canonical"},
        {"node_id": "deliv-opensearch", "node_type": "deliverable", "canonical_name": "Sherpa AWS OpenSearch Migration", "status": "canonical"},
    ]
    norm_map = {n["node_id"]: normalize_name(n["canonical_name"]) for n in nodes}
    proposals = _evaluate_fuzzy_matches(nodes, norm_map, set(), {}, similarity_threshold=0.80)
    pair_sets = [{p["recommended_survivor_id"], p["recommended_retired_id"]} for p in proposals]
    assert {"entity-2a26", "entity-6a26"} in pair_sets
    assert {"tech-opensearch", "deliv-opensearch"} in pair_sets
