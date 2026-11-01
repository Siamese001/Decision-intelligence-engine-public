"""Wave 4 E2E & Adversarial Tests: L4 Judge Independence & Sidecar Vector Deduplication."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge


def test_graph_judge_removes_fixed_score_prompt_injection() -> None:
    """Verify GraphExtractionJudge source contains no max_mutation_budget==450 or fixed-score bypasses."""
    judge_file = Path(__file__).resolve().parents[1] / "dual_pipeline" / "llm_judge_core" / "judges" / "graph.py"
    source = judge_file.read_text(encoding="utf-8")
    assert "max_mutation_budget" not in source or "450" not in source
    assert "Award full 15/15" not in source
    assert "award full 20/20" not in source
    assert "Award full 12/12" not in source
    assert "Award full 8/8" not in source

    judge = GraphExtractionJudge()
    assert judge is not None


def test_sidecar_deduplicates_sibling_slide_quote_hashes(tmp_path: Path) -> None:
    """Verify sibling candidate assertions sharing an identical >200-char slide body receive distinct retrieval_text_hash."""
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            entity_type TEXT,
            canonical_status TEXT DEFAULT 'candidate'
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            assertion_type TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            currentness_state TEXT,
            validation_state TEXT,
            confidence_score REAL,
            retrieval_eligible INTEGER,
            primary_source_id TEXT,
            qualifiers_json TEXT,
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["internal_delivery"]',
            origin_type TEXT DEFAULT 'document_extraction',
            extraction_run_id TEXT DEFAULT 'run-1',
            hitl_required INTEGER DEFAULT 0,
            validation_priority TEXT DEFAULT 'P2',
            validation_owner TEXT DEFAULT 'owner',
            atomicity_state TEXT DEFAULT 'atomic',
            valid_from TEXT DEFAULT '2026-08-26',
            valid_to TEXT DEFAULT NULL
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            assertion_id TEXT,
            object_type TEXT,
            title TEXT,
            body TEXT
        );
        CREATE TABLE source_registry (
            source_id TEXT PRIMARY KEY
        );
        """
    )
    shared_slide_quote = (
        "Slide 19 Target State Provisioning Architecture: Self-service workflow routes from "
        "Apex Global SAGE and IT4U into Backstage Developer Portal, invoking the Infrastructure "
        "Automation Factory, GitHub Enterprise, and Terraform Enterprise to provision GCP and AWS resources."
    )
    assert len(shared_slide_quote) > 200

    conn.execute("INSERT INTO source_registry VALUES ('source-doc-orals')")
    conn.execute("INSERT INTO node VALUES ('system:sage', 'Apex Global SAGE', 'system', 'candidate')")
    conn.execute("INSERT INTO node VALUES ('system:it4u', 'ServiceNow IT4U', 'system', 'candidate')")
    conn.execute("INSERT INTO node VALUES ('system:backstage', 'Backstage Portal', 'system', 'candidate')")

    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            scope_text, assertion_type, canonical_status, canonical_eligible,
            currentness_state, validation_state, confidence_score, retrieval_eligible,
            primary_source_id, qualifiers_json
        ) VALUES (
            'asrt-1', 'system:sage', 'ROUTES_TO', 'system:it4u', NULL,
            'SAGE routes intake tickets to IT4U.', 'relationship', 'candidate', 1,
            'current', 'unreviewed', 0.9, 1, 'source-doc-orals', '{}'
        )"""
    )
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            scope_text, assertion_type, canonical_status, canonical_eligible,
            currentness_state, validation_state, confidence_score, retrieval_eligible,
            primary_source_id, qualifiers_json
        ) VALUES (
            'asrt-2', 'system:it4u', 'TRIGGERS', 'system:backstage', NULL,
            'IT4U triggers Backstage scaffolding.', 'relationship', 'candidate', 1,
            'current', 'unreviewed', 0.9, 1, 'source-doc-orals', '{}'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-slide-19', 'supports')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-2', 'ev-slide-19', 'supports')")
    conn.execute(
        "INSERT INTO search_document (assertion_id, object_type, title, body) VALUES ('asrt-1', 'assertion', 'SAGE to IT4U', ?)",
        (shared_slide_quote,),
    )
    conn.execute(
        "INSERT INTO search_document (assertion_id, object_type, title, body) VALUES ('asrt-2', 'assertion', 'IT4U to Backstage', ?)",
        (shared_slide_quote,),
    )
    conn.commit()
    conn.close()

    records = render_retrieval_records(db_path, "snap-1")
    assertion_records = [r for r in records if r["record_type"] in ("assertion", "edge", "architecture_relationship")]
    assert len(assertion_records) == 2
    hashes = {r["retrieval_text_hash"] for r in assertion_records}
    assert len(hashes) == 2, f"Expected 2 unique retrieval_text_hash values for sibling slide assertions, got {hashes}"
