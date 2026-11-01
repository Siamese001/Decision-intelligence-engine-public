"""Adversarial and edge-case tests for Wave 3 Modality Ingestion & Evidence Envelope (CAT-1)."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from dual_pipeline.cli.ingest import _validate_canonical_input
from dual_pipeline.document_pipeline_core.executor import _rebind_single_update_item
from tools.prepare_evidence_envelope_intake import _validate_single_locator


# Category 1: Invariant bypass attempt (fuzzy input resolution matching blocked legacy .doc / .ppt)
def test_adversarial_fuzzy_resolved_legacy_extension_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAT1-P1-2: Fuzzy input resolver matching a legacy .doc file must fail closed."""
    legacy_doc = tmp_path / "legacy_deck.ppt"
    legacy_doc.write_bytes(b"legacy binary")

    monkeypatch.setattr("dual_pipeline.cli.ingest._is_fuzzy_resolvable_input", lambda *a, **kw: True)
    monkeypatch.setattr("dual_pipeline.cli.ingest._resolve_input_resource_fuzzy", lambda *a, **kw: (legacy_doc, None))

    args = argparse.Namespace(input="legacy_deck", source=None, inputs=None, metadata=None)
    with pytest.raises(ValueError, match="UNSUPPORTED_MODALITY_GOVERNED_REFUSAL"):
        _validate_canonical_input(args)


# Category 2: Malformed input (unrelated or non-existent evidence_ids on document update items)
def test_adversarial_unmatched_evidence_ids_cleared_instead_of_preserved() -> None:
    """CAT1-P1-1: When token overlap is zero across all evidence items, hallucinated evidence_ids are cleared."""
    item = {
        "description": "Quantum cryptography satellite link deployment",
        "evidence_ids": ["ev-hallucinated-99"],
    }
    ev_tokens_map = {
        "ev-01": {"quarterly", "catering", "lunch", "budget"},
    }
    _rebind_single_update_item(item, ev_tokens_map, lambda s: set(s.lower().split()))
    assert item["evidence_ids"] == []


# Category 3: Boundary payload (single-digit page locator matching prose substring instead of page marker)
def test_adversarial_single_char_page_locator_cannot_bypass_via_substring(tmp_path: Path) -> None:
    """CAT1-P1-4: Page locator with value '1' must not bypass validation solely because '1' is in prose."""
    src_p = tmp_path / "notes.md"
    src_text = "We discussed 1 item during the quick sync.\n"
    src_p.write_text(src_text, encoding="utf-8")

    item = {"text": "Nonexistent claim nowhere in text", "locator": {"kind": "timestamp", "value": "1"}}
    with pytest.raises(ValueError, match="source-bound locator is absent"):
        _validate_single_locator(item, item["locator"], src_text, src_p)
