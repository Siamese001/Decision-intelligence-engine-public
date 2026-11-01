"""
Wave 2 E2E Test: Track 2 Partial Resume (`Outputs 01 & 02 -> Output 03`) & Cross-Artifact Action Parity.

Verifies:
1. Running `--resume` or `--output3-only` with completed `01_*` and `02_*` files leaves both files
   byte-identical on disk, runs zero LLM calls for Stages 01/02 or Micro-Judges 1/2, and synthesizes only `03_*`.
2. `_heal_orphan_entities` heals unconnected `person:` nodes without raising `NameError` (`DEF-T2-003`).
3. `prepare_evidence_envelope_intake.py` populates `package["actions"]` from Output 02's
   `# Action & Governance Ledger` while Output 03's `action_updates` remains `[]` per Gate 3.5 (`DEF-T2-006`).
4. `_build_single_entity_candidate` preserves entity `description` and `attributes` (`DEF-T2-008`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _memoize_output_1,
    _memoize_output_2,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
)
from dual_pipeline.cli.ingest import _apply_semantic_flags
from dual_pipeline.normalization_core.meeting_entities import (
    build_entity_upsert_candidates,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)
from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_orphan_entities,
    heal_graph_projection,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
from tools.prepare_evidence_envelope_intake import (
    _adapt_v53_projection_to_envelope,
    parse_output02_action_ledger,
)


SAMPLE_OUTPUT_01 = """# Forensic Normalizer — HITL Resolved Transcript

**Meeting:** Apex Global Partner Alliances Sync
**Project:** partner_alliances
**Date:** 10/20/2026
**Time:** 10:00 AM - 10:45 AM ET
**Platform:** Microsoft Teams
**Classification:** Confidential

### [00:00:05]

**Amit Ayer:** We are aligning the Q4 joint partner roadmap for Apex Global grid modernization today.

### [00:01:12]

**Speaker_Alpha McElroy:** Google Cloud committed two principal architects to validate the Vertex AI telemetry pipeline by November 15.

### [00:02:30]

**Amit Ayer:** Let us lock the architecture review milestone and assign Speaker_Alpha McElroy as the directly responsible owner.
"""

SAMPLE_OUTPUT_02 = """# Executive Recap — Apex Global Partner Alliances Sync

**Date:** 10/20/2026
**Meeting:** Apex Global Partner Alliances Sync

## Executive Summary
Amit Ayer and Speaker_Alpha McElroy aligned on the Q4 Apex Global grid modernization roadmap and Vertex AI telemetry validation.

# Action & Governance Ledger

| Action Item | Owner / DRI | Due Date | Status |
| :--- | :--- | :--- | :--- |
| Validate Vertex AI telemetry pipeline with two principal architects | Speaker_Alpha McElroy | 2026-11-15 | planned |
| Circulate signed Q4 joint partner architecture blueprint | Amit Ayer | 2026-10-24 | open |
"""


def test_track2_memoizes_unsealed_output02_on_resume_and_output3_only() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t2_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_10-20-2026_partner_alliances_hitl_resolved_transcript.md"
        f2_path = out_dir / "02_10-20-2026_partner_alliances_executive_recap.md"
        f1_path.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")
        f2_path.write_text(SAMPLE_OUTPUT_02, encoding="utf-8")

        sha1_pre = hashlib.sha256(f1_path.read_bytes()).hexdigest()
        sha2_pre = hashlib.sha256(f2_path.read_bytes()).hexdigest()

        args = argparse.Namespace(
            input=str(f1_path),
            document=False,
            notes=False,
            copilot_notes=False,
            output3_only=True,
            command="ingest-meeting",
            regenerate=None,
        )
        meta: dict = {"output_dir": str(out_dir)}
        _apply_semantic_flags(meta, args)

        assert meta["output3_only"] is True
        assert meta["regenerate"] == "03"
        assert meta["resume"] is True

        sm = SelectiveRemediationStateMachine()
        assert _memoize_output_1(out_dir, sm, meta, meta["regenerate"]) is True
        assert _memoize_output_2(out_dir, sm, meta, meta["regenerate"]) is True
        assert sm.is_frozen("output_1") is True
        assert sm.is_frozen("output_2") is True

        mock_l2_forensic = MagicMock()
        mock_l2_recap = MagicMock()
        mock_l2_graph = MagicMock()
        mock_l2_graph.execute.return_value = json.dumps(
            {
                "meeting_id": "meeting-partner-alliances-20261020",
                "title": "Apex Global Partner Alliances Sync",
                "date": "2026-10-20",
                "evidence_index": {
                    "ev-01": {
                        "speaker": "Speaker_Alpha McElroy",
                        "exact_text": "Google Cloud committed two principal architects to validate the Vertex AI telemetry pipeline by November 15.",
                        "locator": {"kind": "timestamp", "value": "00:01:12"},
                    }
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "org:partner_advisory",
                            "type": "org",
                            "name": "PartnerAdvisory",
                            "description": "Consulting partner leading grid modernization.",
                            "evidence_ids": ["ev-01"],
                        },
                        {
                            "id": "person:brien-mcelroy",
                            "type": "person",
                            "name": "Speaker_Alpha McElroy",
                            "description": "Partner Alliances Lead at Google Cloud.",
                            "evidence_ids": ["ev-01"],
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "person:brien-mcelroy",
                            "to_id": "org:partner_advisory",
                            "predicate": "AFFILIATED_WITH",
                            "evidence_ids": ["ev-01"],
                        }
                    ],
                    "action_updates": [],
                },
            }
        )

        strategy = FullMeetingSynthesisStrategy(
            provider=MagicMock(),
            l2_forensic=mock_l2_forensic,
            l2_recap=mock_l2_recap,
            l2_graph=mock_l2_graph,
        )
        pa_context = {"state_machine": sm, "output_dir": str(out_dir), "metadata": meta}
        res = strategy.synthesize(
            source_text=SAMPLE_OUTPUT_01,
            pa_context=pa_context,
            metadata=meta,
            attempt=1,
        )

        mock_l2_forensic.execute.assert_not_called()
        mock_l2_recap.execute.assert_not_called()
        mock_l2_graph.execute.assert_called_once()
        assert res.output_1 == SAMPLE_OUTPUT_01
        assert res.output_2 == SAMPLE_OUTPUT_02
        assert hashlib.sha256(f1_path.read_bytes()).hexdigest() == sha1_pre
        assert hashlib.sha256(f2_path.read_bytes()).hexdigest() == sha2_pre


def test_track2_heal_orphan_entities_and_output02_action_ledger_extraction() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t2_actions_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_10-20-2026_partner_alliances_hitl_resolved_transcript.md"
        f2_path = out_dir / "02_10-20-2026_partner_alliances_executive_recap.md"
        f3_path = out_dir / "03_10-20-2026_partner_alliances_canonical_signal_projection.json"
        f1_path.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")
        f2_path.write_text(SAMPLE_OUTPUT_02, encoding="utf-8")

        draft_data = {
            "meeting_id": "meeting-partner-alliances-20261020",
            "title": "Apex Global Partner Alliances Sync",
            "date": "2026-10-20",
            "evidence_index": {
                "ev-01": {
                    "speaker": "Speaker_Alpha McElroy",
                    "exact_text": "Google Cloud committed two principal architects to validate the Vertex AI telemetry pipeline by November 15.",
                    "locator": {"kind": "timestamp", "value": "00:01:12"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "org:partner_advisory",
                        "type": "org",
                        "name": "PartnerAdvisory",
                        "description": "Consulting partner.",
                        "attributes": {"tier": "premier"},
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        "id": "person:amit-ayer",
                        "type": "person",
                        "name": "Amit Ayer",
                        "description": "Managing Director at PartnerAdvisory.",
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        "id": "person:brien-mcelroy",
                        "type": "person",
                        "name": "Speaker_Alpha McElroy",
                        "description": "Partner Alliances Lead at Google.",
                        "evidence_ids": ["ev-01"],
                    },
                ],
                "relationship_signals": [],
                "action_updates": [],
            },
        }

        changes: list[str] = []
        # Verify _heal_orphan_entities connects unconnected person nodes without raising NameError
        _heal_orphan_entities(
            draft_data,
            draft_data["graph_mutations"],
            changes,
            is_doc=False,
            metadata={"project": "nea"},
        )
        signals = draft_data["graph_mutations"]["relationship_signals"]
        assert len(signals) >= 2
        assert any(s.get("from_id") == "person:amit-ayer" for s in signals)
        assert any(s.get("from_id") == "person:brien-mcelroy" for s in signals)

        f3_path.write_text(json.dumps(draft_data, indent=2), encoding="utf-8")
        envelope = _adapt_v53_projection_to_envelope(
            draft_data, source_path=f1_path, draft_path=f3_path
        )
        assert len(envelope["actions"]) == 2
        assert envelope["actions"][0]["reported_owner"] == "Speaker_Alpha McElroy"
        assert envelope["actions"][0]["due_date"] == "2026-11-15"
        assert envelope["actions"][1]["reported_owner"] == "Amit Ayer"

        # Verify entity description & attributes preservation
        cands, profiles, _ = build_entity_upsert_candidates(
            entity_upserts=draft_data["graph_mutations"]["entity_upserts"],
            run_id="run-test-01",
            meeting_title="Apex Global Partner Alliances Sync",
            classification="confidential",
            audiences=["internal"],
            default_evidence_id="ev-01",
            default_locator={"kind": "timestamp", "value": "00:01:12"},
            evidence_by_id={"ev-01": {"locator": {"kind": "timestamp", "value": "00:01:12"}}},
            evidence_by_identifier={"ev-01": "ev-01"},
        )
        partner_advisory_cand = next(c for c in cands if "PartnerAdvisory" in c["normalized_text"])
        assert partner_advisory_cand["description"] == "Consulting partner."
        assert partner_advisory_cand["attributes"] == {"tier": "premier"}
