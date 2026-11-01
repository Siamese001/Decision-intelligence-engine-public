#!/usr/bin/env python3
"""
Comprehensive QA Gate Evaluation for Conduit Lessons Learned artifacts.
Evaluates Output 1, Output 2, Output 3, and cross-artifact consistency.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_stages import XExitGatesEvaluator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate_conduit_outputs")

OUTPUT_DIR = WORKSPACE_ROOT / "output" / "conduit_lessons_learned"
BASE_NAME = "Conduit_Lessons_Learned_08-31-2026_0100PM_0147PM_ET"

F1_PATH = OUTPUT_DIR / f"01_{BASE_NAME}_hitl_resolved_transcript.md"
F2_PATH = OUTPUT_DIR / f"02_{BASE_NAME}_executive_recap.md"
F3_PATH = OUTPUT_DIR / f"03_{BASE_NAME}_canonical_signal_projection.json"


def test_artifact_01():
    text = F1_PATH.read_text(encoding="utf-8")
    assert "# Conduit_Lessons_Learned -- HITL-Resolved Transcript" in text, "Missing title in Output 1"
    assert "**Date:** 08/31/2026" in text, "Missing date in Output 1"
    assert "**Platform Reference:** Microsoft Teams" in text, "Missing platform in Output 1"
    assert "**Participants:**" in text, "Missing participants in Output 1"
    assert "## Ambiguity Lock Register" in text, "Missing Ambiguity Lock Register in Output 1"
    assert "## Evidence Locks / Semantic Constraints" in text, "Missing Evidence Locks in Output 1"
    assert "**Amit Ayer:**" in text, "Missing Amit Ayer in Output 1"
    assert "**Michael Maione:**" in text, "Missing Michael Maione in Output 1"
    assert "**Siara Montgelas:**" in text, "Missing Siara Montgelas in Output 1"
    assert "**Mike Homorody:**" in text, "Missing Mike Homorody in Output 1"
    logger.info("Artifact 01 QA validation: PASS")


def test_artifact_02():
    text = F2_PATH.read_text(encoding="utf-8")
    assert "# Subject: Conduit_Lessons_Learned -- Executive Strategic & Technical Brief | 08/31/2026" in text
    assert "**Date:** 08/31/2026" in text
    assert "**Time:** 01:00 PM - 01:47 PM ET" in text
    assert "## Meeting Objective" in text
    assert "## Key Discussion Points & Findings" in text
    assert "## Next Steps & Action Items" in text
    assert "## Open Decisions / Blockers" in text
    assert "## Business Meaning and Value Case" in text
    assert "## Executive Decision" in text
    assert "## Bounded Execution Wedge" in text

    # Check action item table
    action_table_match = re.search(r"## Next Steps & Action Items\s*\n\s*(\|.+?\|\n\s*\|[-:| ]+\|\n(?:\|.+?\|\n)+)", text)
    assert action_table_match, "Action item table not found or improperly formatted"

    # Active verb validation
    rows = [line.strip() for line in action_table_match.group(1).splitlines() if line.strip() and not line.strip().startswith("| #") and not line.strip().startswith("|---")]
    assert len(rows) >= 2, f"Expected at least 2 action items, got {len(rows)}"

    active_verbs = {"publish", "locate", "document", "confirm", "assign", "build", "define", "deliver", "escalate", "identify", "map", "measure", "obtain", "resolve", "test", "validate", "create"}
    for r in rows:
        cells = [c.strip() for c in r.split("|") if c.strip()]
        action_text = cells[1]
        first_word = re.match(r"^[A-Za-z]+", action_text).group(0).lower()
        assert first_word in active_verbs, f"Action '{action_text}' does not start with approved active verb (got '{first_word}')"

    logger.info(f"Artifact 02 QA validation: PASS ({len(rows)} action items validated)")


def test_artifact_03():
    data = json.loads(F3_PATH.read_text(encoding="utf-8"))
    assert data["schema_version"] == "5.7"
    assert data["classification"] == "confidential"
    assert "nee-engineering" in data["audiences"]
    assert "evidence_index" in data
    assert "graph_mutations" in data

    # Validate Pydantic model
    CanonicalSignalProjectionV57.model_validate(data)

    # Check evidence ceiling (C-17)
    promoted_count = (
        len(data["graph_mutations"]["entity_upserts"]) +
        len(data["graph_mutations"]["relationship_signals"]) +
        len(data["graph_mutations"]["decision_updates"]) +
        len(data["graph_mutations"]["proposal_updates"]) +
        len(data["graph_mutations"]["action_updates"]) +
        len(data["graph_mutations"]["risk_updates"])
    )
    evidence_only = data.get("evidence_only_statement_ids", [])
    max_allowed = max(3, 2 * promoted_count)
    assert len(evidence_only) <= max_allowed, f"Evidence ceiling exceeded: {len(evidence_only)} > {max_allowed}"

    # Check exact text matches for all evidence items
    f1_text = F1_PATH.read_text(encoding="utf-8")
    for ev_id, ev in data["evidence_index"].items():
        assert ev["exact_text"] in f1_text, f"Evidence {ev_id} exact text not found in Output 1"
        assert ev["start_offset"] == f1_text.find(ev["exact_text"]), f"Offset mismatch for {ev_id}"
        assert ev["end_offset"] == ev["start_offset"] + len(ev["exact_text"]), f"End offset mismatch for {ev_id}"
        assert ev["kind"] in {"summary", "decision", "risk", "dependency", "technical", "strategic"}, f"Invalid kind: {ev['kind']}"

    # Check temporal formatting MM/DD/YYYY
    for dec in data["graph_mutations"]["decision_updates"]:
        assert re.match(r"^\d{2}/\d{2}/\d{4}$", dec["observed_at"]), f"Invalid date: {dec['observed_at']}"
        assert re.match(r"^\d{2}/\d{2}/\d{4}$", dec["stale_after"]), f"Invalid date: {dec['stale_after']}"
    for rsk in data["graph_mutations"]["risk_updates"]:
        assert re.match(r"^\d{2}/\d{2}/\d{4}$", rsk["observed_at"]), f"Invalid date: {rsk['observed_at']}"
        assert re.match(r"^\d{2}/\d{2}/\d{4}$", rsk["stale_after"]), f"Invalid date: {rsk['stale_after']}"
    for act in data["graph_mutations"]["action_updates"]:
        if act.get("due_date"):
            assert re.match(r"^\d{2}/\d{2}/\d{4}$", act["due_date"]), f"Invalid due_date: {act['due_date']}"

    logger.info("Artifact 03 QA validation: PASS (Pydantic, C-17 ceiling, offsets, temporal hygiene)")


def test_exit_gates():
    evaluator = XExitGatesEvaluator()
    recap_text = F2_PATH.read_text(encoding="utf-8")
    graph_json = F3_PATH.read_text(encoding="utf-8")
    metadata = {
        "meeting_name": "Conduit_Lessons_Learned",
        "date": "08/31/2026",
        "time": "01:00 PM - 01:47 PM ET",
        "classification": "confidential",
        "prior_entity_ids": [
            "workstream:pd-conduit",
            "person:amit-ayer",
            "person:michael-maione",
            "person:mike-homorody",
            "org:partner_advisory",
            "org:nea",
            "org:nee"
        ]
    }
    assert evaluator.execute(recap_text, graph_json, metadata) is True
    logger.info("XExitGatesEvaluator validation: PASS")


def main():
    logger.info("Executing comprehensive QA gate suite...")
    test_artifact_01()
    test_artifact_02()
    test_artifact_03()
    test_exit_gates()
    print("\n" + "=" * 80)
    print("ALL QA GATES PASSED (100% COMPLIANT)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
