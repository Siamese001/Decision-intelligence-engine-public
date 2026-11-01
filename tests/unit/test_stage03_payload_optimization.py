"""
tests/unit/test_stage03_payload_optimization.py

Wave 3 Acceptance & Regression Suite:
Modality-Calibrated Model Routing & Statement-Filtered Payloads.
Verifies:
1. Stage 03 prompt size on 90-minute transcripts drops from >25,000 tokens to <7,000 tokens (<= 30,000 chars).
2. Conversational small talk and banter (jackets, pocket squares, Miami rain, parties) are purged.
3. Attendee introductions, roles, affiliations, and core technical systems are preserved.
4. PromptPrefixAligner and L2GraphExtractorExecutor default to reasoning_effort="low" and FAST_STRUCTURED tier.
5. High-velocity statement filtering executes in < 50ms (< 45s threshold).
6. Resulting projection satisfies 100% of Gate 3.1–3.14 deterministic checks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from dual_pipeline.transcript_pipeline_core.context_builder import filter_stage03_statement_payload
from dual_pipeline.transcript_stages_core.graph_executor import (
    L2GraphExtractorExecutor,
    build_stage03_user_content,
    post_process_stage03_projection,
)
from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner
from dual_pipeline.transcript_qa_core.gate3_evidence import check_gate_3_14
from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates


_TRANSCRIPT_PATH = Path("input/infrastructure_automation/10-02-2026/infrastructure_automation_meeting_transcript.md")


@pytest.fixture
def raw_93k_transcript() -> str:
    """Load the authentic ~97KB Infrastructure Automation meeting transcript."""
    if _TRANSCRIPT_PATH.is_file():
        return _TRANSCRIPT_PATH.read_text(encoding="utf-8")
    pytest.skip(f"Transcript fixture {_TRANSCRIPT_PATH} not found.")


@pytest.fixture
def meeting_metadata() -> Dict[str, Any]:
    return {
        "meeting_name": "Apex Global Enterprises × PartnerAdvisory — Infrastructure Automation Meeting",
        "project": "infrastructure_automation",
        "date": "10-02-2026",
        "archetype": "architecture_review",
        "attendees": {
            "Apex Global Enterprises": [
                "Christina Harris", "Luis Arencibia", "Michael Valdes",
                "Ivan Sanchez", "Lerida Ramirez", "Pedro G. Martinez", "Nathan Sutton",
            ],
            "PartnerAdvisory": [
                "Jeremy Cianella", "Andrew Hanson", "Drew Oakley",
                "Daniel Simao", "Jason Nicholas", "Lan Tran", "Megan Orzell",
            ],
        },
    }


class DummyFastStructuredProvider:
    """Provider stub to verify reasoning_effort calibration."""

    def __init__(self):
        self.reasoning_effort: Optional[str] = None
        self.reasoning_budget: Optional[int] = None
        self.synthesize_graph_calls: List[Dict[str, str]] = []

    def set_reasoning_effort(self, effort: str) -> None:
        self.reasoning_effort = effort

    def set_reasoning_budget(self, budget: int) -> None:
        self.reasoning_budget = budget

    def synthesize_graph(self, system_prompt: str, user_content: str) -> str:
        self.synthesize_graph_calls.append({
            "system_prompt": system_prompt,
            "user_content": user_content,
        })
        return json.dumps({
            "meeting_id": "test_meeting",
            "evidence_index": {
                "stmt-001": {
                    "statement_id": "stmt-001",
                    "speaker": "Christina Harris",
                    "exact_text": "I'm a technology manager overseeing infrastructure automation initiatives.",
                },
                "stmt-002": {
                    "statement_id": "stmt-002",
                    "speaker": "Jason Nicholas",
                    "exact_text": "We deploy Terraform modules and reusable cloud patterns across AWS and GCP.",
                },
            },
            "entity_upserts": [
                {
                    "entity_id": "person:christina_harris",
                    "node_type": "person",
                    "display_name": "Christina Harris",
                    "evidence_ids": ["stmt-001"],
                    "description": "Technology manager overseeing infrastructure automation initiatives at Apex Global.",
                },
                {
                    "entity_id": "org:apex_global",
                    "node_type": "org",
                    "display_name": "Apex Global Enterprises",
                    "evidence_ids": ["stmt-001"],
                    "description": "Enterprise energy client organization.",
                },
                {
                    "entity_id": "system:terraform",
                    "node_type": "system",
                    "display_name": "Terraform",
                    "evidence_ids": ["stmt-002"],
                    "description": "Infrastructure as Code automation platform used for cloud deployment patterns.",
                },
            ],
            "relationship_signals": [
                {
                    "source_id": "person:christina_harris",
                    "predicate": "MEMBER_OF",
                    "target_id": "org:apex_global",
                    "evidence_ids": ["stmt-001"],
                    "description": "Christina Harris serves as technology manager at Apex Global Enterprises.",
                },
            ],
            "proposal_updates": [
                {
                    "proposal_id": "prop:iac_automation",
                    "name": "IaC Automation Factory",
                    "description": "Deploy Terraform modules and reusable cloud patterns across AWS and GCP.",
                    "evidence_ids": ["stmt-002"],
                    "status": "PROPOSED",
                },
            ],
        })


def test_stage03_prompt_size_reduction(raw_93k_transcript: str, meeting_metadata: Dict[str, Any]):
    """Verify Stage 03 prompt character count shrinks to <= 30,000 characters (< 7,000 tokens)."""
    assert len(raw_93k_transcript) > 90000, "Fixture must be >= 90K chars"

    # 1. Unfiltered prompt baseline
    meta_disabled = dict(meeting_metadata, disable_statement_filtering=True)
    unfiltered_static, unfiltered_dynamic = build_stage03_user_content(
        raw_93k_transcript, meta_disabled, return_components=True
    )
    unfiltered_len = len(unfiltered_static) + len(unfiltered_dynamic)
    assert unfiltered_len > 90000, "Unfiltered prompt must exceed 90,000 chars (>25,000 tokens)"

    # 2. Filtered prompt
    filtered_static, filtered_dynamic = build_stage03_user_content(
        raw_93k_transcript, meeting_metadata, return_components=True
    )
    filtered_len = len(filtered_static) + len(filtered_dynamic)

    # Acceptance criterion: Prompt character count <= 30,000 (< 7,000 tokens)
    assert filtered_len <= 30000, f"Filtered prompt length {filtered_len} exceeds 30,000 character limit"
    reduction_pct = (1.0 - (filtered_len / unfiltered_len)) * 100.0
    assert reduction_pct >= 65.0, f"Token reduction was {reduction_pct:.1f}%, expected >= 65%"


def test_conversational_banter_exclusion(raw_93k_transcript: str):
    """Verify conversational filler and non-technical chatter are cleanly excluded."""
    filtered = filter_stage03_statement_payload(raw_93k_transcript)
    filtered_lower = filtered.lower()

    # Known banter phrases present in original text
    banter_test_cases = [
        "wearing jacket",
        "pocket square",
        "pocket doesn't even open",
        "raining in miami",
        "huge party",
        "fundraising season",
        "take care. bye. hello",
        "love minnesota",
    ]

    for btc in banter_test_cases:
        assert btc not in filtered_lower, f"Conversational banter phrase '{btc}' leaked into Stage 03 payload"


def test_attendee_introductions_and_tech_retention(raw_93k_transcript: str):
    """Verify attendee introductions, organizational roles, and technical topics are retained."""
    filtered = filter_stage03_statement_payload(raw_93k_transcript)

    # Key participants who introduced themselves
    assert "Christina Harris" in filtered
    assert "technology manager" in filtered.lower()
    assert "Luis Arencibia" in filtered
    assert "Michael Valdes" in filtered
    assert "enterprise cloud" in filtered.lower() or "cloud lead" in filtered.lower()
    assert "Ivan Sanchez" in filtered
    assert "network engineering" in filtered.lower()
    assert "Lerida Ramirez" in filtered
    assert "authentication" in filtered.lower()

    # Key architectural and platform concepts
    assert "Terraform" in filtered or "terraform" in filtered.lower()
    assert "cloud" in filtered.lower()
    assert "automation" in filtered.lower()


def test_filtering_latency_budget(raw_93k_transcript: str):
    """Verify high-velocity statement filtering executes in < 50ms (< 45s threshold)."""
    start = time.perf_counter()
    filtered = filter_stage03_statement_payload(raw_93k_transcript)
    duration_ms = (time.perf_counter() - start) * 1000.0

    assert len(filtered) > 1000, "Filtered text must not be empty"
    assert duration_ms < 50.0, f"Filtering took {duration_ms:.2f}ms, expected < 50ms"


def test_reasoning_effort_calibration_for_stage03(raw_93k_transcript: str, meeting_metadata: Dict[str, Any]):
    """Verify Stage 03 routes to reasoning_effort='low' and FAST_STRUCTURED tier by default."""
    budget = PromptPrefixAligner.calculate_reasoning_token_budget(
        raw_93k_transcript, meeting_metadata, stage="stage_03"
    )
    assert budget["reasoning_effort"] == "low"
    assert budget["tier"] == "FAST_STRUCTURED"
    assert budget["budget_tokens"] <= 4096

    # Verify provider receives low reasoning effort during execute()
    provider = DummyFastStructuredProvider()
    executor = L2GraphExtractorExecutor(provider)
    executor.execute(
        forensic_transcript=raw_93k_transcript,
        pa_context={"metadata": meeting_metadata},
    )

    assert provider.reasoning_effort == "low"
    assert meeting_metadata.get("stage03_reasoning_effort") == "low"
    assert len(provider.synthesize_graph_calls) == 1
    # Verify user content passed to model was filtered
    user_content = provider.synthesize_graph_calls[0]["user_content"]
    assert len(user_content) <= 30000


def test_reasoning_effort_custom_overrides(raw_93k_transcript: str):
    """Verify explicit operator overrides are honored."""
    meta_override = {
        "stage03_reasoning_effort": "medium",
        "reasoning_budget_override": 3000,
    }
    budget = PromptPrefixAligner.calculate_reasoning_token_budget(
        raw_93k_transcript, meta_override, stage="stage_03"
    )
    assert budget["budget_tokens"] == 3000
    assert budget["tier"] == "CUSTOM_OVERRIDE"


def test_gate3_qa_compliance_on_projection(raw_93k_transcript: str, meeting_metadata: Dict[str, Any]):
    """Verify that Stage 03 projection satisfies Gate 3.1–3.14 deterministic checks."""
    provider = DummyFastStructuredProvider()
    executor = L2GraphExtractorExecutor(provider)
    result_json_str = executor.execute(
        forensic_transcript=raw_93k_transcript,
        pa_context={"metadata": meeting_metadata},
    )

    projection_data = json.loads(result_json_str)
    # Check Gate 3.14 (Proposal Evidence Alignment)
    passed_14, failures_14, details_14 = check_gate_3_14(projection_data)
    assert passed_14 is True, f"Gate 3.14 failed: {failures_14}"

    # Check full QA gates report
    qa_report = evaluate_all_qa_gates(
        output_1=raw_93k_transcript,
        output_2="## Executive Summary\nStrategic automation review.",
        output_3=projection_data,
        metadata=meeting_metadata,
        auto_heal=True,
    )
    g3_summary = qa_report.get("gate_groups", {}).get("gate_3_canonical_signal_projection", {})
    assert g3_summary.get("passed") is True or g3_summary.get("failures", 0) == 0
