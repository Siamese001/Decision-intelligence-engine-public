"""End-to-End tests for Wave 5: Evidence Envelope Packaging, Intake Engine U0-L4 & Governed Graph Mutations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.cli.ingest_resolve_cmd import extract_package_deltas_and_digests
from dual_pipeline.core import seal_artifact
from dual_pipeline.intake_core.change_set_helpers import _select_high_confidence_existing_entity
from dual_pipeline.normalization import normalize_evidence_envelope
from tools.prepare_evidence_envelope_intake import _adapt_v53_projection_to_envelope


def test_e2e_v57_projection_to_envelope_preserves_kinds_and_multi_evidence(tmp_path: Path) -> None:
    """Verify Output 3 v5.7 projection preserves statement kinds, classifications, and multi-evidence IDs."""
    src_path = tmp_path / "01_transcript.md"
    src_path.write_text(
        "**Alice Smith:** We decided to adopt the unified ingestion schema across all workstreams.\n"
        "**Bob Jones:** We propose migrating the vector index before Q3.\n",
        encoding="utf-8",
    )
    proj: dict[str, Any] = {
        "schema_version": "5.7",
        "meeting": {
            "meeting_id": "meeting-wave5-001",
            "title": "Architecture Alignment",
            "date": "2026-04-06",
            "classification": "confidential",
            "audiences": ["internal"],
            "source_location": "local://01_transcript.md",
            "source_hash": "a" * 64,
            "attendees": ["Alice Smith", "Bob Jones"],
        },
        "evidence_index": {
            "stmt-01": {
                "speaker": "Alice Smith",
                "exact_text": "We decided to adopt the unified ingestion schema across all workstreams.",
                "kind": "decision",
                "source_classification": "decision",
                "locator": {"kind": "timestamp", "value": "00:01"},
            },
            "stmt-02": {
                "speaker": "Bob Jones",
                "exact_text": "We propose migrating the vector index before Q3.",
                "source_classification": "proposal",
                "locator": {"kind": "timestamp", "value": "00:02"},
            },
        },
        "action_updates": [
            {
                "action_id": "act-01",
                "owner": "Alice Smith",
                "action_text": "Finalize schema migration checklist and share with engineering.",
                "status": "open",
                "evidence_ids": ["stmt-01", "stmt-02"],
            }
        ],
        "entity_upserts": [
            {
                "id": "person:alice-smith",
                "entity_id": "person:alice-smith",
                "canonical_name": "Alice Smith",
                "name": "Alice Smith",
                "entity_type": "person",
                "mutation": "UPDATE",
                "evidence_ids": ["stmt-01"],
            },
            {
                "id": "org:lead-architect",
                "entity_id": "org:lead-architect",
                "canonical_name": "Lead Architect",
                "name": "Lead Architect",
                "entity_type": "organization",
                "mutation": "CREATE",
                "evidence_ids": ["stmt-01"],
            },
        ],
        "relationship_signals": [
            {
                "id": "rel-01",
                "from_id": "person:alice-smith",
                "to_id": "org:lead-architect",
                "subject_id": "person:alice-smith",
                "predicate": "HAS_ROLE_IN",
                "relation_type": "HAS_ROLE_IN",
                "object_id": "org:lead-architect",
                "rationale": "Alice Smith serves as Lead Architect for the platform.",
                "evidence_ids": ["stmt-01"],
            }
        ],
    }

    pkg = _adapt_v53_projection_to_envelope(proj, source_path=src_path)
    pkg["meeting"]["source_hash"] = "a" * 64
    pkg["meeting"]["source_location"] = "local://01_transcript.md"
    pkg["meeting"]["classification"] = "confidential"
    pkg["meeting"]["audiences"] = ["internal"]
    pkg = seal_artifact(pkg)

    alice_stmts = [
        s for spk in pkg["speakers"] if spk["reported_name"] == "Alice Smith" for s in spk["statements"]
    ]
    assert alice_stmts and alice_stmts[0]["kind"] == "decision"
    assert pkg["extensions"]["com.partner_advisory.nee.statement-classifications"]["stmt-02"] == "proposal"
    assert pkg["extensions"]["com.partner_advisory.nee.action-evidence-ids"]["action-act-01"] == ["stmt-01", "stmt-02"]

    # Normalize the package through normalize_evidence_envelope and verify zero synthetic lens/atlas injection
    bundle = normalize_evidence_envelope(pkg)
    all_workstreams = {
        ws
        for cand in bundle["candidates"]
        for ws in (cand.get("workstreams") or [])
    }
    assert "lens" not in all_workstreams
    assert "atlas" not in all_workstreams

    # Verify action candidate retained multiple evidence_ids (action evidence + stmt-01 + stmt-02)
    action_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "action"]
    assert len(action_cands) >= 1
    assert len(action_cands[0]["evidence_ids"]) >= 3

    # Verify proposal statement preserved assertion_type="proposal"
    proposal_cands = [c for c in bundle["candidates"] if c.get("assertion_type") == "proposal"]
    assert len(proposal_cands) == 1

    # Verify entity intent profile preserved mutation_intent="UPDATE" and role hydration matched HAS_ROLE_IN
    profiles = bundle["extensions"].get("com.partner_advisory.nee.entity-intent-profiles", {})
    assert any(p.get("mutation_intent") == "UPDATE" for p in profiles.values())
    edge_traits = bundle["extensions"].get("com.partner_advisory.nee.edge-traits", {})
    assert any(
        t.get("relationship_predicate") == "HAS_ROLE_IN" and t.get("assertion_type") == "speaker_role"
        for t in edge_traits.values()
    )


def test_e2e_single_token_entity_selection_and_action_delta_dedup(tmp_path: Path) -> None:
    """Verify single-token high-confidence existing entity selection and deduplicated action delta counts."""
    res_single_token = {
        "outcome": "AMBIGUOUS_REQUIRES_HITL",
        "reported_name": "Snowflake",
        "alternatives": [
            {
                "entity_id": "entity-technical_anchor-snowflake",
                "match_score": 0.94,
                "margin_over_next": 0.35,
                "identity_conflicts": [],
                "supporting_context": ["Reported tokens occur in the canonical name"],
            }
        ],
    }
    assert _select_high_confidence_existing_entity(res_single_token) == "entity-technical_anchor-snowflake"

    res_auto_high = {
        "outcome": "AUTO_LINK_HIGH_CONFIDENCE",
        "selected_entity_id": "entity-program-001",
        "reported_name": "NEE",
    }
    assert _select_high_confidence_existing_entity(res_auto_high) == "entity-program-001"

    # Verify extract_package_deltas_and_digests deduplicates actions present in both top-level and projection
    pkg_file = tmp_path / "intake_package.json"
    pkg_payload = {
        "schema_id": "https://consulting.example.com/nee/schemas/evidence-envelope-intake.schema.json",
        "schema_version": "1.0.0",
        "package_id": "pkg-001",
        "meeting": {"meeting_id": "m-001", "source_hash": "a" * 64},
        "actions": [{"action_id": "action-01", "summary": "Deliver architecture deck"}],
        "extensions": {
            "com.partner_advisory.nee.canonical-signal-projection": {
                "entity_upserts": [{"name": "Alice"}],
                "action_updates": [{"action_id": "01", "summary": "Deliver architecture deck"}],
                "decision_updates": [{"decision_id": "dec-01"}],
            }
        },
    }
    pkg_file.write_text(json.dumps(pkg_payload), encoding="utf-8")
    deltas, src_dig, _ = extract_package_deltas_and_digests(pkg_file)
    assert src_dig == "a" * 64
    assert deltas["node"] == 1
    assert deltas["assertion"] == 2  # 1 deduplicated action + 1 decision (not 3!)
