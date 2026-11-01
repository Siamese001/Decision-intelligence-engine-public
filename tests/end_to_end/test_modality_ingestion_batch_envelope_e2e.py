"""End-to-end tests for Wave 3 Modality Ingestion, Batch Isolation & Evidence Envelope (CAT-1)."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
from dual_pipeline.cli.ingest import _batch_all_document_inputs, _validate_canonical_input
from dual_pipeline.document_pipeline_core.executor import (
    _rebind_quarantined_locators,
    _rebind_single_update_item,
    _resolve_document_raw_graph,
)
from tools.prepare_evidence_envelope_intake import (
    _adapt_v53_projection_to_envelope,
    _resolve_envelope_started_at,
)


def test_mixed_batch_inputs_do_not_poison_non_document_items(tmp_path: Path) -> None:
    """CAT1-P0-1: A mixed batch of .pdf and .txt transcript does not mark all items as document modality."""
    doc_f = tmp_path / "deck.pdf"
    doc_f.write_bytes(b"%PDF-1.4")
    txt_f = tmp_path / "meeting_transcript.txt"
    txt_f.write_text("[00:00:05] Alice Smith: Welcome to our session today.\n" * 5, encoding="utf-8")

    assert _batch_all_document_inputs([str(doc_f)], {}) is True
    assert _batch_all_document_inputs([str(doc_f), str(txt_f)], {}) is False


def test_u0_ingress_blocks_unrecognized_single_token_speaker_even_with_container_dir(tmp_path: Path) -> None:
    """CAT1-P0-2: Live container receipt cannot auto-confirm when unrecognized single-token speaker exists."""
    metadata = {
        "date": "2026-10-15",
        "time": "10:00 AM - 11:00 AM ET",
        "attendees": ["Alice Smith (VP, PartnerAdvisory)", "MysterySingleToken"],
        "container_dir": str(tmp_path),
        "is_audio": True,
        "attendee_roles_confirmed_by_operator": True,
    }
    raw_tx = "[00:00:10] Alice Smith: Hello.\n[00:00:20] MysterySingleToken: I have a question.\n"
    report = batch_validate_preflight_checks(metadata, raw_transcript=raw_tx)
    assert "attendee_confirmation" in report.missing_fields
    assert not metadata.get("attendees_confirmed_by_operator")


def test_u0_ingress_exempts_document_from_mandatory_time() -> None:
    """CAT1-P1-3: Document modality is exempt from U0_MANDATORY_TIME."""
    metadata = {
        "is_document": True,
        "canonical_modality": "DOCUMENT",
        "date": "2026-10-15",
    }
    report = batch_validate_preflight_checks(metadata, raw_transcript="")
    assert "time" not in report.missing_fields


def test_visual_document_remediation_retry_routes_to_provider_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAT1-P0-3: When remediation_directive is present, visual document does not short-circuit to static extractor."""
    calls: list[str] = []

    class _DummyProvider:
        def synthesize_graph(self, sys_prompt: str, user_content: str) -> str:
            calls.append(user_content)
            return '{"graph_mutations": {"entity_upserts": [{"id": "person:healed", "type": "person"}]}}'

    res = _resolve_document_raw_graph(
        provider=_DummyProvider(),  # type: ignore[arg-type]
        document_text="Slide 1: Org Chart",
        metadata={"is_visual_document": True, "project": "nea"},
        remediation_directive="Fix missing leadership edge",
    )
    assert len(calls) == 1
    assert "person:healed" in res


def test_evidence_envelope_preserves_referenced_metadata_statement_and_parses_12h_time(tmp_path: Path) -> None:
    """CAT1-P0-4 & CAT1-P1-4: Referenced metadata evidence is retained and 12h time ranges normalize to ISO-8601."""
    src_p = tmp_path / "source.md"
    src_p.write_text("# Session Context\nApproved the budget migration.\n", encoding="utf-8")

    started_at = _resolve_envelope_started_at(
        {"date": "2026-10-15", "time": "02:30 PM - 03:30 PM ET"},
        {},
        "mtg-20261015",
    )
    assert started_at == "2026-10-15T14:30:00-04:00"

    draft = {
        "meeting_id": "mtg-20261015",
        "title": "Budget Review",
        "date": "2026-10-15",
        "time": "02:30 PM - 03:30 PM ET",
        "organizer": "Alice Smith",
        "evidence_index": {
            "ev-meta-1": {
                "speaker": "meeting_metadata",
                "text": "Approved the budget migration.",
                "locator": {"kind": "section", "value": "Session Context"},
            },
            "ev-unreferenced-meta": {
                "speaker": "system",
                "text": "Unreferenced system note.",
                "locator": {"kind": "section", "value": "Session Context"},
            },
        },
        "decision_updates": [
            {
                "decision_id": "dec-01",
                "decision_question": "Approve budget migration?",
                "status": "APPROVED",
                "evidence_ids": ["ev-meta-1"],
            }
        ],
    }
    envelope = _adapt_v53_projection_to_envelope(draft, source_path=src_p)
    all_stmt_ids = {
        st["statement_id"]
        for spk in envelope["speakers"]
        for st in spk.get("statements", [])
    }
    assert "ev-meta-1" in all_stmt_ids
    assert "ev-unreferenced-meta" not in all_stmt_ids
