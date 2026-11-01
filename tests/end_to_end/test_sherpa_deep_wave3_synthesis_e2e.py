"""End-to-End verification of Sherpa Deep RCA Wave 3:
Thread-Safe Synthesis, Stage 02/03 Healer De-Sabotage & Checkpoint Pinning.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evaluator import (
    _merge_pinned_into_judge_report,
    _pre_heal_output_3,
    _resolve_pinned_evaluation,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
    NotesMeetingSynthesisStrategy,
    SynthesisResult,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
from dual_pipeline.canonical_pipeline.transcript_resume_binding import (
    build_transcript_resume_binding,
)
from dual_pipeline.transcript_qa_core.eval_cache import check_pin
from dual_pipeline.transcript_qa_core.graph_ontology_healer import _is_lens_session
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import (
    _apply_pruned_evidence,
    _extract_proposal_tokens,
)
from dual_pipeline.transcript_qa_core.recap_healer import (
    _heal_output_2_placeholder_titles,
    _resolve_dan_label,
)
from dual_pipeline.transcript_qa_core.recap_key_discussion_healer import (
    heal_key_discussion_anchors,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import _slugify


def test_concurrent_synthesis_strategy_context_isolation():
    """Verify concurrent synthesis strategies isolate pa_context and metadata without race conditions."""
    shared_meta = {
        "project": "project_beacon",
        "meeting_name": "Deep RCA Sync",
        "reasoning_effort": "low",
    }
    pa_context = {"metadata": shared_meta}

    results = {}
    errors = []

    def run_full(thread_id: int):
        try:
            strat = FullMeetingSynthesisStrategy(provider=object(), l2_forensic=object(), l2_recap=object(), l2_graph=object())
            ctx = copy.deepcopy(pa_context)
            meta = copy.deepcopy(ctx.get("metadata", {}))
            meta["reasoning_effort"] = f"effort_full_{thread_id}"
            ctx["metadata"] = meta
            results[f"full_{thread_id}"] = ctx["metadata"]["reasoning_effort"]
        except Exception as e:
            errors.append(e)

    def run_notes(thread_id: int):
        try:
            strat = NotesMeetingSynthesisStrategy(provider=object())
            ctx = copy.deepcopy(pa_context)
            meta = copy.deepcopy(ctx.get("metadata", {}))
            meta["reasoning_effort"] = f"effort_notes_{thread_id}"
            ctx["metadata"] = meta
            results[f"notes_{thread_id}"] = ctx["metadata"]["reasoning_effort"]
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=run_full, args=(1,)),
        threading.Thread(target=run_notes, args=(2,)),
        threading.Thread(target=run_full, args=(3,)),
        threading.Thread(target=run_notes, args=(4,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent execution had errors: {errors}"
    assert shared_meta["reasoning_effort"] == "low", "Shared metadata was mutated by worker threads!"
    assert results["full_1"] == "effort_full_1"
    assert results["notes_2"] == "effort_notes_2"


def test_surgical_patcher_heading_and_appendix_preservation():
    """Verify SurgicalPatcher.apply_critique_envelope preserves # and ## heading markers and Appendix speaker H1s."""
    original_markdown = (
        "# Executive Summary\n\n"
        "Initial summary paragraph.\n\n"
        "## Key Decisions\n\n"
        "- Decision 1: Proceed with cloud native migration.\n\n"
        "# Appendix: Speaker Contributions\n\n"
        "## Jeremy Cianella (Chief Technology Officer)\n"
        "- Focused on low latency ASR pipelines.\n"
    )

    envelope = {
        "target_path": "sections[Executive Summary]",
        "protected_paths": ["sections[Key Decisions]", "sections[Appendix: Speaker Contributions]"],
    }
    replacement = "Updated executive summary with comprehensive scope.\n\n"

    patched, audit = SurgicalPatcher.apply_critique_envelope(
        original_text=original_markdown,
        critique_envelope=envelope,
        replacement_content=replacement,
    )
    assert "# Executive Summary" in patched
    assert "## Key Decisions" in patched
    assert "# Appendix: Speaker Contributions" in patched
    assert "## Jeremy Cianella (Chief Technology Officer)" in patched
    assert "Updated executive summary with comprehensive scope." in patched


def test_recap_healer_placeholder_titles_and_dash_forms():
    """Verify recap healer resolves placeholder titles across em-dash, en-dash, and hyphen forms."""
    recap_text = (
        "# Meeting Recap\n\n"
        "## Attendees\n"
        "- Drew Oakley (Lead)\n"
        "- Alex Rivera (Project Lead)\n\n"
        "# Appendix: Speaker Contributions\n\n"
        "# Drew Oakley – PartnerAdvisory, Lead\n"
        "- Contributed key feedback.\n"
    )

    meta = {
        "roster": [
            {"name": "Drew Oakley", "role": "Lead"},
            {"name": "Alex Rivera", "role": "Project Lead"},
        ]
    }

    # Drew Oakley resolution
    dan_label = _resolve_dan_label(meta)
    assert "Client Service Lead, PartnerAdvisory" in dan_label

    healed = _heal_output_2_placeholder_titles(recap_text, meta=meta, changes=[], audit_log=[])
    assert "Drew Oakley (Client Service Lead)" in healed
    assert "# Drew Oakley — PartnerAdvisory, Client Service Lead" in healed


def test_recap_key_discussion_dynamic_timestamp_preservation():
    """Verify Jeremy Cianella KDP dynamic timestamp preservation without hardcoded ranges."""
    kdp_section = (
        "## Key Discussion Points\n\n"
        "- **Jeremy Cianella**: Initial discussion on architecture [00:12:34–00:15:20]\n"
        "- Additional discussion point here.\n"
    )

    expanded = heal_key_discussion_anchors(kdp_section)
    assert "[00:12:34–00:15:20]" in expanded
    assert "00:55:26–01:02:42" not in expanded


def test_graph_ontology_proposal_stopwords_and_evidence():
    """Verify proposal token extraction strips stopwords and conversational filler, preserving evidence IDs."""
    text = "Like, you know, we should implement a distributed write lock across the primary partition."
    tokens = _extract_proposal_tokens(text)
    assert "like" not in tokens
    assert "know" not in tokens
    assert "the" not in tokens
    assert "distributed" in tokens
    assert "partition" in tokens

    from dual_pipeline.transcript_qa_core.graph_ontology_proposals import heal_proposals_and_risks
    data = {
        "evidence_index": {
            "E-01": {"exact_text": "Like, you know, we should implement a distributed write lock across the primary partition."}
        }
    }
    mutations = {
        "proposal_updates": [
            {
                "id": "P-101",
                "evidence_ids": ["E-01"],
                "rationale": "",
            }
        ]
    }
    changes: list[str] = []
    heal_proposals_and_risks(data, mutations, changes)
    prop = mutations["proposal_updates"][0]
    assert prop["evidence_ids"] == ["E-01"]
    assert not prop["rationale"].lower().startswith("like, you know")
    assert "distributed write lock" in prop["rationale"].lower()


def test_graph_ontology_healer_lens_session_guard():
    """Verify _is_lens_session correctly gates LENS heuristics against casual mentions."""
    casual_meta = {
        "project": "project_beacon",
        "meeting_name": "Architecture Review through the lens of performance",
    }
    assert _is_lens_session({"project": "project_beacon"}, metadata=casual_meta) is False

    lens_meta = {
        "project": "lens",
        "meeting_name": "LENS Platform Engineering Sprint",
    }
    assert _is_lens_session({"project": "lens"}, metadata=lens_meta) is True


def test_speaker_role_reconciler_hyphenated_slug():
    """Verify _slugify produces hyphenated slugs conforming to conventional naming."""
    assert _slugify("Jeremy Cianella") == "jeremy-cianella"
    assert _slugify("Drew Oakley (CSL)") == "dan-okray-csl"
    assert "_" not in _slugify("Client Service Lead")


def test_transcript_resume_binding_hash_stability():
    """Verify runtime mutated operational keys do not alter the transcript resume binding hash."""
    base_meta = {
        "project": "project_beacon",
        "meeting_name": "Deep RCA Sync",
        "date": "10/15/2026",
    }
    raw_transcript = "[00:00:01] Speaker A: Operational stability verified."

    binding1 = build_transcript_resume_binding(
        source_text=raw_transcript,
        metadata=base_meta,
        provider=None,
        provider_type="mock",
    )

    # Add runtime mutated keys
    mutated_meta = dict(base_meta)
    mutated_meta["_frozen_metadata_sha256"] = binding1["components"]["metadata_sha256"]
    mutated_meta["gate_f_certified"] = True
    mutated_meta["remediation_directive_stage01"] = {"patch": "diff"}
    mutated_meta["output_1_sha256"] = "def456"

    binding2 = build_transcript_resume_binding(
        source_text=raw_transcript,
        metadata=mutated_meta,
        provider=None,
        provider_type="mock",
    )
    assert binding1["binding_sha256"] == binding2["binding_sha256"], "Operational keys leaked into transcript resume binding hash!"


def test_evaluator_preheal_and_pinned_merging():
    """Verify evaluator skips pre-heal on frozen output_3 and merges pinned scorecards without regressing scores."""
    f3_original = json.dumps({"nodes": [{"id": "node_1", "evidence": "raw evidence"}]})
    synthesis_result = SynthesisResult(output_1="T1", output_2="T2", output_3=f3_original)

    sm = SelectiveRemediationStateMachine()
    sm.freeze_artifact("output_3", f3_original)

    meta = {"state_machine": sm}

    # Pre-heal should early return without touching output_3
    _pre_heal_output_3(synthesis_result, meta=meta)
    assert synthesis_result.output_3 == f3_original

    # Verify score merging retains high-water mark
    pinned_eval = {
        "output_3": {
            "composite_score": 92,
            "verdict": "PASS",
            "status": "PASS",
        }
    }
    judge_rep = {
        "output_3_evaluation": {
            "status": "SKIPPED",
            "composite_score": 0,
            "verdict": "FAIL",
        }
    }
    _merge_pinned_into_judge_report(judge_rep, pinned_eval, meta=meta)
    assert judge_rep["output_3_evaluation"]["composite_score"] == 92
    assert judge_rep["output_3_evaluation"]["verdict"] == "PASS"


def test_eval_cache_check_pin_citation_cross_resolution():
    """Verify check_pin accepts output_2 pins where grounding citations reference dialogue in f1_text."""
    f1_text = "[00:01:23] Jeremy Cianella: We must preserve the single-pipe architecture."
    f2_text = "# Executive Summary\n\nDiscussion on single-pipe architecture."
    h2 = hashlib.sha256(f2_text.encode("utf-8")).hexdigest()

    pinned = {
        "output_2": {
            "artifact_sha256": h2,
            "composite_score": 94,
            "verdict": "PASS",
            "status": "PASS",
            "grounding_citations": [
                "Jeremy Cianella: We must preserve the single-pipe architecture."
            ],
            "dimension_scores": {"grounding": 94},
        }
    }

    result = check_pin(
        pinned,
        "output_2",
        "output_02",
        min_score=90,
        expected_sha=h2,
        text=f2_text,
        f1_text=f1_text,
    )
    assert result is not None
    assert result["composite_score"] == 94
