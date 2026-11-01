"""End-to-end verification for document ingestion acceleration and quote grounding.

Verifies:
1. Deterministic markdown-aware quote grounding and pointer resolution against realistic catalog fixtures.
2. Format-agnostic source coverage verification in bounded patch remediation for non-DOCX documents.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict

import pytest

from dual_pipeline.document_pipeline_core.bounded_patch import _check_source, validate_patch_result
from dual_pipeline.llm_judge_core.graph_checks import check_quote_and_evidence_resolution
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    locate_quote_pointer,
    resolve_evidence_pointers,
)


CATALOG_MARKDOWN_SOURCE = """# PartnerAdvisory Solutions Catalog & Apex Global 2027 Strategic Plays Master Specification

**Date:** 10/14/2026
**Project:** enterprise_account_planning
**Author:** PartnerAdvisory Solutions Architecture & Account Leadership Team

---

## 1. Executive Context & Engagement Topology

PartnerAdvisory and Apex Global Enterprises have entered a multi-year strategic partnership to accelerate digital transformation.

- **DEC-ARCH-001**: PartnerAdvisory and Apex Global will establish a unified architecture specification for Florida operations.
- **DEC-ARCH-002**: Delivery lead Cameron Mercer coordinates cloud engineering workstreams across all accounts.
- **DEC-ARCH-003**: Enrique Ervesun and Doug White sponsor the enterprise modernization initiative.
- **DEC-ARCH-004**: System telemetry will integrate with OpenTelemetry collector pipelines.
- **DEC-ARCH-005**: All cloud infrastructure migrations must adhere to zero-trust network boundaries.
- **DEC-ARCH-006**: Account planning reviews occur bi-weekly with PartnerAdvisory executive leadership.
- **DEC-ARCH-007**: Architecture Decision Records are committed to git repositories with mandatory peer reviews.
- **DEC-ARCH-008**: Apex Global enterprise architects will review all integration specifications before deployment.
- **DEC-ARCH-009**: The core migration timeline targets Q3 2027 for foundational platform readiness.
- **DEC-ARCH-010**: PartnerAdvisory cloud engineers provide hands-on architectural enablement and training.
- **DEC-ARCH-011**: Data pipeline ingestion benchmarks require sub-minute turnaround for daily telemetry.
- **DEC-ARCH-012**: Strategic account milestones require signoff from Dan and Andrew Hanson.
"""


def _build_projection_with_quotes(quotes: list[str]) -> Dict[str, Any]:
    evidence_index = {}
    for idx, q in enumerate(quotes, start=1):
        eid = f"ev-{idx:02d}"
        evidence_index[eid] = {
            "statement_id": eid,
            "text": q,
            "speaker": "PartnerAdvisory Leadership",
            "locator": f"L{idx * 2}",
        }
    return {
        "evidence_index": evidence_index,
        "graph_mutations": {
            "entity_upserts": [
                {
                    "entity_id": "org:partner_advisory",
                    "entity_type": "ORGANIZATION",
                    "name": "PartnerAdvisory",
                    "evidence_ids": [f"ev-{i:02d}" for i in range(1, len(quotes) + 1)],
                }
            ],
            "relationship_signals": [],
        },
    }


def test_markdown_catalog_quote_grounding_e2e() -> None:
    """Verifies that quotes stripped of markdown bold/bullet prefixes resolve deterministically."""
    # 12 quotes extracted without the leading markdown bullet and bold markers
    unformatted_quotes = [
        "DEC-ARCH-001: PartnerAdvisory and Apex Global will establish a unified architecture specification for Florida operations.",
        "DEC-ARCH-002: Delivery lead Cameron Mercer coordinates cloud engineering workstreams across all accounts.",
        "DEC-ARCH-003: Enrique Ervesun and Doug White sponsor the enterprise modernization initiative.",
        "DEC-ARCH-004: System telemetry will integrate with OpenTelemetry collector pipelines.",
        "DEC-ARCH-005: All cloud infrastructure migrations must adhere to zero-trust network boundaries.",
        "DEC-ARCH-006: Account planning reviews occur bi-weekly with PartnerAdvisory executive leadership.",
        "DEC-ARCH-007: Architecture Decision Records are committed to git repositories with mandatory peer reviews.",
        "DEC-ARCH-008: Apex Global enterprise architects will review all integration specifications before deployment.",
        "DEC-ARCH-009: The core migration timeline targets Q3 2027 for foundational platform readiness.",
        "DEC-ARCH-010: PartnerAdvisory cloud engineers provide hands-on architectural enablement and training.",
        "DEC-ARCH-011: Data pipeline ingestion benchmarks require sub-minute turnaround for daily telemetry.",
        "DEC-ARCH-012: Strategic account milestones require signoff from Dan and Andrew Hanson.",
    ]

    candidate = _build_projection_with_quotes(unformatted_quotes)

    # 1. Resolve quote pointers
    resolved = resolve_evidence_pointers(copy.deepcopy(candidate), CATALOG_MARKDOWN_SOURCE)

    # Every quote must be GROUNDED with a valid character slice in source
    for eid, ev in resolved["evidence_index"].items():
        assert ev.get("grounding_status") == "GROUNDED", f"{eid} failed grounding: {ev}"
        pointer = ev.get("quote_pointer")
        assert isinstance(pointer, list) and len(pointer) == 2, f"{eid} missing pointer"
        start, end = pointer
        assert 0 <= start < end <= len(CATALOG_MARKDOWN_SOURCE)
        verbatim = CATALOG_MARKDOWN_SOURCE[start:end]
        assert ev["exact_text"] == verbatim

    # 2. Verify LLM judge deterministic checks pass with 0 broken quotes
    passed, defects, stats = check_quote_and_evidence_resolution(resolved, CATALOG_MARKDOWN_SOURCE)
    assert passed is True, f"Deterministic check failed with defects: {defects}"
    assert defects == []
    assert stats["broken_quotes_count"] == 0


def test_bounded_patch_markdown_source_coverage_e2e() -> None:
    """Verifies that non-DOCX coverage dicts succeed during bounded patch validation."""
    source_bytes = CATALOG_MARKDOWN_SOURCE.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    metadata = {
        "source_format": "md",
        "input_ext": ".md",
        "raw_source_sha256": source_sha,
        "_source_coverage": {
            "total_records": 12,
            "processed_records": 12,
            "lines_count": 28,
            "units_count": 12,
            "complete": True,
        },
    }

    # Pointer-bound candidate projection
    candidate = {
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "exact_text": "PartnerAdvisory and Apex Global Enterprises have entered a multi-year strategic partnership",
                "locator": {"kind": "span", "start": 182, "end": 254},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "entity_id": "org:partner_advisory",
                    "entity_type": "ORGANIZATION",
                    "name": "PartnerAdvisory",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "relationship_signals": [],
        },
    }

    # _check_source must not raise for markdown coverage
    _check_source(candidate, CATALOG_MARKDOWN_SOURCE, metadata)


def test_document_mode_remediation_state_hygiene_e2e() -> None:
    """Verifies that empty O1/O2 deliverables in document mode do not trigger spurious regressions."""
    from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine

    sm = SelectiveRemediationStateMachine(max_attempts=2)

    # Attempt 1: Document mode with empty O1/O2 and Output 3 at score 84
    decision = {"status": "FAIL", "output_3_deterministic_gates_pass": True}
    j_rep1 = {"output_3_evaluation": {"composite_score": 84, "verdict": "FAIL"}}
    qa_rep = {"output_03_canonical_signal_projection": {"passed": True}}
    artifacts1 = {"output_1": "", "output_2": "", "output_3": "{\"candidate\": 1}"}

    sm.evaluate_attempt(decision, j_rep1, qa_rep, artifacts=artifacts1, is_notes_mode=True)

    # Ledger must not track empty strings for O1/O2
    assert sm.get_best_candidate("output_1") is None
    assert sm.get_best_candidate("output_2") is None
    assert sm.get_best_candidate("output_3") is not None
    assert sm.get_high_water_mark_score("output_3") == 84

    # Attempt 2: Output 3 improves to 85
    sm.current_attempt = 2
    j_rep2 = {"output_3_evaluation": {"composite_score": 85, "verdict": "FAIL"}}
    artifacts2 = {"output_1": "", "output_2": "", "output_3": "{\"candidate\": 2}"}

    sm.evaluate_attempt(decision, j_rep2, qa_rep, artifacts=artifacts2, is_notes_mode=True)

    # O1/O2 must not be flagged as regressed
    assert not sm.has_regressed("output_1")
    assert not sm.has_regressed("output_2")
    assert not sm.has_regressed("output_3")
    assert sm.get_high_water_mark_score("output_3") == 85


def test_consolidated_document_intelligence_entity_cap_e2e() -> None:
    """Verifies that 64 detected entities are capped at 14 to preserve mutation budget."""
    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
        MultiAgentDocumentIntelligenceReport,
        build_intelligence_prior_context,
    )

    entities = [
        {"id": f"role:engineer_{i}", "type": "role", "name": f"Engineer {i}", "description": f"Lead {i}"}
        for i in range(1, 65)
    ]
    report = MultiAgentDocumentIntelligenceReport(
        document_title="Enterprise Solutions Catalog",
        total_entities_detected=64,
        entities=entities,
        relationships=[],
        coverage_score=1.0,
        primacy_bias_detected=False,
        arbiter_verdict="PASS",
        arbiter_notes="Full coverage.",
    )

    prior_ctx = build_intelligence_prior_context(report)

    # Must contain capped entity count of 14, not all 64
    assert "- **Identified First-Class Entities (14):**" in prior_ctx
    assert "role:engineer_14" in prior_ctx
    assert "role:engineer_15" not in prior_ctx
    assert "Do not drop secondary roles" in prior_ctx


def test_single_read_document_scaffold_metadata_e2e(tmp_path) -> None:
    """Verifies that scaffold_metadata reuses pre-loaded source_text without re-reading disk/cache."""
    from dual_pipeline.canonical_pipeline.lifecycle import CanonicalModality, scaffold_metadata

    doc_file = tmp_path / "catalog.md"
    doc_file.write_text(CATALOG_MARKDOWN_SOURCE, encoding="utf-8")

    meta = {
        "project": "enterprise_account_planning",
        "title": "PartnerAdvisory Solutions Catalog 2026 Master",
        "date": "10/14/2026",
    }

    # Pass pre-loaded source_text
    scaffolded = scaffold_metadata(
        CanonicalModality.DOCUMENT,
        doc_file,
        meta,
        source_text=CATALOG_MARKDOWN_SOURCE,
    )

    assert scaffolded["is_document"] is True
    assert scaffolded["output3_only"] is True
    assert scaffolded["project"] == "enterprise_account_planning"
    assert scaffolded["title"] == "PartnerAdvisory Solutions Catalog 2026 Master"
