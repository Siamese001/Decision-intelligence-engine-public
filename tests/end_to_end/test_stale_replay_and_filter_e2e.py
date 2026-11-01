"""
tests/end_to_end/test_stale_replay_and_filter_e2e.py

End-to-End and Adversarial Edge-Case Verification Suite for:
1. Cross-worktree seeding rejection of failed/stale primary repo artifacts and --regenerate targets.
2. Zero-mutation text remediation directive bypassing surgical patch short-circuit and invoking LLM extraction.
3. [HH:MM:SS] Speaker: ** dialogue turn parsing and verbatim quote retention in statement_filter.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
)
from dual_pipeline.canonical_pipeline.worktree_seeding import (
    seed_prior_artifacts_into_worktree,
)
_seed_prior_artifacts_into_worktree = seed_prior_artifacts_into_worktree
from dual_pipeline.transcript_pipeline_core.statement_filter import (
    filter_stage03_statement_payload,
)
from dual_pipeline.transcript_qa_core.json_surgical_patcher import JsonSurgicalPatcher


@pytest.mark.e2e
def test_cross_worktree_seeding_rejects_unapproved_or_regenerated_artifacts(tmp_path: Path):
    """
    Invariant: _seed_prior_artifacts_into_worktree must never copy failed
    final_release_decision.json, attempts/, or deliverables targeted by --regenerate.
    """
    src_dir = tmp_path / "primary_output"
    src_dir.mkdir(parents=True, exist_ok=True)
    dst_dir = tmp_path / "worktree_output"
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Seed files in primary repo output
    (src_dir / "01_slug_01-01-2026_hitl_resolved_transcript.md").write_text("transcript content", encoding="utf-8")
    (src_dir / "02_slug_01-01-2026_executive_recap.md").write_text("recap content", encoding="utf-8")
    (src_dir / "03_slug_01-01-2026_canonical_signal_projection.json").write_text('{"graph_mutations": {}}', encoding="utf-8")
    (src_dir / "final_release_decision.json").write_text('{"status": "FAIL", "authoritative_write_authorized": false}', encoding="utf-8")
    
    src_attempts = src_dir / "attempts" / "attempt_1"
    src_attempts.mkdir(parents=True, exist_ok=True)
    (src_attempts / "output_3.ckpt.json").write_text('{"graph_mutations": {}}', encoding="utf-8")

    # Case 1: Seed with status FAIL in src_dec -> final_release_decision.json and attempts/ must NOT be copied
    meta_case1 = {"regenerate": "03"}
    _seed_prior_artifacts_into_worktree(src_dir, dst_dir, meta=meta_case1)

    assert (dst_dir / "01_slug_01-01-2026_hitl_resolved_transcript.md").is_file()
    assert (dst_dir / "02_slug_01-01-2026_executive_recap.md").is_file()
    # 03 is in regenerate scope -> must NOT be seeded
    assert not (dst_dir / "03_slug_01-01-2026_canonical_signal_projection.json").exists()
    # failed decision must NOT be seeded
    assert not (dst_dir / "final_release_decision.json").exists()
    # attempts must NOT be seeded
    assert not (dst_dir / "attempts").exists()


@pytest.mark.e2e
def test_zero_mutation_text_directive_bypasses_surgical_patch_and_invokes_llm(tmp_path: Path):
    """
    Invariant: When remediation_directive_graph is plain text without structured JSON ops,
    JsonSurgicalPatcher returns patch_applied=False / NO_OP, and _resolve_output_3 proceeds
    to self.l2_graph.execute instead of short-circuiting.
    """
    initial_json = json.dumps({
        "graph_mutations": {
            "entity_upserts": [{"id": "entity_1", "entity_type": "PROJECT"}],
            "relationship_signals": [],
        },
        "evidence_index": {},
    })

    # Test JsonSurgicalPatcher directly on plain text directive
    healed, audit = JsonSurgicalPatcher.apply_patch(
        original_json=initial_json,
        critique_diff_or_envelope="Please ensure that Alex Rivera is mapped to Partner Solutions.",
    )
    assert audit["patch_applied"] is False

    # Test FullMeetingSynthesisStrategy._resolve_output_3
    mock_provider = MagicMock()
    strategy = FullMeetingSynthesisStrategy(mock_provider)
    strategy.l2_graph = MagicMock()
    strategy.l2_graph.execute.return_value = json.dumps({
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:mitchell_simmons",
                    "entity_type": "PERSON",
                    "name": "Alex Rivera",
                    "description": "Lead Architect",
                },
                {
                    "id": "project:cloud_migration",
                    "entity_type": "PROJECT",
                    "name": "Cloud Migration",
                    "description": "Enterprise cloud migration",
                },
            ],
            "relationship_signals": [
                {
                    "from_id": "person:mitchell_simmons",
                    "to_id": "project:cloud_migration",
                    "relation_type": "LEADS",
                }
            ],
        },
        "evidence_index": {},
    })

    ckpt_dir = tmp_path / "attempts" / "attempt_2"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    prev_ckpt_dir = tmp_path / "attempts" / "attempt_1"
    prev_ckpt_dir.mkdir(parents=True, exist_ok=True)
    (prev_ckpt_dir / "output_3.ckpt.json").write_text(initial_json, encoding="utf-8")
    (prev_ckpt_dir / "final_release_decision.json").write_text('{"status": "FAIL", "output_3_pass": false}', encoding="utf-8")

    meta = {
        "regenerate": "03",
        "resume": True,
        "remediation_directive_graph": "Add Alex Rivera to the partner map.",
    }
    pa_context = {"metadata": meta, "output_dir": str(tmp_path)}

    result = strategy._resolve_output_3(
        forensic_transcript="Forensic transcript text with Alex Rivera",
        executive_recap="Recap text",
        pa_context=pa_context,
        attempt=2,
        remediation_directive=meta["remediation_directive_graph"],
        progress_notify=None,
        ckpt_dir=ckpt_dir,
    )

    # Must invoke LLM extraction rather than reusing unmutated initial_json
    assert strategy.l2_graph.execute.called
    data = json.loads(result)
    entity_ids = [e["id"] for e in data["graph_mutations"]["entity_upserts"]]
    assert "person:mitchell_simmons" in entity_ids


@pytest.mark.e2e
def test_statement_filter_parses_timestamp_speaker_turns_and_preserves_dialogue():
    """
    Invariant: filter_stage03_statement_payload parses Output 01 format:
    [HH:MM:SS] Speaker: ** or [HH:MM:SS] **Speaker:** and preserves dialogue turns.
    """
    transcript = """## Forensic Transcript

[00:00:15] Speaker_Alpha: We have decided to migrate our Terraform modules to AWS and GCP.
[00:01:30] **Alex Rivera**: That matches the enterprise architecture decision we approved yesterday.
[00:02:10] Tenika: How is the weather in Miami today? It is raining so hard.
[00:03:00] Speaker_Beta: We confirmed the budget and timeline deliverable is set for Friday.
"""

    filtered = filter_stage03_statement_payload(transcript, max_budget_chars=5000)

    # Technical statements and decisions must be preserved
    assert "Speaker_Alpha" in filtered
    assert "Terraform" in filtered
    assert "Alex Rivera" in filtered
    assert "enterprise architecture decision" in filtered
    assert "Speaker_Beta" in filtered
    assert "budget and timeline" in filtered

    # Pure social banter ("weather in Miami") must be filtered out
    assert "weather in Miami" not in filtered


@pytest.mark.e2e
def test_adversarial_boundary_payloads_in_statement_filter():
    """Adversarial coverage: Empty, malformed, and single-turn inputs."""
    # 1. Empty payload
    assert filter_stage03_statement_payload("") == ""
    assert filter_stage03_statement_payload("   ") == ""

    # 2. Malformed non-splitting text within budget
    short_text = "Single unstructured paragraph discussing AWS Terraform pipeline architecture."
    assert filter_stage03_statement_payload(short_text, max_budget_chars=1000) == short_text

    # 3. Non-splitting text exceeding budget
    long_banter = "how are you doing today nice to meet you " * 50
    filtered_banter = filter_stage03_statement_payload(long_banter, max_budget_chars=100)
    assert len(filtered_banter) <= 100
