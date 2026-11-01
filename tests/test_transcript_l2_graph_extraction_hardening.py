"""Unit tests for Output 03 graph extraction hardening and zero-tolerance schema enforcement."""

from __future__ import annotations

import json

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_stages import OfflineTranscriptSynthesizer
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    sanitize_and_validate_projection,
)
from dual_pipeline.transcript_stages_core.l2_executors import (
    L2GraphExtractorExecutor,
)


def test_sanitize_and_validate_action_updates_zero_tolerance() -> None:
    raw_projection = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "classification": "confidential",
        "audiences": ["internal_consulting"],
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:mandy_hopmann", "type": "person", "name": "harper Hoffman", "mutation": "CONFIRM"}
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [
                {"action_id": "action:01", "owner": "Alex Sanchez", "task": "Deliver stabilization estimate"}
            ],
            "risk_updates": [],
        },
        "evidence_index": {},
        "evidence_only_statement_ids": [],
    }

    sanitized = sanitize_and_validate_projection(raw_projection, metadata={"date": "09/11/2026"})

    assert sanitized["graph_mutations"]["action_updates"] == []
    # Verify Pydantic validation passes
    validated = CanonicalSignalProjectionV57.model_validate(sanitized)
    assert validated.graph_mutations.action_updates == []


def test_sanitize_and_validate_temporal_date_conversion() -> None:
    raw_projection = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "classification": "confidential",
        "audiences": ["internal_consulting"],
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [
                {
                    "decision_id": "dec-01",
                    "status": "HELD",
                    "observed_at": "2026-09-11",
                    "stale_after": "2026-10-11",
                    "decision_question": "Shall Phase 2 be held?",
                    "rationale": "Holding pending stabilization estimate.",
                    "evidence_ids": [],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [
                {
                    "risk_id": "rsk-01",
                    "status": "ACTIVE",
                    "observed_at": "09-11-2026",
                    "stale_after": "10-11-2026",
                    "description": "Rule authoring tool bugs.",
                    "evidence_ids": [],
                }
            ],
        },
        "evidence_index": {},
        "evidence_only_statement_ids": [],
    }

    sanitized = sanitize_and_validate_projection(raw_projection, metadata={"date": "09/11/2026"})

    dec = sanitized["graph_mutations"]["decision_updates"][0]
    assert dec["decision_id"] == "decision:01"
    assert dec["observed_at"] == "09/11/2026"
    assert dec["stale_after"] == "10/11/2026"

    rsk = sanitized["graph_mutations"]["risk_updates"][0]
    assert rsk["risk_id"] == "risk:01"
    assert rsk["observed_at"] == "09/11/2026"
    assert rsk["stale_after"] == "10/11/2026"


def test_sanitize_and_validate_markdown_fence_stripping() -> None:
    raw_fenced_json = """```json
{
  "schema_version": "5.7",
  "meeting_id": "fenced-meeting",
  "classification": "internal",
  "audiences": ["partner_advisory"],
  "graph_mutations": {
    "entity_upserts": [],
    "relationship_signals": [],
    "decision_updates": [],
    "proposal_updates": [],
    "action_updates": [{"action_id": "act-1"}],
    "risk_updates": []
  },
  "evidence_index": {},
  "evidence_only_statement_ids": []
}
```"""

    sanitized = sanitize_and_validate_projection(raw_fenced_json, metadata={"date": "09/11/2026"})

    assert sanitized["schema_version"] == "5.7"
    assert sanitized["meeting_id"] == "fenced-meeting"
    assert sanitized["graph_mutations"]["action_updates"] == []


def test_l2_graph_extractor_executor_sanitization_integration() -> None:
    provider = OfflineTranscriptSynthesizer()
    extractor = L2GraphExtractorExecutor(provider)

    pa_context = {
        "metadata": {
            "meeting_id": "test-offline-meeting",
            "date": "09/11/2026",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
        }
    }

    result_json_str = extractor.execute(
        forensic_transcript="**harper Hoffman:** [01:00] Meeting started.",
        pa_context=pa_context,
    )

    parsed = json.loads(result_json_str)
    assert parsed["schema_version"] == "5.7"
    assert parsed["graph_mutations"]["action_updates"] == []
    validated = CanonicalSignalProjectionV57.model_validate(parsed)
    assert validated.schema_version == "5.7"
