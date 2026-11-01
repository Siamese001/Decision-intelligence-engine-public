"""End-to-end and adversarial tests for Wave 4: Lineage Footer Source Resolution, Confidence Calibration & Answer Ledger Fix."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from dual_pipeline.answer_ledger import (
    _extract_query_metadata,
    get_default_answer_ledger,
    record_query_answer_safe,
)
from dual_pipeline.query_core.lineage_formatter import format_seamless_query_response
from tools.validate_query_output_format import validate_query_markdown


def test_lineage_formatter_resolves_human_readable_paths_and_rejects_superseded_as_confirmed() -> None:
    """Exact production regression: v31 superseded claim must not be badged CONFIRMED (100%) or emit HAS_CLAIM_TEXT."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave4_") as tmp_dir:
        db_path = Path(tmp_dir) / "test_graph.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT,
                canonical_uri TEXT,
                cold_archive_ref TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                currentness_state TEXT,
                primary_source_id TEXT,
                legacy_claim_id TEXT
            );
            CREATE TABLE claim (
                claim_id TEXT PRIMARY KEY,
                confidence TEXT,
                superseded_by TEXT
            );

            INSERT INTO node VALUES ('staffing_footprint_partner_advisory_nee', 'PartnerAdvisory NEE Staffing Footprint', 'staffing');
            INSERT INTO node VALUES ('org_nea', 'Apex Global Analytics (NEA)', 'organization');
            INSERT INTO node VALUES ('model_cohydrated_operating_model', 'Co-Hydrated Operating Model', 'operating_model');

            INSERT INTO source VALUES (
                'source_v33_partner_advisory_engineering_footprint_user_confirmed_2026_07_10',
                'User Confirmed PartnerAdvisory Engineering Footprint (2026-07-10)',
                'file:///tmp/footprint.md',
                NULL
            );
            INSERT INTO source VALUES (
                'source_user_hitl_consolidated_2026_07_10',
                'User HITL Consolidated Operating Model (2026-07-10)',
                'file:///tmp/hitl.md',
                NULL
            );

            -- Active canonical assertion
            INSERT INTO assertion VALUES (
                'asrt_edge_v33_66_nea',
                'staffing_footprint_partner_advisory_nee',
                'ALLOCATES_ENGINEERING_CAPACITY_TO',
                'org_nea',
                NULL,
                'PartnerAdvisory allocates 66 engineers to NEA',
                'confirmed',
                1.0,
                'canonical',
                1,
                'approved',
                'current',
                'source_v33_partner_advisory_engineering_footprint_user_confirmed_2026_07_10',
                NULL
            );

            -- Superseded v31 assertion (must get SUPERSEDED (0%), never CONFIRMED (100%))
            INSERT INTO assertion VALUES (
                'asrt_claim_claim_v31_cohydrated_model_selected',
                'model_cohydrated_operating_model',
                'HAS_CLAIM_TEXT',
                NULL,
                'Co-hydrated NEA-PartnerAdvisory Product + Engineering model',
                'Co-hydrated NEA-PartnerAdvisory Product + Engineering model',
                'confirmed',
                1.0,
                'candidate',
                0,
                'superseded',
                'historical',
                'source_user_hitl_consolidated_2026_07_10',
                'claim_v31_cohydrated_model_selected'
            );
            INSERT INTO claim VALUES ('claim_v31_cohydrated_model_selected', 'confirmed', 'claim_v35_cohydrated_model');
            """
        )
        conn.commit()
        conn.close()

        raw_md = (
            "PartnerAdvisory allocates 66 engineers to NEA (`asrt_edge_v33_66_nea`).\n"
            "Historical v31 claim (`asrt_claim_claim_v31_cohydrated_model_selected`)."
        )
        formatted = format_seamless_query_response(raw_md, graph_path=str(db_path))
        assert "HAS_CLAIM_TEXT" not in formatted.lineage_footer
        assert "PartnerAdvisory NEE Staffing Footprint" in formatted.lineage_footer
        assert "Apex Global Analytics (NEA)" in formatted.lineage_footer
        assert "User Confirmed PartnerAdvisory Engineering Footprint (2026-07-10)" in formatted.lineage_footer
        assert "CONFIRMED (100%)" in formatted.lineage_footer
        assert "SUPERSEDED (0%)" in formatted.lineage_footer

        # Validator rejects markdown whose lineage table cites a SUPERSEDED assertion
        is_valid, violations = validate_query_markdown(formatted.formatted_markdown)
        assert is_valid is False
        assert any("SUPERSEDED" in v for v in violations)


def test_validate_query_output_format_rejects_broken_user_footer() -> None:
    """Exact production regression: broken user lineage table with HAS_CLAIM_TEXT and trailing primary keys fails validation."""
    broken_md = """### Executive Question
At Apex Global Enterprises, where we have 106 software engineers [1][2].

---

### Evidence & Graph Lineage

| Ref | Graph Path | Confidence | Verified Evidence |
| :--- | :--- | :--- | :--- |
| `[1]` | `staffing_footprint_partner_advisory_nee` $\\rightarrow$ `ALLOCATES_ENGINEERING_CAPACITY_TO` $\\rightarrow$ `org_nea` | `CONFIRMED (100%)` | [graph.sqlite](file:///tmp/graph.sqlite) — `ev_legacy_edge_edge_v33_partner_advisory_footprint_allocates_66_to_nea` (`source_v33`) |
| `[2]` | `model_cohydrated_operating_model` $\\rightarrow$ `HAS_CLAIM_TEXT` $\\rightarrow$ `Co-hydrated model` | `CONFIRMED (100%)` | [graph.sqlite](file:///tmp/graph.sqlite) — `asrt_claim_claim_v31_cohydrated_model_selected` (`source_user`) |
"""
    is_valid, violations = validate_query_markdown(broken_md)
    assert is_valid is False
    assert any("HAS_CLAIM_TEXT" in v for v in violations)
    assert any("primary keys" in v for v in violations)


def test_answer_ledger_extracts_nested_citations_and_active_pair_provenance() -> None:
    """Exact production regression (row_id=952): nested DecisionService result records non-empty cited_assertion_ids, VERIFIED rigor, and graph_sha256."""
    nested_res = {
        "status": "DELIVERED",
        "result": {
            "query_type": "cross_workstream_synthesis",
            "x3_outcome": "X3_ALLOW",
            "formatted_markdown": "### Grounded Answer\n- PartnerAdvisory allocates 66 engineers to NEA [1].\n",
            "active_pair": {
                "generation_id": "generation-82c8ea9d374a222c7d153c192b9b",
                "graph_sha256": "a" * 64,
            },
            "answer": {
                "current_state": [
                    {
                        "statement": "PartnerAdvisory allocates 66 engineers to NEA.",
                        "citations": ["retrieval-record-66-nea"],
                        "evidence_ids": ["ev-66-nea"],
                        "lane": "approved",
                    }
                ],
                "opportunities": [],
                "pain_points": [],
                "guidance": [],
                "counterevidence": [],
            },
        },
    }
    cited_ids, resp_text, rigor, g_sha, gen, _lat, _mid = _extract_query_metadata(nested_res)
    assert cited_ids == ["retrieval-record-66-nea", "ev-66-nea"]
    assert rigor == "VERIFIED"
    assert g_sha == "a" * 64
    assert gen == "generation-82c8ea9d374a222c7d153c192b9b"
    assert resp_text.startswith("### Grounded Answer")

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_ledger_") as tmp_dir:
        rt_root = Path(tmp_dir)
        ans_id = record_query_answer_safe(
            rt_root,
            "How many engineers are allocated to NEA?",
            nested_res,
            query_type="cross_workstream_synthesis",
            latency_ms=42.5,
        )
        assert ans_id is not None
        ledger = get_default_answer_ledger(rt_root)
        saved = ledger.get_answer(ans_id)
        assert saved is not None
        assert saved.rigor == "VERIFIED"
        assert saved.cited_assertion_ids == ["retrieval-record-66-nea", "ev-66-nea"]
        assert saved.graph_sha256 == "a" * 64
        assert saved.generation == "generation-82c8ea9d374a222c7d153c192b9b"
