"""Adversarial and edge-case tests for Wave 4 Transcript QA Healer & Cached ASR (CAT-2)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.transcript_qa_core.graph_healer import _heal_orphan_entities, _heal_single_ev_item
from dual_pipeline.transcript_qa_core.recap_healer import _heal_output_2_actions


# Category 1: Invariant bypass attempt (placeholder evidence item attempting C0_RECEIPT quote fabrication)
def test_adversarial_placeholder_evidence_never_fabricates_c0_receipt_quote() -> None:
    """CAT2-P0-1: Placeholder 'Evidence statement' must not fabricate 'Confirmed attendee ... from verified C0 roster'."""
    ev_idx = {
        "ev-fake": {"text": "Evidence statement", "speaker": "person:alice"},
    }
    changes: list[str] = []
    pruned: list[str] = []
    _heal_single_ev_item("ev-fake", ev_idx["ev-fake"], cited_eids=set(), changes=changes, pruned_eids=pruned, ev_idx=ev_idx)
    assert "ev-fake" not in ev_idx
    assert "ev-fake" in pruned


# Category 2: Invariant bypass attempt (remediation prompt markdown table scraping into Output 02)
def test_adversarial_remediation_prompt_table_never_injected_into_output_2() -> None:
    """CAT2-P1-2: Markdown table inside remediation_directive_recap must never be scraped into Output 02."""
    recap_text = "## Action & Governance Ledger\n\nNo formal actions were established in this session.\n"
    meta = {
        "remediation_directive_recap": (
            "Please fix formatting:\n"
            "| # | Type | Action | Owner |\n"
            "| 1 | Action | Fake Scraped Prompt Row | PromptOwner |\n"
        )
    }
    healed = _heal_output_2_actions(recap_text, changes=[], audit_log=[], output_1=None, meta=meta)
    assert "Fake Scraped Prompt Row" not in healed


# Category 3: Boundary payload / Cached ASR single-speaker collapse detection
def test_adversarial_cached_acoustic_hit_enforces_collapse_validation_and_sanitization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAT2-P1-1: _finalize_cached_acoustic_hit runs _sanitize_raw_segments and validate_acoustic_diarization."""
    transcriber = LocalMLXAudioTranscriber(mock=True)
    raw_segments = [
        {"start": 0.0, "end": 5.0, "text": "Hello world"},
        {"start": 5.0, "end": 10.0, "text": "Thank you for watching"},  # Whisper hallucination phrase
    ]
    sanitized_called: list[bool] = []
    orig_sanitize = transcriber._sanitize_raw_segments

    def _spy_sanitize(segs, meta):
        sanitized_called.append(True)
        return orig_sanitize(segs, meta)

    monkeypatch.setattr(transcriber, "_sanitize_raw_segments", _spy_sanitize)

    def _fake_validate(diar, expected_roster_count=None, source_name=""):
        if expected_roster_count and expected_roster_count >= 3:
            raise DiarizationCollapseError("Cached diarization collapsed to 1 cluster for 4 attendees")

    monkeypatch.setattr("dual_pipeline.audio_ingestion_core.diarization_engine.validate_acoustic_diarization", _fake_validate)

    with pytest.raises(DiarizationCollapseError, match="Cached diarization collapsed"):
        transcriber._finalize_cached_acoustic_hit(
            raw_segments=raw_segments,
            cached_diar=([{"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"}], {}),
            has_cached_speakers=False,
            cache_dir=tmp_path,
            safe_stem="test_audio",
            cache_key="k1",
            audio_hash="h1",
            metadata={"attendees": ["Alice", "Bob", "Carol", "Dave"]},
        )
    assert sanitized_called == [True]


# Category 4: Cross-tenant leakage prevention in _heal_orphan_entities
def test_adversarial_orphan_healer_does_not_inject_apex_global_org_into_non_nea_project() -> None:
    """CAT2-P0-2: Non-PartnerAdvisory person in non-Apex Global project must not auto-inject org:apex_trans."""
    mutations = {
        "entity_upserts": [
            {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory"},
            {"id": "person:external-client", "type": "person", "name": "External Client", "description": "Client VP"},
        ],
        "relationship_signals": [
            {"from_id": "org:partner_advisory", "to_id": "system:crm", "relationship": "USES"},
        ],
    }
    data = {"graph_mutations": mutations, "evidence_index": {}}
    _heal_orphan_entities(data, mutations, changes=[], is_doc=True, metadata={"project": "other_client"})
    entity_ids = {e["id"] for e in mutations["entity_upserts"]}
    assert "org:apex_trans" not in entity_ids
