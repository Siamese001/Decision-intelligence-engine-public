"""Adversarial and edge-case test suite for query lineage formatting."""

from __future__ import annotations

import pytest

from dual_pipeline.query_core.lineage_formatter import (
    LINEAGE_SECTION_HEADER,
    format_seamless_query_response,
    validate_seamless_prose,
)


def test_adversarial_boundary_empty_and_whitespace() -> None:
    """Category: Boundary payload - handles empty, whitespace, and citation-free inputs safely."""
    res_empty = format_seamless_query_response("")
    assert res_empty.clean_prose == ""
    assert res_empty.lineage_footer == ""
    assert res_empty.citations == []

    res_spaces = format_seamless_query_response("   \n\n   ")
    assert res_spaces.clean_prose == ""
    assert res_spaces.lineage_footer == ""

    no_citations = "This is a simple query answer with absolutely no database tokens."
    res_plain = format_seamless_query_response(no_citations)
    assert res_plain.clean_prose == no_citations
    assert res_plain.lineage_footer == ""
    assert res_plain.citations == []


def test_adversarial_malformed_bracket_inputs() -> None:
    """Category: Malformed input - handles unclosed brackets, broken markdown links, and corrupted URIs."""
    malformed_bracket = "This claim has an unclosed bracket ([ast_ing_1234567890abcdef and text."
    res_bracket = format_seamless_query_response(malformed_bracket)
    assert "[1]" not in res_bracket.clean_prose
    assert "ast_ing_1234567890abcdef" not in res_bracket.clean_prose
    assert len(res_bracket.citations) == 1
    assert res_bracket.citations[0].raw_id == "ast_ing_1234567890abcdef"

    broken_link = "Another claim with [broken](ast_ing_abcdef1234567890) markdown link syntax."
    res_link = format_seamless_query_response(broken_link)
    assert "[1]" not in res_link.clean_prose
    assert "[" not in res_link.clean_prose
    assert len(res_link.citations) == 1

    corrupted_uri = "Testing raw uri ([`asrt_claim_999`](file:///path/with%20spaces/and[brackets]/graph.sqlite))."
    res_uri = format_seamless_query_response(corrupted_uri)
    assert "file:///" not in res_uri.clean_prose
    assert "[1]" not in res_uri.clean_prose
    assert "[" not in res_uri.clean_prose


def test_adversarial_invariant_bypass_and_leakage_detection() -> None:
    """Category: Invariant bypass attempt - detects and rejects inline assertion hash leaks."""
    leaked_prose = "The PO model (ast_ing_1234567890abcdef) is applied directly to workstream_lens."
    is_valid, violations = validate_seamless_prose(leaked_prose)
    assert not is_valid
    assert any("assertion ID" in v for v in violations)
    assert any("node ID" in v for v in violations)

    formatted = format_seamless_query_response(leaked_prose)
    is_now_valid, remaining_violations = validate_seamless_prose(formatted.clean_prose)
    assert is_now_valid, f"Sanitization failed to strip leaked tokens: {remaining_violations}"
    assert "ast_ing_1234567890abcdef" not in formatted.clean_prose
    assert "workstream_lens" not in formatted.clean_prose
    assert "[1]" not in formatted.clean_prose
    assert "[2]" not in formatted.clean_prose
    assert "[" not in formatted.clean_prose
    assert "**Graph Traversal Summary:**" in formatted.lineage_footer


def test_adversarial_overflow_massive_citation_set() -> None:
    """Category: Overflow & exhaustion - formats and deduplicates 100+ distinct assertions without degradation."""
    claims = []
    for i in range(120):
        claims.append(f"Fact {i} verifies point {i} ([ast_ing_{i:04d}abcdef1234567890]).")
    large_input = "\n\n".join(claims)

    res = format_seamless_query_response(large_input)
    assert len(res.citations) == 120
    is_valid, violations = validate_seamless_prose(res.clean_prose)
    assert is_valid, f"Massive citation set failed prose validation: {violations}"
    assert "[1]" not in res.clean_prose
    assert "[120]" not in res.clean_prose
    assert "[" not in res.clean_prose
    assert LINEAGE_SECTION_HEADER in res.lineage_footer
    assert "**Graph Traversal Summary:** 120 assertions evaluated" in res.lineage_footer
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in res.lineage_footer


def test_adversarial_legacy_three_column_footer_upgrade_and_corrupt_db(tmp_path: pytest.TempPathFactory) -> None:
    """Category: Rollback & recovery / Malformed input - upgrades 3-col legacy footers and handles corrupt DB files."""
    corrupt_db = tmp_path / "corrupt.sqlite"
    corrupt_db.write_bytes(b"NOT_A_VALID_SQLITE_HEADER_12345")

    legacy_md = (
        "- Key note [1]\n\n"
        "### Evidence & Graph Lineage\n\n"
        "| Ref | Graph Path | Verified Evidence |\n"
        "| :--- | :--- | :--- |\n"
        "| **`[1]`** | Workstream: Lens | [workstream_lens](file:///tmp/corrupt.sqlite) |\n"
    )
    upgraded = format_seamless_query_response(legacy_md, graph_path=str(corrupt_db))
    assert LINEAGE_SECTION_HEADER in upgraded.lineage_footer
    assert "**Graph Traversal Summary:**" in upgraded.lineage_footer
    assert len(upgraded.citations) == 1
