"""Adversarial and edge-case verification for document ingestion acceleration.

Covers mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty strings, zero-length quotes, null coverage payloads, whitespace-only locators.
2. Malformed input: Corrupted SHA-256 digests, negative record counts, mismatching processed records.
3. Invariant bypass attempt: Fabricated quotes, hallucinatory assertions failing closed as UNGROUNDED_HALLUCINATION.
4. Exact production regression: Pin the task-609 failure where 12 quotes lost markdown bullet/bold markers.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict

import pytest

from dual_pipeline.document_pipeline_core.bounded_patch import _check_source
from dual_pipeline.llm_judge_core.graph_checks import check_quote_and_evidence_resolution
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    locate_quote_pointer,
    resolve_evidence_pointers,
)


SAMPLE_DOCUMENT = """# Technical Architecture & Operations Guide

- **OP-001**: Kubernetes clusters are provisioned using Terraform infrastructure modules.
- **OP-002**: Database replication uses asynchronous WAL streaming with a 30-second timeout.
- **OP-003**: Microservices authenticate via mutual TLS with certificate rotation every 90 days.
"""


def test_adversarial_boundary_payloads() -> None:
    """Category 1: Boundary payload tests for empty, whitespace, and null inputs."""
    # 1. Empty source and quote
    assert locate_quote_pointer("", "some quote") is None
    assert locate_quote_pointer(SAMPLE_DOCUMENT, "") is None
    assert locate_quote_pointer(SAMPLE_DOCUMENT, "   \n\t  ") is None

    # 2. Empty evidence index in projection
    empty_proj = {"evidence_index": {}, "graph_mutations": {}}
    res = resolve_evidence_pointers(empty_proj, SAMPLE_DOCUMENT)
    assert res["evidence_index"] == {}

    # 3. None/missing coverage in non-DOCX
    candidate = {
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "exact_text": "Kubernetes clusters are provisioned using Terraform",
                "locator": {"kind": "span", "start": 56, "end": 107},
            }
        },
        "graph_mutations": {},
    }
    # Coverage is None for markdown: should not raise
    _check_source(candidate, SAMPLE_DOCUMENT, {"source_format": "md"})


def test_adversarial_malformed_input() -> None:
    """Category 2: Malformed input tests for coverage dicts and corrupted digests."""
    candidate = {
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "exact_text": "Kubernetes clusters are provisioned using Terraform",
                "locator": {"kind": "span", "start": 56, "end": 107},
            }
        },
        "graph_mutations": {},
    }

    # 1. Incomplete coverage dict
    with pytest.raises(ValueError, match="Patch source coverage is incomplete"):
        _check_source(candidate, SAMPLE_DOCUMENT, {
            "source_format": "md",
            "_source_coverage": {"complete": False},
        })

    # 2. Mismatching record counts
    with pytest.raises(ValueError, match="Patch source coverage count mismatch"):
        _check_source(candidate, SAMPLE_DOCUMENT, {
            "source_format": "md",
            "_source_coverage": {
                "complete": True,
                "total_records": 10,
                "processed_records": 5,
            },
        })

    # 3. Negative record count
    with pytest.raises(ValueError, match="Patch source coverage count mismatch"):
        _check_source(candidate, SAMPLE_DOCUMENT, {
            "source_format": "md",
            "_source_coverage": {
                "complete": True,
                "total_records": -1,
                "processed_records": -1,
            },
        })

    # 4. Corrupted extracted_sha256
    with pytest.raises(ValueError, match="Patch source coverage digest mismatch"):
        _check_source(candidate, SAMPLE_DOCUMENT, {
            "source_format": "md",
            "_source_coverage": {
                "complete": True,
                "extracted_sha256": "deadbeef" * 8,
            },
        })

    # 5. Corrupted raw source sha256
    with pytest.raises(ValueError, match="Patch source coverage raw-byte binding mismatch"):
        _check_source(candidate, SAMPLE_DOCUMENT, {
            "source_format": "md",
            "raw_source_sha256": "1111" * 16,
            "_source_coverage": {
                "complete": True,
                "source_sha256": "2222" * 16,
            },
        })


def test_adversarial_invariant_bypass_attempts() -> None:
    """Category 3: Invariant bypass attempt: fabricated quotes must fail closed."""
    hallucinated_quotes = [
        "All engineers are immediately granted root access to production servers.",
        "Apex Global has agreed to decommission all on-premise power plants by Friday.",
        "PartnerAdvisory leadership voted to cancel the cloud modernization contract.",
    ]

    proj = {
        "evidence_index": {
            f"ev-{i}": {
                "statement_id": f"ev-{i}",
                "text": q,
                "locator": "L99",
            }
            for i, q in enumerate(hallucinated_quotes, start=1)
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "entity_id": "org:partner_advisory",
                    "evidence_ids": [f"ev-{i}" for i in range(1, len(hallucinated_quotes) + 1)],
                }
            ]
        },
    }

    # 1. resolve_evidence_pointers must mark all as UNGROUNDED_HALLUCINATION
    resolved = resolve_evidence_pointers(copy.deepcopy(proj), SAMPLE_DOCUMENT)
    for eid, ev in resolved["evidence_index"].items():
        assert ev.get("grounding_status") == "UNGROUNDED_HALLUCINATION"
        assert ev.get("quote_pointer") is None
        assert "exact_text" not in ev

    # 2. check_quote_and_evidence_resolution must fail with defects
    passed, defects, stats = check_quote_and_evidence_resolution(resolved, SAMPLE_DOCUMENT)
    assert passed is False
    assert stats["broken_quotes_count"] == len(hallucinated_quotes)
    assert any("Quote grounding defect" in d for d in defects)


def test_exact_production_regression_task_609() -> None:
    """Category 4: Exact regression test for task-609 DLQ failure dlq_20261014_125233_bd8741b0.

    In task-609, 12 evidence quotes stripped markdown bold/bullet prefixes,
    causing quote grounding to fail with 12 ungrounded quotes, capping Dimension 6 to 10 (< floor 14)
    and crashing the pipeline at Gate X1-X3.
    """
    catalog_source = """# PartnerAdvisory Solutions Catalog

- **DEC-ARCH-001**: PartnerAdvisory and Apex Global will establish a unified architecture specification for Florida.
- **DEC-ARCH-002**: Delivery lead Cameron Mercer coordinates cloud engineering workstreams across all accounts.
- **DEC-ARCH-003**: Enrique Ervesun and Doug White sponsor the enterprise modernization initiative.
"""

    stripped_quotes = [
        "DEC-ARCH-001: PartnerAdvisory and Apex Global will establish a unified architecture specification for Florida.",
        "DEC-ARCH-002: Delivery lead Cameron Mercer coordinates cloud engineering workstreams across all accounts.",
        "DEC-ARCH-003: Enrique Ervesun and Doug White sponsor the enterprise modernization initiative.",
    ]

    proj = {
        "evidence_index": {
            f"ev-{i}": {
                "statement_id": f"ev-{i}",
                "text": q,
                "locator": f"L{i}",
            }
            for i, q in enumerate(stripped_quotes, start=1)
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "entity_id": "org:partner_advisory",
                    "evidence_ids": [f"ev-{i}" for i in range(1, len(stripped_quotes) + 1)],
                }
            ]
        },
    }

    resolved = resolve_evidence_pointers(copy.deepcopy(proj), catalog_source)
    for eid, ev in resolved["evidence_index"].items():
        assert ev["grounding_status"] == "GROUNDED"
        assert ev["quote_pointer"] is not None

    passed, defects, stats = check_quote_and_evidence_resolution(resolved, catalog_source)
    assert passed is True
    assert defects == []
    assert stats["broken_quotes_count"] == 0


def test_circuit_breaker_granular_hash_monotonic_progress() -> None:
    """Verifies that circuit breaker failure hashing differentiates score improvements from stagnant loops."""
    from unittest.mock import MagicMock
    from dual_pipeline.canonical_pipeline.checkpoint_memoizer import check_circuit_breaker
    from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker

    cb = CandidateCircuitBreaker(max_consecutive_failures=2)

    # Attempt 1: Output 3 fails with score 84
    eval1 = MagicMock()
    eval1.authoritative_write_authorized = False
    eval1.final_decision = {"status": "FAIL"}
    eval1.qa_report = {"failures": []}
    eval1.judge_report = {"output_3_evaluation": {"composite_score": 84, "defect_codes": ["D6_DEFECT"]}}

    tripped1 = check_circuit_breaker(cb, eval1, "candidate1", 1, 2)
    assert not tripped1

    # Attempt 2: Output 3 monotonically improves to 85 (same defect type, higher score)
    eval2 = MagicMock()
    eval2.authoritative_write_authorized = False
    eval2.final_decision = {"status": "FAIL"}
    eval2.qa_report = {"failures": []}
    eval2.judge_report = {"output_3_evaluation": {"composite_score": 85, "defect_codes": ["D6_DEFECT"]}}

    tripped2 = check_circuit_breaker(cb, eval2, "candidate2", 2, 2)
    # Must NOT trip on monotonic score improvement
    assert not tripped2

    # Attempt 3: Stagnant failure with identical score 85 and identical defect
    eval3 = MagicMock()
    eval3.authoritative_write_authorized = False
    eval3.final_decision = {"status": "FAIL"}
    eval3.qa_report = {"failures": []}
    eval3.judge_report = {"output_3_evaluation": {"composite_score": 85, "defect_codes": ["D6_DEFECT"]}}

    tripped3 = check_circuit_breaker(cb, eval3, "candidate2", 2, 2)
    # MUST trip on identical consecutive candidate failure
    assert tripped3
