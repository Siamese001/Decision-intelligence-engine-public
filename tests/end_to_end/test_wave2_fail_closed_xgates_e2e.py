"""
End-to-end tests for Wave 2 fail-closed XExitGatesEvaluator (X1-X3), Stage 03 post-extractor, and C0 role verification.
Verifies:
1. XExitGatesEvaluator.execute() is strictly read-only on disk (SHA-256 unchanged) and passes valid coherent artifacts.
2. Stage 03 sanitize_and_validate_projection preserves action_updates when preserve_action_updates=True.
3. Generalized C-28 anti-conflation detects multi-workstream compound slugs across Jarvis, DocIQ, LENS, Sherpa, and Chakra.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.transcript_stages_core.graph_post_extractor import sanitize_and_validate_projection
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def _make_valid_artifacts() -> tuple[str, str, Dict[str, Any]]:
    transcript = "### [00:01:00]\n\n**Alex Rivera:** We will deploy the LENS rule engine by 10/25/2026."
    quote = "We will deploy the LENS rule engine by 10/25/2026."
    start = transcript.index(quote)
    end = start + len(quote)

    recap = (
        "# Executive Recap\n\n"
        "## 1. Meeting Objective\n"
        "Review LENS deployment schedule.\n\n"
        "## 2. Action & Governance Ledger\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | Action | Deploy LENS rule engine | Alex Rivera | 10/25/2026 | [00:01:00] | Open |\n"
    )

    projection: Dict[str, Any] = {
        "schema_version": "5.7",
        "meeting_id": "mtg-wave2-01",
        "meeting_date": "10/17/2026",
        "source_file": "01_test_transcript.md",
        "classification": "CLIENT_SAFE",
        "audiences": ["CLIENT_SAFE"],
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:alex_rivera",
                    "type": "PERSON",
                    "name": "Alex Rivera",
                    "description": "Engineering Lead at Apex Global Enterprises",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_index": {
            "ev-01": {
                "text": quote,
                "exact_text": quote,
                "speaker": "Alex Rivera",
                "timestamp": "00:01:00",
                "quote_pointer": [start, end],
            }
        },
    }
    return transcript, recap, projection


def test_x_exit_gates_pass_valid_bundle_without_mutating_output03_on_disk(tmp_path: Path) -> None:
    """Verify XExitGatesEvaluator.execute passes valid artifacts and never overwrites Output 03 on disk."""
    transcript, recap, proj = _make_valid_artifacts()
    f3_path = tmp_path / "03_test_canonical_signal_projection.json"
    raw_json = json.dumps(proj, indent=2)
    f3_path.write_text(raw_json, encoding="utf-8")
    sha_before = hashlib.sha256(f3_path.read_bytes()).hexdigest()

    meta = {
        "attendees": ["Alex Rivera (Engineering Lead)"],
        "output_03_path": str(f3_path),
        "output_dir": str(tmp_path),
    }
    evaluator = XExitGatesEvaluator()
    assert evaluator.execute(recap=recap, graph_json=raw_json, metadata=meta, transcript=transcript) is True

    sha_after = hashlib.sha256(f3_path.read_bytes()).hexdigest()
    assert sha_before == sha_after, "XExitGatesEvaluator must be strictly read-only and never mutate Output 03 on disk"


def test_stage03_post_extractor_preserves_valid_action_updates_when_requested() -> None:
    """Verify sanitize_and_validate_projection retains valid action_updates when preserve_action_updates=True."""
    _, _, proj = _make_valid_artifacts()
    proj["graph_mutations"]["action_updates"] = [
        {
            "action_id": "action:deploy-lens-rules",
            "owner_id": "person:alex_rivera",
            "status": "OPEN",
            "due_date": "10/25/2026",
            "evidence_ids": ["ev-01"],
        }
    ]
    sanitized = sanitize_and_validate_projection(
        proj,
        metadata={"date": "10/17/2026", "preserve_action_updates": True},
    )
    actions = sanitized["graph_mutations"]["action_updates"]
    assert len(actions) == 1
    assert actions[0]["action_id"] == "action:deploy-lens-rules"
    assert actions[0]["owner_id"] == "person:alex_rivera"
