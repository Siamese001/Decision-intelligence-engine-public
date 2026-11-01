"""Adversarial and edge-case test suite for query traversal summary and external-ready prose.

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Category: Boundary payload (empty, whitespace, single-claim, zero-citation).
2. Category: Malformed input (unclosed brackets, broken markdown, corrupted URIs, malformed HTML tokens).
3. Category: Invariant bypass attempt (superseded claims, candidate badging, disguised inline IDs).
4. Category: Overflow & exhaustion (150+ assertions across 25+ sources, deduplication stress).
5. Category: Exact production regression (sanitizing messy inline-annotated query responses).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.query_core.lineage_formatter import (
    LINEAGE_SECTION_HEADER,
    format_seamless_query_response,
    validate_seamless_prose,
)
from tools.validate_query_output_format import validate_query_markdown


@pytest.fixture
def adversarial_db(tmp_path: Path) -> Path:
    """Provisions a temporary SQLite graph with varied assertion states (canonical, superseded, candidate)."""
    db_file = tmp_path / "adversarial_graph.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
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

            INSERT INTO node VALUES ('node_active', 'Active Architecture', 'architecture');
            INSERT INTO node VALUES ('node_deprecated', 'Deprecated Legacy Architecture', 'legacy');

            INSERT INTO source VALUES ('src_current', 'Current System Blueprint 2026', 'file:///tmp/blueprint.md', NULL);
            INSERT INTO source VALUES ('src_archived', 'Archived V1 System Spec', 'file:///tmp/v1_archived.md', NULL);

            INSERT INTO assertion VALUES (
                'ast_ing_active_01', 'node_active', 'HAS_STATE', NULL,
                'Active system is fully validated', 'Validation scope',
                'HIGH', 0.95, 'canonical', 1, 'approved', 'current', 'src_current', NULL
            );
            INSERT INTO assertion VALUES (
                'ast_ing_superseded_02', 'node_deprecated', 'HAS_STATE', NULL,
                'Legacy system was deprecated in 2024', 'Historical scope',
                'LOW', 0.10, 'superseded', 0, 'rejected', 'historical', 'src_archived', NULL
            );
            INSERT INTO assertion VALUES (
                'ast_ing_candidate_03', 'node_active', 'PROPOSES_FEATURE', NULL,
                'Candidate feature under review', 'Draft scope',
                'MEDIUM', 0.45, 'candidate', 0, 'pending', 'current', 'src_current', NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_file


def test_adversarial_boundary_payloads() -> None:
    """Category: Boundary payload - handles zero-length, whitespace, and plain markdown inputs safely."""
    # Empty string
    res_empty = format_seamless_query_response("")
    assert res_empty.clean_prose == ""
    assert res_empty.lineage_footer == ""
    assert res_empty.citations == []

    # Whitespace and newlines
    res_ws = format_seamless_query_response("   \n\n\t  \n  ")
    assert res_ws.clean_prose == ""
    assert res_ws.lineage_footer == ""

    # Single-word plain input
    res_word = format_seamless_query_response("Affirmative.")
    assert res_word.clean_prose == "Affirmative."
    assert res_word.lineage_footer == ""
    assert res_word.citations == []

    # Prose with zero database citations
    plain_text = "PartnerAdvisory delivers transformative digital solutions across enterprise clients."
    res_plain = format_seamless_query_response(plain_text)
    assert res_plain.clean_prose == plain_text
    assert res_plain.lineage_footer == ""
    assert res_plain.citations == []


def test_adversarial_malformed_inputs() -> None:
    """Category: Malformed input - unclosed brackets, broken markdown, and corrupted tokens."""
    # Unclosed bracket with raw ID
    unclosed = "Crucial finding ([ast_ing_unclosed_1234567890 and next statement."
    res_unclosed = format_seamless_query_response(unclosed)
    assert "[1]" not in res_unclosed.clean_prose
    assert "ast_ing_unclosed" not in res_unclosed.clean_prose
    assert len(res_unclosed.citations) == 1

    # Corrupted markdown link syntax
    broken_md = "Point A [link text](ast_ing_broken_md_9876543210) followed by text."
    res_broken = format_seamless_query_response(broken_md)
    assert "[1]" not in res_broken.clean_prose
    assert "ast_ing_broken" not in res_broken.clean_prose

    # Truncated or fragmented parenthetical
    fragment = "Testing fragment (ast_ing_frag_001) and [ast_ing_frag_002] in prose."
    res_frag = format_seamless_query_response(fragment)
    assert "[1]" not in res_frag.clean_prose
    assert "[2]" not in res_frag.clean_prose
    assert "ast_ing_frag" not in res_frag.clean_prose
    assert len(res_frag.citations) == 2


def test_adversarial_invariant_bypass_attempts(adversarial_db: Path) -> None:
    """Category: Invariant bypass attempt - detects disguised IDs, superseded assertions, and candidate state."""
    # Attempt 1: Disguised tokens in backticks and bold
    disguised = (
        "We recommend deploying `ast_ing_active_01` into the **workstream_core** pipeline "
        "alongside *person_john_doe* for review."
    )
    is_valid, violations = validate_seamless_prose(disguised)
    assert not is_valid
    assert any("assertion ID" in v for v in violations)
    assert any("node ID" in v for v in violations)

    formatted = format_seamless_query_response(disguised, graph_path=str(adversarial_db))
    valid_after, remaining = validate_seamless_prose(formatted.clean_prose)
    assert valid_after, f"Disguised tokens leaked into clean prose: {remaining}"
    assert "ast_ing_active_01" not in formatted.clean_prose
    assert "[1]" not in formatted.clean_prose
    assert "**Graph Traversal Summary:**" in formatted.lineage_footer

    # Attempt 2: Citing superseded assertion
    superseded_input = (
        "Legacy deployment strategy ([`ast_ing_superseded_02`](file:///tmp/test.sqlite)) "
        "is retained for backward compatibility."
    )
    formatted_superseded = format_seamless_query_response(superseded_input, graph_path=str(adversarial_db))
    # The footer should reflect the SUPERSEDED badge
    assert "SUPERSEDED (0%)" in formatted_superseded.lineage_footer

    # Validator must reject any query markdown that includes SUPERSEDED assertions
    is_valid_sup, errors_sup = validate_query_markdown(formatted_superseded.formatted_markdown)
    assert not is_valid_sup
    assert any("SUPERSEDED" in err for err in errors_sup)

    # Attempt 3: Citing candidate assertion
    candidate_input = (
        "New capability proposal ([`ast_ing_candidate_03`](file:///tmp/test.sqlite)) "
        "is under evaluation."
    )
    formatted_candidate = format_seamless_query_response(candidate_input, graph_path=str(adversarial_db))
    assert "CANDIDATE (45%)" in formatted_candidate.lineage_footer


def test_adversarial_overflow_and_exhaustion() -> None:
    """Category: Overflow & exhaustion - 150+ assertions across 25 distinct sources."""
    # Synthesize 150 claims referencing 25 distinct sources
    lines = []
    for i in range(150):
        src_idx = i % 25
        lines.append(
            f"- Finding number {i + 1} demonstrates verified invariant compliance "
            f"([`ast_ing_scale_{i:04d}`](file:///tmp/src_{src_idx}.sqlite))."
        )
    large_payload = "### Scalability Invariant Verification\n\n" + "\n".join(lines)

    result = format_seamless_query_response(large_payload)

    # 1. Clean prose checks
    valid, violations = validate_seamless_prose(result.clean_prose)
    assert valid, f"Violations in large payload: {violations}"
    assert "[1]" not in result.clean_prose
    assert "[150]" not in result.clean_prose
    assert "ast_ing_scale" not in result.clean_prose

    # 2. Footprint and deduplication checks
    assert len(result.citations) == 150
    assert "**Graph Traversal Summary:**" in result.lineage_footer
    assert "150 assertions evaluated" in result.lineage_footer
    assert "1 distinct sources" in result.lineage_footer

    # The table must consolidate all synthetic assertions into 1 deduplicated row, NOT 150 rows
    table_lines = [
        ln for ln in result.lineage_footer.splitlines()
        if ln.startswith("|") and not ln.startswith("| Source") and not ln.startswith("| :---")
    ]
    assert len(table_lines) == 1, f"Expected 1 deduplicated row, got {len(table_lines)}"


def test_adversarial_exact_production_regression() -> None:
    """Category: Exact production regression - turns messy inline query output into external-ready deliverable."""
    messy_production_output = """### Executive Recommendation: Agentic Managed Services

1. Modern engagements require proactive, outcome-driven architectures ([`ast_ing_ams_model_01`](file:///tmp/graph.sqlite)).
2. Continuous autonomous loops [1] provide 24/7 telemetry monitoring ([`ast_ing_ams_telemetry_02`](file:///tmp/graph.sqlite)).
3. Stakeholder Sarah Jenkins ([`person_sarah_jenkins`](file:///tmp/graph.sqlite)) oversees the integration [2, 3].
4. Project Sentinel ([`workstream_sentinel`](file:///tmp/graph.sqlite)) requires dual-key verification gates ([`asrt_claim_sentinel_gate`](file:///tmp/graph.sqlite)).

### Evidence & Footnotes
| Footnote | Assertion | Node | Confidence | Source |
| :--- | :--- | :--- | :--- | :--- |
| [1] | ast_ing_ams_model_01 | model_ams | HIGH (94%) | PartnerAdvisory AMS Spec |
| [2] | ast_ing_ams_telemetry_02 | model_ams | HIGH (91%) | PartnerAdvisory AMS Spec |
| [3] | asrt_claim_sentinel_gate | workstream_sentinel | HIGH (96%) | Sentinel SOW |
"""
    cleaned = format_seamless_query_response(messy_production_output)

    # Clean prose must be 100% free of legacy footnote brackets and raw tokens
    assert "[1]" not in cleaned.clean_prose
    assert "[2, 3]" not in cleaned.clean_prose
    assert "[2]" not in cleaned.clean_prose
    assert "[3]" not in cleaned.clean_prose
    assert "ast_ing_ams_model_01" not in cleaned.clean_prose
    assert "workstream_sentinel" not in cleaned.clean_prose
    assert "Sarah Jenkins" in cleaned.clean_prose
    assert "Project Sentinel" in cleaned.clean_prose

    # Footer must feature the new compact traversal summary and deduplicated source table
    assert "**Graph Traversal Summary:**" in cleaned.lineage_footer
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in cleaned.lineage_footer
    # Old legacy footnote header must not exist
    assert "| Footnote | Assertion | Node |" not in cleaned.lineage_footer
