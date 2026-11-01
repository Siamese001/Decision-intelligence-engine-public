"""End-to-End test suite for seamless query response and lineage decoupling."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dual_pipeline.query_core.lineage_formatter import (
    LINEAGE_SECTION_HEADER,
    format_seamless_query_response,
    validate_seamless_prose,
)


partner_advisory_LENS_REPRESENTATIVE_INPUT = """
### PartnerAdvisory Product Owner Definition

In PartnerAdvisory’s co-hydrated and Adaptive AI-PDLC models, the **Product Owner / Solutions Lead** owns the product vision, roadmap, and measurable business ROI, adhering to **product leadership over feature ordering** ([`ast_ing_997d4d3ff6060cf435145be7c5d3`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite), [`asrt_claim_claim_v42_cohydrated_principle_product_leadership_over_feature_ordering`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)). Rather than performing passive backlog management, the PO actively validates Definition of Done, field evidence, and outcomes against user value ([`asrt_v50_d809d833603926345fa92b65e0e875`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)). Account lessons demonstrate that lacking client PO continuity forces engineers to infer product intent ([`asrt_claim_claim_v21_sherpa_po_success_factor`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)).

### Current LENS Role & Scope

Project LENS ([`workstream_lens`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)) is an image-to-Maximo nameplate extraction pipeline ([`sow_lens_visual_asset_intelligence`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)). Technically, it is PartnerAdvisory-led by Tech Lead Alex Sanchez ([`asrt_edge_31bb9fe2fb7d631498349d5c`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)). PartnerAdvisory PM transitioned from Tony Kablan to Delivery Lead Jen Pan without handover ([`asrt_tony_kablan_lens_role`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite), [`asrt_amy_moore_transition_statement`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)). However, LENS currently **lacks formal client product ownership or sign-off authority** ([`asrt_claim_claim_v36_lens_partner_advisory_led_2026_06_29`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)); business requirements are informally steered by Project Atlas users Alexandra Coro and Ed Moore ([`assertion-293988b91068657fc7fc307d925e`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)).

### Application to LENS & Graph Lineage

Applying PartnerAdvisory's PO definition to LENS requires an accountable client PO to:
1. **Bridge Technical DoD and Business Rules**: Reconcile Alex Sanchez's 85% OCR/VLM confidence metric ([`assertion-ad6c0c573df60643fb14ac38da31`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)) with Atlas human-in-the-loop rule creation ([`assertion-c9f9a0c466a295ad88f2d6ef6de7`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)).
2. **Prevent Permissive Pollution**: Guard against overly permissive extraction rules that auto-approve invalid serial numbers into Maximo ([`assertion-b0fbf604fb032a7c7cf83e5617ad`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)).
3. **Govern Milestone Boundaries**: Formally arbitrate SOW 1 phase-completion gates and SOW 2 Maximo writeback dependencies ([`assertion-6bbb547c4d863c238ef1aa41153e`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite), [`assertion-b8ac690ff6aad2c4849427c51f8c`](file:///Users/amitayer/Git/decision-intelligence-engine/.local/dual_pipeline/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite)).
"""


def test_partner_advisory_lens_query_response_e2e() -> None:
    """Verifies that an 11-assertion response is transformed into clean prose with all citations in the footer."""
    result = format_seamless_query_response(
        partner_advisory_LENS_REPRESENTATIVE_INPUT,
        graph_path="/storage/generations/generation-e18bbb4bfcfd34bb16fd09a9dd63/graph.sqlite",
    )

    # 1. Clean prose validation
    is_valid, violations = validate_seamless_prose(result.clean_prose)
    assert is_valid, f"Prose validation failed: {violations}"
    assert "ast_ing_997d4d3f" not in result.clean_prose
    assert "asrt_claim_" not in result.clean_prose
    assert "workstream_lens" not in result.clean_prose
    assert "file:///" not in result.clean_prose

    # 2. Footnote markers stripped (external-ready prose)
    assert "[1" not in result.clean_prose
    assert "[2" not in result.clean_prose
    assert "[3" not in result.clean_prose
    assert "[" not in result.clean_prose

    # 3. Footer lineage section
    assert LINEAGE_SECTION_HEADER in result.lineage_footer
    assert "**Graph Traversal Summary:**" in result.lineage_footer
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in result.lineage_footer
    assert all(it.confidence for it in result.citations)

    # 4. Verify all expected assertion IDs are preserved in footer
    expected_ids = [
        "ast_ing_997d4d3ff6060cf435145be7c5d3",
        "asrt_claim_claim_v42_cohydrated_principle_product_leadership_over_feature_ordering",
        "asrt_v50_d809d833603926345fa92b65e0e875",
        "asrt_claim_claim_v21_sherpa_po_success_factor",
        "workstream_lens",
        "sow_lens_visual_asset_intelligence",
        "asrt_edge_31bb9fe2fb7d631498349d5c",
        "asrt_tony_kablan_lens_role",
        "asrt_amy_moore_transition_statement",
        "asrt_claim_claim_v36_lens_partner_advisory_led_2026_06_29",
        "assertion-293988b91068657fc7fc307d925e",
        "assertion-ad6c0c573df60643fb14ac38da31",
        "assertion-c9f9a0c466a295ad88f2d6ef6de7",
        "assertion-b0fbf604fb032a7c7cf83e5617ad",
        "assertion-6bbb547c4d863c238ef1aa41153e",
        "assertion-b8ac690ff6aad2c4849427c51f8c",
    ]
    for exp_id in expected_ids:
        assert exp_id in result.citation_index_map or any(exp_id in it.raw_id for it in result.citations), (
            f"Expected ID {exp_id} missing from decoupled citations!"
        )


def test_sqlite_confidence_resolution_and_note_readability_e2e(tmp_path: Any) -> None:
    """Verifies SQLite confidence resolution and conversion of dense parentheticals/acronyms into readable notes."""
    from storage.connection import get_db_connection

    db_file = tmp_path / "test_graph.sqlite"
    conn = get_db_connection(db_file, readonly=False)
    try:
        conn.execute(
            "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, confidence_label TEXT, confidence_score REAL)"
        )
        conn.execute(
            "CREATE TABLE claim (claim_id TEXT PRIMARY KEY, confidence REAL)"
        )
        conn.execute(
            "INSERT INTO assertion VALUES ('assertion-aaaa111122223333', 'HIGH', 0.94)"
        )
        conn.execute(
            "INSERT INTO assertion VALUES ('assertion-bbbb444455556666', 'MEDIUM', 0.72)"
        )
        conn.execute(
            "INSERT INTO claim VALUES ('claim_lens_ocr_eval', 0.48)"
        )
        conn.commit()
    finally:
        conn.close()

    raw_note = (
        "- Apply CLAHE and VLM ensemble checks (e.g., Textract alongside vision models) "
        "([`assertion-aaaa111122223333`](file:///tmp/test_graph.sqlite)).\n"
        "- Filter noisy tokens (serial drops, revision suffixes) "
        "([`assertion-bbbb444455556666`](file:///tmp/test_graph.sqlite), `claim_lens_ocr_eval`)."
    )
    res = format_seamless_query_response(raw_note, graph_path=str(db_file))
    assert "adaptive contrast enhancement" in res.clean_prose
    assert "Vision Language Model" in res.clean_prose
    assert "— such as Textract alongside vision models" in res.clean_prose
    assert "— serial drops, revision suffixes" in res.clean_prose
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in res.lineage_footer
    assert "**Graph Traversal Summary:**" in res.lineage_footer
    assert "HIGH (94%)" in res.lineage_footer
    assert "MEDIUM (72%)" in res.lineage_footer
    assert "LOW (48%)" in res.lineage_footer


def test_sequential_reference_numbering_e2e() -> None:
    """Verifies that footnote references are cleanly stripped from prose while citations register sequentially."""
    input_text = "Claim A (ast_ing_111). Claim B (ast_ing_222). Claim C (ast_ing_333)."
    res = format_seamless_query_response(input_text)
    assert res.clean_prose == "Claim A. Claim B. Claim C."
    assert len(res.citations) == 3
    assert res.citations[0].index == 1
    assert res.citations[1].index == 2
    assert res.citations[2].index == 3
    assert "**Graph Traversal Summary:**" in res.lineage_footer


def test_idempotent_formatting_e2e() -> None:
    """Verifies that re-formatting an already formatted response is idempotent and safe."""
    first_pass = format_seamless_query_response(partner_advisory_LENS_REPRESENTATIVE_INPUT)
    second_pass = format_seamless_query_response(first_pass.formatted_markdown)
    assert second_pass.clean_prose == first_pass.clean_prose
    assert len(second_pass.citations) == len(first_pass.citations)
    assert "**Graph Traversal Summary:**" in second_pass.lineage_footer


def test_cli_validator_e2e() -> None:
    """Verifies that validate_query_output_format mechanical validator correctly gates format."""
    from tools.validate_query_output_format import validate_query_markdown

    # 1. Raw legacy input fails validation
    is_valid_raw, violations_raw = validate_query_markdown(partner_advisory_LENS_REPRESENTATIVE_INPUT)
    assert not is_valid_raw, "Raw legacy response must fail validation"
    assert len(violations_raw) > 0

    # 2. Formatted output with valid assertions passes validation
    sample_valid_input = """### PartnerAdvisory Product Owner Definition
In PartnerAdvisory models, the PO owns the product vision ([`ast_ing_00112233445566778899aabbccddeeff`](file:///path/to/graph.sqlite)).
Project LENS ([`workstream_lens`](file:///path/to/graph.sqlite)) is led by Alex Sanchez.
"""
    formatted = format_seamless_query_response(sample_valid_input)
    is_valid_fmt, violations_fmt = validate_query_markdown(formatted.formatted_markdown)
    assert is_valid_fmt, f"Formatted response must pass validation: {violations_fmt}"
    assert len(violations_fmt) == 0


def test_nee_mcp_server_query_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that nee-knowledge-graph MCP server routes nee_query through --human by default."""
    from tools.nee_mcp_server import nee_query

    captured_args: list[str] = []

    def mock_run_cli(args: list[str], timeout: int = 120) -> str:
        nonlocal captured_args
        captured_args = list(args)
        return "MOCK_SUCCESS"

    monkeypatch.setattr("tools.nee_mcp_server._run_cli_command", mock_run_cli)

    # Invoke with default human=True
    res = nee_query(question="What is PartnerAdvisory's PO definition?")
    assert res == "MOCK_SUCCESS"
    assert "--human" in captured_args

    # Invoke with human=False
    captured_args.clear()
    res_raw = nee_query(question="What is PartnerAdvisory's PO definition?", human=False)
    assert res_raw == "MOCK_SUCCESS"
    assert "--human" not in captured_args


def test_response_envelope_unwrapping_e2e() -> None:
    """Verifies that _apply_seamless_lineage_formatting unwrap nested result.answer in decision envelopes."""
    from dual_pipeline.cli.engine import _apply_seamless_lineage_formatting

    mock_envelope = {
        "artifact_id": "decision-response-envelope-test",
        "contract_id": "urn:nee:dual-pipeline:decision-response-envelope:1.0.0",
        "destination": "GRAPH_EVIDENCE_UTILITY",
        "result": {
            "answer": {
                "current_state": [
                    {
                        "statement": "Project LENS is active under leadership",
                        "citations": ["workstream_lens", "asrt_claim_lens_lead"],
                    }
                ],
                "opportunities": [],
                "pain_points": [],
                "guidance": [],
                "hypotheses": [],
                "missing_information": [],
            },
            "x3_outcome": "X3D_ALLOW_FINISH",
        },
    }

    _apply_seamless_lineage_formatting(mock_envelope)

    assert "formatted_markdown" in mock_envelope
    assert "clean_prose" in mock_envelope
    assert "lineage_footer" in mock_envelope
    assert "[1]" not in mock_envelope["clean_prose"]
    assert "workstream_lens" not in mock_envelope["clean_prose"]
    assert "**Graph Traversal Summary:**" in mock_envelope["lineage_footer"]
    assert LINEAGE_SECTION_HEADER in mock_envelope["lineage_footer"]
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in mock_envelope["lineage_footer"]
    assert mock_envelope["result"]["formatted_markdown"] == mock_envelope["formatted_markdown"]

