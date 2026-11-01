"""
tests/end_to_end/test_graph_role_confidence_erosion_e2e.py

End-to-End verification of Graph Role Confidence Integrity:
1. Multi-assertion GraphDB traversal ranking
2. Chronological HITL ledger resolution (latest overrides prior)
3. C0.6 Gate-to-roster synchronization
4. UWG role persistence and stale assertion supersession
5. Full Output 1-3 rendering and projection purification
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    _load_ledger_role_resolutions,
)
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    strip_org_suffix_from_role,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from dual_pipeline.transcript_stages_core.attribution_builder import (
    AttributionSummaryBuilder,
)
from dual_pipeline.transcript_qa_core.recap_healer import (
    _heal_output_2_placeholder_titles,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)
from storage.connection import create_sqlite_conn


def _seed_e2e_graph_db(db_path: Path) -> None:
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("CREATE TABLE node_type_registry (node_type_id TEXT PRIMARY KEY, registry_state TEXT NOT NULL)")
    for nt in ("stakeholder", "internal_person", "role_group", "org"):
        cur.execute("INSERT INTO node_type_registry VALUES (?, 'active')", (nt,))
    cur.execute("CREATE TABLE controlled_value_registry (vocabulary TEXT NOT NULL, value TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (vocabulary, value))")
    cur.execute("INSERT INTO controlled_value_registry VALUES ('evidence_relationship', 'supports', 1)")

    cur.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE VIRTUAL TABLE node_fts USING fts5(node_id UNINDEXED, canonical_name, description, aliases, node_type UNINDEXED)")
    cur.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_name TEXT NOT NULL, source_date TEXT, authority_rank INTEGER NOT NULL, source_posture TEXT NOT NULL, cold_archive_ref TEXT, notes TEXT, created_at TEXT NOT NULL)")
    cur.execute("CREATE TABLE source_version (source_version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version_number INTEGER NOT NULL, source_fingerprint TEXT NOT NULL, content_hash TEXT NOT NULL, source_date_normalized TEXT, source_date_precision TEXT NOT NULL, source_date_status TEXT NOT NULL, authority_rank INTEGER NOT NULL, scope_text TEXT NOT NULL, posture_text TEXT NOT NULL, created_at TEXT NOT NULL)")
    cur.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT NOT NULL, source_id TEXT NOT NULL, source_version_id TEXT NOT NULL, locator_kind TEXT NOT NULL, locator_value TEXT NOT NULL, direct_quote TEXT, evidence_directness TEXT NOT NULL, source_authority_rank INTEGER NOT NULL, extraction_confidence REAL NOT NULL, corroboration_group TEXT, location_status TEXT NOT NULL, created_at TEXT NOT NULL)")
    cur.execute(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL REFERENCES node(node_id),
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            confidence_score REAL NOT NULL,
            confidence_label TEXT,
            currentness_state TEXT NOT NULL,
            validation_state TEXT NOT NULL,
            valid_from TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            primary_source_id TEXT,
            canonical_status TEXT DEFAULT 'candidate',
            retrieval_eligible INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evidence_relationship TEXT NOT NULL, independent_corroboration INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, evidence_id))")

    # Seed source and evidence
    cur.execute("INSERT INTO source VALUES ('src-e2e', 'MEETING_TRANSCRIPT', 'Open Roles Discussion', '2026-10-16', 1, 'CONFIRMED', NULL, NULL, '2026-10-16T12:00:00Z')")
    cur.execute("INSERT INTO source_version VALUES ('sv-e2e', 'src-e2e', 1, 'fp-1', 'hash-1', '2026-10-16', 'DAY', 'NORMALIZED', 1, 'Scope', 'CONFIRMED', '2026-10-16T12:00:00Z')")
    cur.execute("INSERT INTO evidence_item VALUES ('ev-e2e-1', 'ev-fp-1', 'src-e2e', 'sv-e2e', 'line', '1', 'Amit Ayer confirmed as Principal Architect', 'DIRECT', 1, 1.0, NULL, 'VERIFIED', '2026-10-16T12:00:00Z')")

    # Seed node person_amit
    cur.execute(
        "INSERT INTO node VALUES ('person_amit', 'internal_person', 'Amit Ayer', 'Amit Ayer', 'PartnerAdvisory Architecture Lead', 1, '2026-08-28T00:00:00Z', '2026-10-16T12:00:00Z')"
    )
    cur.execute(
        "INSERT INTO node_fts VALUES ('person_amit', 'Amit Ayer', 'PartnerAdvisory Architecture Lead', 'Amit;Ayer', 'internal_person')"
    )

    # Seed competing assertions for person_amit:
    # 1. Historical lower-tier assertion (needs_review, 0.90) with compound/legacy role
    cur.execute(
        """
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            confidence_score, confidence_label, currentness_state, validation_state,
            valid_from, canonical_status, retrieval_eligible, created_at
        ) VALUES (
            'asrt-historical-1', 'person_amit', 'HAS_ROLE', NULL, 'Senior Director / Lead Enterprise Architect',
            0.90, 'HIGH', 'current', 'needs_review', '2026-08-28', 'candidate', 1, '2026-08-28T00:00:00Z'
        )
        """
    )

    # 2. Verified atomic assertion (verified, 1.0)
    cur.execute(
        """
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            confidence_score, confidence_label, currentness_state, validation_state,
            valid_from, canonical_status, retrieval_eligible, created_at
        ) VALUES (
            'asrt-verified-2', 'person_amit', 'HAS_ROLE', NULL, 'Principal Architect & Technology Strategist',
            1.0, 'AUTHORITATIVE', 'current', 'verified', '2026-10-12', 'canonical', 1, '2026-10-12T00:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()


def test_full_pipeline_graph_role_confidence_e2e(tmp_path: Path):
    """End-to-end verification across GraphDB traversal, HITL ledger, C0.6, UWG, and Output 1-3."""
    db_file = tmp_path / "canonical_graph.sqlite"
    _seed_e2e_graph_db(db_file)
    (tmp_path / "active_pair.json").write_text(json.dumps({"graph_path": "canonical_graph.sqlite"}), encoding="utf-8")

    # Stage 1: GraphDB Multi-Assertion Traversal Ranking
    resolver = PreSynthesisEntityResolver(repo_root=tmp_path, runtime_root=tmp_path)
    amit_ent = resolver.resolve("Amit Ayer")
    assert amit_ent is not None
    assert amit_ent.role == "Principal Architect & Technology Strategist"
    assert "/" not in amit_ent.role
    assert len(amit_ent.candidates) >= 1
    assert amit_ent.candidates[0].validation_state == "verified"

    # Stage 2: Chronological HITL Ledger Resolution
    from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA
    sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
    conn = create_sqlite_conn(sqlite_path, readonly=False)
    cur = conn.cursor()
    cur.executescript(HITL_TABLE_SCHEMA)
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-1', 1, 'dec-1', 'role_clarification', 'operator',
            'Prompt', '[]', 'Lead Client Partner / Principal Architect & Technology Strategist',
            'Lead Client Partner / Principal Architect & Technology Strategist', 'Earlier entry', '0000',
            '2026-10-10T10:00:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Lead Client Partner / Principal Architect & Technology Strategist"}', 'h1'
        )
        """
    )
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-2', 2, 'dec-2', 'role_clarification', 'operator',
            'Prompt', '[]', 'Principal Architect & Technology Strategist, PartnerAdvisory',
            'Principal Architect & Technology Strategist, PartnerAdvisory', 'Operator confirmed', 'h1',
            '2026-10-16T15:30:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Principal Architect & Technology Strategist, PartnerAdvisory"}', 'h2'
        )
        """
    )
    conn.commit()
    conn.close()

    init_meta = {
        "repo_root": str(tmp_path),
        "roster": ["Amit Ayer"],
    }
    hitl_decisions = _load_ledger_role_resolutions(init_meta)
    assert hitl_decisions["Amit Ayer"] == "Principal Architect & Technology Strategist"

    # Stage 3: C0.6 Gate-to-Roster Synchronization
    metadata: Dict[str, Any] = {
        "repo_root": str(tmp_path),
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Client Partner / Principal Architect & Technology Strategist", "org": "PartnerAdvisory"},
            {"name": "Megan Orzell", "role": "Client Partner", "org": "PartnerAdvisory"},
        ],
        "attendees": {
            "PartnerAdvisory": [
                "Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)",
                "Megan Orzell (Client Partner)",
            ]
        },
        "hitl_role_resolutions": {
            "Amit Ayer": {"resolved_role": hitl_decisions["Amit Ayer"], "provenance": "HITL_LEDGER"}
        },
    }
    gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    l0_context = {"metadata": metadata}
    gate.execute(l0_context)

    # Verify post-gate metadata synchronization
    assert metadata["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert "Principal Architect & Technology Strategist" in metadata["attendees"]["PartnerAdvisory"][0]
    assert "Lead Client Partner /" not in metadata["attendees"]["PartnerAdvisory"][0]

    # Stage 4: UWG Persistence and Supersession
    uwg_receipt = persist_validated_role(
        graph_db=db_file,
        canonical_name="Amit Ayer",
        selected_role="Principal Architect & Technology Strategist",
    )
    assert uwg_receipt["status"] in ("SUCCESS", "PERSISTED_SSOT")

    # Verify database state after UWG write
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.assertion_id, COALESCE(n.canonical_name, a.object_literal), a.validation_state, a.superseded_by_assertion_id
        FROM assertion a
        LEFT JOIN node n ON a.object_node_id = n.node_id
        WHERE a.subject_node_id='person_amit'
        """
    )
    rows = cur.fetchall()
    conn.close()

    # The historical assertion should be superseded
    historical = next(r for r in rows if r[0] == "asrt-historical-1")
    assert historical[3] is not None  # superseded_by_assertion_id is set

    # The canonical assertion is active and verified
    active_canonical = [r for r in rows if r[1] == "Principal Architect & Technology Strategist" and r[2] == "verified"]
    assert len(active_canonical) >= 1

    # Stage 5: Output 1 Attribution Summary Rendering
    summary = AttributionSummaryBuilder.build_summary(
        metadata=metadata,
        dialogue_text="Amit Ayer: Reviewing the open staffing profiles.",
    )
    assert "Amit Ayer | PartnerAdvisory | Principal Architect & Technology Strategist" in summary
    assert "Lead Client Partner /" not in summary

    # Stage 6: Output 2 Executive Recap Rendering & Healing
    raw_output2 = (
        "## Attendees\n"
        "- PartnerAdvisory: Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)\n\n"
        "# Appendix: Detailed Stakeholder Perspectives\n\n"
        "# Amit Ayer — PartnerAdvisory, Lead Client Partner / Principal Architect & Technology Strategist\n"
        "Spoken perspectives.\n"
    )
    healed_output2 = _heal_output_2_placeholder_titles(raw_output2, metadata, [], [])
    assert "- PartnerAdvisory: Amit Ayer (Principal Architect & Technology Strategist)" in healed_output2
    assert "# Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist" in healed_output2
    assert "Lead Client Partner /" not in healed_output2

    # Stage 7: Output 3 Graph Projection Reconciler
    final_data: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:amit_ayer",
                    "name": "Amit Ayer",
                    "type": "person",
                    "role": "Lead Client Partner / Principal Architect & Technology Strategist",
                    "description": "Amit Ayer is Lead Client Partner / Principal Architect & Technology Strategist.",
                }
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-old",
                    "from_id": "person:amit_ayer",
                    "to_id": "role:lead_client_partner",
                    "relationship": "HAS_ROLE",
                }
            ],
            "proposal_updates": [
                {
                    "proposal_id": "p-1",
                    "lineage": {
                        "speaker": "person:amit-ayer",
                        "speaker_role": "Lead Client Partner / Principal Architect & Technology Strategist",
                    },
                }
            ],
        }
    }
    reconciled = reconcile_speaker_role_resolutions(final_data, metadata)

    # Verify purified entities, signals, and lineage
    ent = reconciled["graph_mutations"]["entity_upserts"][0]
    assert ent["role"] == "Principal Architect & Technology Strategist"
    assert "Lead Client Partner /" not in ent["description"]

    signals = reconciled["graph_mutations"]["relationship_signals"]
    amit_role_signals = [
        s for s in signals
        if s.get("from_id") in ("person:amit_ayer", "person:amit-ayer")
        and s.get("relationship") == "HAS_ROLE"
    ]
    assert len(amit_role_signals) >= 1
    sig_targets = [s["to_id"] for s in amit_role_signals]
    assert any("principal-architect" in t for t in sig_targets)
    assert not any(s.get("to_id") == "role:lead_client_partner" for s in signals)

    prop = reconciled["graph_mutations"]["proposal_updates"][0]
    assert prop["lineage"]["speaker_role"] == "Principal Architect & Technology Strategist"
