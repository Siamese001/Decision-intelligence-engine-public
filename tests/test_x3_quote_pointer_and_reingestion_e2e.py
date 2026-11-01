"""tests/test_x3_quote_pointer_and_reingestion_e2e.py

End-to-End and Adversarial test suite for Wave 7:
1. X3 Quote Pointer Integrity: Verifies non-transcript evidence (locators with region/attachment)
   is never overwritten with transcript dialogue spans.
2. X3 Attendee & DRI Parity: Verifies org-grouped attendee dictionaries, silent attendee upsert in graph_healer,
   and C-25 "Unassigned (Pending Owner Assignment)" DRI exemption.
3. Idempotent Re-Ingestion & Directive Versioning: Re-publishing an already completed directive increments
   to -v2..N and purges stale ACTIVATED mutation receipts.
4. Gate 2.9 & Gate Cross-5 Collateral Fidelity: Ensures reviewed screenshots in collateral_drop_log.json
   are strictly enforced in Output 02 and Output 03.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import (
    resolve_next_directive_id,
    verify_exit_qa,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    verify_x3_cross_output_coherence,
)
from dual_pipeline.transcript_qa_core.consistency import run_cross_artifact_consistency
from dual_pipeline.transcript_qa_core.gate2 import (
    check_gate_2_9_collateral,
    run_qa_gate_2,
)
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.quote_pointer_healer import (
    _is_non_transcript_evidence,
    heal_quote_pointers,
)


def test_quote_pointer_exempts_non_transcript_attachment_and_region():
    """Verify that attachment quotes and regions are never overwritten with transcript dialogue."""
    data = {
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "evidence_id": "ev-01",
                "speaker": "Alex Rivera",
                "text": "We are fully aligned across both teams.",
                "exact_text": "We are fully aligned across both teams.",
                "locator": {"kind": "timestamp", "value": "00:05:10"},
                "quote_pointer": None,
            },
            "ev-att-01": {
                "statement_id": "ev-att-01",
                "evidence_id": "ev-att-01",
                "speaker": "attachment:mock_prototype.png",
                "text": "Live telemetry overview panel showing alert levels.",
                "exact_text": "Live telemetry overview panel showing alert levels.",
                "locator": {"kind": "region", "value": "attachment:mock_prototype.png"},
                "quote_pointer": None,
                "attribution_mode": "COLLATERAL_REVIEWED",
            },
        }
    }
    transcript = (
        "**Alex Rivera:** We are fully aligned across both teams.\n\n"
        "**Will Booth:** Agreed, this prototype proves the single view concept."
    )

    heal_quote_pointers(data, transcript=transcript)

    # ev-01 should be re-anchored to dialogue span
    ev1 = data["evidence_index"]["ev-01"]
    assert ev1["quote_pointer"] is not None
    span = transcript[ev1["quote_pointer"][0]:ev1["quote_pointer"][1]]
    assert span == ev1["exact_text"]

    # ev-att-01 must remain intact and exempt from transcript re-anchoring
    ev_att = data["evidence_index"]["ev-att-01"]
    assert ev_att["quote_pointer"] is None
    assert _is_non_transcript_evidence(ev_att) is True


def test_x3_coherence_handles_org_grouped_attendees_and_unassigned_dri():
    """Verify X3 attendee parity flattens org-grouped dicts and exempts unassigned DRI rows."""
    f1_text = "**Alex Rivera:** Hello team.\n**Peter:** Hi Mitchell."
    f2_text = (
        "# 1. Meeting Objective\n\nReview prototype.\n\n"
        "# 2. Action & Governance Ledger\n\n"
        "| # | Workstream | Action Item | Owner | Target Date |\n"
        "| 1 | Architecture | Ratify schema | Alex Rivera | 10/20/2026 |\n"
        "| 2 | Operations | Deploy agents | Unassigned (Pending Owner Assignment) | 10/25/2026 |\n\n"
        "# 3. Key Discussion Points & Findings\n\nDiscussion."
    )
    metadata = {
        "attendees": {
            "Apex Global Enterprises": ["Alex Rivera"],
            "PartnerAdvisory": ["Peter", "Amit Ayer (Silent Attendee)"],
        }
    }
    f3_data = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:mitchell_simmons", "type": "person", "name": "Alex Rivera"},
                {"id": "person:peter", "type": "person", "name": "Peter"},
            ],
            "relationship_signals": [],
        },
        "evidence_index": {},
    }

    # First verify heal_graph_projection deterministically upserts the silent attendee
    changes = []
    heal_graph_projection(f3_data, changes, metadata=metadata, output_1=f1_text)

    upsert_ids = {e["id"] for e in f3_data["graph_mutations"]["entity_upserts"]}
    assert "person:mitchell_simmons" in upsert_ids
    assert "person:peter" in upsert_ids
    assert "person:amit_ayer" in upsert_ids or "person:amit-ayer" in upsert_ids

    # Run X3 coherence check
    passed, errors = verify_x3_cross_output_coherence(
        f3_data=f3_data,
        metadata=metadata,
        f1_text=f1_text,
        f2_text=f2_text,
    )
    assert passed is True, f"X3 Coherence failed: {errors}"


def test_resolve_next_directive_id_auto_increments_completed_directives():
    """Verify idempotent directive versioning increments -v1 to -v2 when prior attempt completed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        rt = Path(tmp_dir)
        d_dir = rt / "ingestion_directives"
        d_dir.mkdir(parents=True, exist_ok=True)

        base_id = "directive-knowledge-test_meeting-10-18-2026-v1"

        # When no prior directive exists, returns base_id
        assert resolve_next_directive_id(rt, base_id) == base_id

        # Record completed directive for v1
        v1_file = d_dir / f"{base_id}.json"
        v1_file.write_text(json.dumps({"directive_id": base_id, "status": "COMPLETE"}), encoding="utf-8")

        # Next resolution should automatically yield -v2
        v2_id = resolve_next_directive_id(rt, base_id)
        assert v2_id == "directive-knowledge-test_meeting-10-18-2026-v2"

        # Record completed directive for v2
        v2_file = d_dir / f"{v2_id}.json"
        v2_file.write_text(json.dumps({"directive_id": v2_id, "status": "COMPLETE"}), encoding="utf-8")

        # Next resolution should yield -v3
        v3_id = resolve_next_directive_id(rt, base_id)
        assert v3_id == "directive-knowledge-test_meeting-10-18-2026-v3"


def test_gate_2_9_and_cross_5_collateral_drop_log_enforcement():
    """Verify Gate 2.9 and Gate Cross-5 inspect collateral_drop_log.json on disk and enforce coverage."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox = Path(tmp_dir)
        drop_log = sandbox / "collateral_drop_log.json"
        drop_log.write_text(
            json.dumps([
                {"filename": "dashboard_overview.png", "attribution_mode": "COLLATERAL_REVIEWED", "reviewed": True},
                {"filename": "event_drawer.png", "attribution_mode": "COLLATERAL_REVIEWED", "reviewed": True},
            ]),
            encoding="utf-8",
        )

        meta = {
            "output_dir": str(sandbox),
            "attachments": [
                {"filename": "dashboard_overview.png", "attribution_mode": "COLLATERAL_REVIEWED"},
                {"filename": "event_drawer.png", "attribution_mode": "COLLATERAL_REVIEWED"},
            ],
            "duration": "00:30:00",
        }

        # 1. Output 2 missing one screenshot should fail Gate 2.9
        incomplete_f2 = (
            "# 1. Meeting Objective\n\nReview.\n\n"
            "# 2. Action & Governance Ledger\n\n| # | Workstream | Action Item | Owner | Target Date |\n\n"
            "# 3. Key Discussion Points & Findings\n\n"
            "![Dashboard](referenced_artifacts/dashboard_overview.png)\n"
        )
        passed_29, is_applicable, fails_29, _ = check_gate_2_9_collateral(incomplete_f2, meta=meta)
        assert passed_29 is False
        assert any("event_drawer.png" in f for f in fails_29)

        # Complete Output 2 should pass Gate 2.9
        complete_f2 = incomplete_f2 + "![Event Drawer](referenced_artifacts/event_drawer.png)\n"
        passed_29, _, fails_29, _ = check_gate_2_9_collateral(complete_f2, meta=meta)
        assert passed_29 is True

        # 2. Gate Cross-5 verifies evidence propagation for all reviewed screenshots
        f3_data = {
            "schema_version": "5.7",
            "meeting_id": "test-meet",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {
                "ev-att-01": {
                    "statement_id": "ev-att-01",
                    "evidence_id": "ev-att-01",
                    "speaker": "attachment:dashboard_overview.png",
                    "locator": {"kind": "region", "value": "attachment:dashboard_overview.png"},
                    "attribution_mode": "COLLATERAL_REVIEWED",
                },
                "ev-att-02": {
                    "statement_id": "ev-att-02",
                    "evidence_id": "ev-att-02",
                    "speaker": "attachment:event_drawer.png",
                    "locator": {"kind": "region", "value": "attachment:event_drawer.png"},
                    "attribution_mode": "COLLATERAL_REVIEWED",
                },
            },
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_only_statement_ids": [],
        }
        res_cross = run_cross_artifact_consistency(
            q1={"passed": True},
            q2={"passed": True},
            q3={"passed": True},
            f1_text="Sample text",
            f2_text=complete_f2,
            f3_text=json.dumps(f3_data),
            metadata=meta,
        )
        assert res_cross["passed"] is True, f"Cross-artifact QA failed: {res_cross.get('failures')}"
