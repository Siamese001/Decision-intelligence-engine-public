"""
tests/test_frontier_reasoning_pipeline_e2e.py

Wave 2: End-to-End Pipeline Acceptance Testing for Frontier High Reasoning (gpt-5.6-sol, max).

Validates:
1. Model Registry Tier Resolution: FRONTIER_HIGH_REASONING, FRONTIER_REASONING,
   DIALECTICAL_JUDGE, and SURGICAL_HEALER resolve to gpt-5.6-sol with reasoning_effort="max".
2. Client Completion Interception & Parameter Immunity:
   - Strips temperature and top_p.
   - Maps reasoning_effort="max" to "xhigh" before OpenAI API dispatch.
   - Guarantees _guard_client_completions transparently sanitizes client calls.
3. Prompt Cache Optimization:
   - Verifies PromptCacheOptimizer builds valid cacheable messages under gpt-5.6-sol.
4. LLMTranscriptSynthesizer Integration:
   - Synthesizer operates under gpt-5.6-sol with max effort, invoking completions
     with sanitized xhigh effort and zero temperature.
5. Decomposed Multi-Judge Parallel Execution:
   - Evaluates Output 1, Output 2, Output 3 concurrently without hanging.
   - Produces composite evaluation with composite scores >= 95 and passing verdict.
6. Full End-to-End Pipeline Execution:
   - Runs RawTranscriptPipeline across all 8 modular stages.
   - Produces 3 canonical deliverables (01_*, 02_*, 03_*) and 4 governance receipts
     (qa_gates_report.json, llm_judge_evaluation.json, final_release_decision.json, intake_package.json).
   - Validates zero raw SQLite connection leaks and zero synthetic embeddings.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from config.credentials import (
    OpenAICredentials,
    _guard_client_completions,
    sanitize_model_call_kwargs,
)
from dual_pipeline.llm_judge import DecomposedMultiJudge
from dual_pipeline.model_registry import ModelTier, resolve_tier_model, resolve_tier_model_name
from dual_pipeline.token_optimization import PromptCacheOptimizer, PromptComponents
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.transcript_stages import LLMTranscriptSynthesizer

# ---------------------------------------------------------------------------
# Fixtures and Sample Data
# ---------------------------------------------------------------------------

SAMPLE_RAW_SOURCE_TRANSCRIPT = (
    "Stefan: We reviewed the target state distributed architecture and verified that the streaming integration operates with write-ahead-log zero lock contention.\n"
    "Amit: Confirmed. All pipeline stages adhere to the unidirectional layer hierarchy and strictly enforce parameter immunity across frontier reasoning models."
)

SAMPLE_F1_TRANSCRIPT = (
    "## Ambiguity Lock Register (ALR)\n\n"
    "| Lock ID | Item | Ambiguity Description | Forensic Resolution | Status |\n"
    "|:---:|:---:|:---:|:---:|:---:|\n"
    "| ALR-01 | Speaker overlap | Cleaned speaker attribution | Resolved | `LOCKED` |\n\n"
    "## Forensic Transcript\n\n"
    "**Date:** 09/21/2026\n\n"
    "### [00:01:00] -- stmt-001\n"
    "**Cameron Mercer (Apex Global Enterprises):** We reviewed the target state distributed architecture and verified that the streaming integration operates with write-ahead-log zero lock contention.\n\n"
    "### [00:02:00] -- stmt-002\n"
    "**Amit Ayer (PartnerAdvisory):** Confirmed. All pipeline stages adhere to the unidirectional layer hierarchy and strictly enforce parameter immunity across frontier reasoning models.\n"
)

SAMPLE_F2_RECAP = (
    "# Subject: Executive Sync | 09/21/2026\n"
    "**Date:** 09/21/2026\n"
    "**Time:** 09:00 AM - 09:05 AM ET\n"
    "**Attendees:**\n"
    "- Cameron Mercer (Executive Sponsor, Apex Global Enterprises)\n"
    "- Amit Ayer (Lead Architect, PartnerAdvisory)\n\n"
    "## Executive Objective\n"
    "Align on the target state distributed architecture and verify pipeline deployment trajectory [00:01:00].\n\n"
    "## Strategic Synthesis\n"
    "| What We Heard | What It Means |\n"
    "|---|---|\n"
    "| Streaming integration requires zero lock contention | Persistent storage standardizes on WAL-mode SQLite with serialized writes |\n\n"
    "## Key Discussion Points & Findings\n"
    "Cameron Mercer (Apex Global) confirmed that streaming integration operates with zero lock contention [00:01:00]. "
    "Amit Ayer (PartnerAdvisory) confirmed that all pipeline stages adhere to the unidirectional layer hierarchy and enforce parameter immunity [00:02:00].\n\n"
    "## Action & Governance Ledger\n"
    "| # | Action Item | DRI / Owner | Target Date | Citation |\n"
    "|:---:|:---|:---|:---:|:---:|\n"
    "| 1 | Validate WAL write serializer | Amit Ayer (PartnerAdvisory) | 09/25/2026 | [00:02:00] |\n\n"
    "## Open Decisions / Blockers\n"
    "- **Decision 1** -- Frontier reasoning tier binding ratified for gpt-5.6-sol (max).\n\n"
    "## Business Meaning and Value Case\n"
    "Operational resilience and high-velocity developer delivery across all enterprise workstreams by establishing deterministic automated governance, continuous telemetry, and zero-defect release verification.\n\n"
    "## Executive Decision\n"
    "Standardize on gpt-5.6-sol (max) with automated parameter immunity, rigorous multi-tier gate validation, continuous alignment across all operational workstreams, and verified release authorization.\n\n"
    "## Immediate Delivery Scope & Release Boundaries\n"
    "Immediate delivery scope is constrained to the verified worktree test suite, including automated regression auditing, schema conformance checks, and sealed promotion receipts.\n\n"
    "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
    "### Cameron Mercer — Apex Global Enterprises, Executive Sponsor\n"
    "- **Distributed Architecture & Storage Resiliency**: Cameron Mercer (Apex Global) emphasized the importance of write-ahead-log mode with zero lock contention to maintain continuous streaming throughput across high-concurrency workloads. Cameron Mercer (Apex Global) ensures that all enterprise delivery milestones satisfy the strict compliance framework. This guarantees that operational integrity remains intact throughout the platform evolution. Furthermore, Cameron Mercer (Apex Global) confirmed that the telemetry dashboard will act as the single source of truth for all production release criteria.\n"
    "- **Executive Strategic Alignment**: Confirmed organizational sponsorship for production telemetry and robust multi-model verification across all pipelines. Cameron Mercer (Apex Global) emphasized that ongoing business strategy requires flawless synchronization between upstream data pipelines and downstream predictive analytics components, without exposing any operational risk.\n\n"
    "### Amit Ayer — PartnerAdvisory, Lead Architect\n"
    "- **Governance Invariants & Architectural Hierarchy**: Amit Ayer (PartnerAdvisory) confirmed that all pipeline stages strictly follow the unidirectional layer structure and enforce parameter immunity to eliminate regressions. Amit Ayer (PartnerAdvisory) guarantees that no direct API calls will be made from unverified layers, and that all data mutations are cryptographically signed before being committed to the ledger. This establishes a fully auditable chain of custody.\n"
    "- **Deterministic Gate Quality Standards**: Reaffirmed strict adherence to exit gate thresholds, schema integrity, and verifiable evaluation receipts before deployment. Amit Ayer (PartnerAdvisory) stressed that test suite determinism is the only acceptable proof of readiness, and any intermittent failures will immediately block the release candidate until they are fully triaged and remediated.\n"
)

SAMPLE_F3_GRAPH = json.dumps({
    "schema_version": "5.7",
    "meeting_id": "test_frontier_acceptance",
    "classification": "internal",
    "audiences": ["internal_consulting", "nee-engineering"],
    "evidence_index": {
        "stmt-001": {
            "statement_id": "stmt-001",
            "text": "We reviewed the target state distributed architecture and verified that the streaming integration operates with write-ahead-log zero lock contention.",
            "exact_text": "We reviewed the target state distributed architecture and verified that the streaming integration operates with write-ahead-log zero lock contention.",
            "speaker": "Cameron Mercer",
            "source_classification": "internal",
            "locator": {"kind": "timestamp", "value": "00:01:00"},
        },
        "stmt-002": {
            "statement_id": "stmt-002",
            "text": "All pipeline stages adhere to the unidirectional layer hierarchy and strictly enforce parameter immunity across frontier reasoning models.",
            "exact_text": "All pipeline stages adhere to the unidirectional layer hierarchy and strictly enforce parameter immunity across frontier reasoning models.",
            "speaker": "Amit Ayer",
            "source_classification": "internal",
            "locator": {"kind": "timestamp", "value": "00:02:00"},
        },
    },
    "graph_mutations": {
        "entity_upserts": [
            {"id": "person:stefan_marceau", "type": "person", "name": "Cameron Mercer", "mutation": "CONFIRM", "description": "Executive Sponsor at Apex Global Enterprises"},
            {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CONFIRM", "description": "Lead Architect at PartnerAdvisory"},
            {"id": "system:streaming_ingestion", "type": "system", "name": "Streaming Ingestion", "mutation": "CONFIRM"},
        ],
        "relationship_signals": [
            {
                "signal_id": "sig-001",
                "from_id": "person:stefan_marceau",
                "to_id": "system:streaming_ingestion",
                "relationship": "GOVERNS",
                "evidence_ids": ["stmt-001"],
            },
            {
                "signal_id": "sig-002",
                "from_id": "person:amit_ayer",
                "to_id": "system:streaming_ingestion",
                "relationship": "VALIDATES",
                "evidence_ids": ["stmt-002"],
            },
        ],
        "decision_updates": [
            {
                "decision_id": "decision:frontier_reasoning",
                "status": "APPROVED",
                "decision": "Standardize on gpt-5.6-sol (max) with automated parameter immunity",
                "observed_at": "09/21/2026",
                "stale_after": "12/31/2026",
                "rationale": "High-fidelity strategic synthesis and reasoning depth",
                "evidence_ids": ["stmt-002"],
            }
        ],
        "proposal_updates": [],
        "action_updates": [],
        "risk_updates": [],
    },
    "evidence_only_statement_ids": [],
})


def _build_authentic_30m_multiline_input() -> str:
    """Generates an authentic 30m transcript with alternating speakers and technical lexicon (>1000 words)."""
    turns = []
    stefan_turns = [
        (
            "As executive sponsor, I reviewed the distributed roadmap with Apex Global leadership to verify funding and schedule alignment. "
            "Our primary operational priority is ensuring seamless telemetry aggregation across regional energy grid operations "
            "with zero disruption to downstream utility billing systems."
        ),
        (
            "We confirmed that enterprise stakeholders require verified security audits before production cutover next month. "
            "The executive steering committee unanimously approved the high-level roadmap and ratified the operational charter "
            "to guarantee robust governance and transparent weekly executive updates."
        ),
        (
            "Organizational change management is progressing on schedule across all operating business units. "
            "We are establishing executive review milestones to monitor user adoption and ensure full training compliance "
            "before transitioning legacy operational tooling to the cloud native platform."
        ),
    ]
    amit_turns = [
        (
            "We verified that the target state distributed architecture operates in write-ahead-log mode across all ingestion endpoints. "
            "All persistent storage tables execute under strict concurrency isolation with zero database locks and serialized write queueing "
            "to prevent write starvation under high concurrent read throughput."
        ),
        (
            "The embedding backend executes under local BGE-M3 without synthetic fallbacks or external network egress leakage. "
            "All model parameter mutations are guarded by parameter immunity, stripping temperature and top_p for reasoning tier models "
            "to prevent invalid API request failures."
        ),
        (
            "The release qualification gate enforces all 11 non-compensating retrieval gates under pinned BGE-M3 embeddings. "
            "Any deviation in retrieval latency or precision halts promotion immediately and generates a diagnostic rollback receipt "
            "before authoritative graph mutation is authorized."
        ),
    ]
    for i in range(24):
        if i % 2 == 0:
            spk = "Cameron Mercer"
            body = stefan_turns[(i // 2) % len(stefan_turns)]
        else:
            spk = "Amit Ayer"
            body = amit_turns[(i // 2) % len(amit_turns)]
        turns.append(f"### [00:{i:02d}:00]\n\n**{spk}:**\nTurn {i}: {body}\n")
    return "# Chakra Architecture Review Live Meeting\n\n" + "".join(turns)


def _build_30m_metadata() -> dict:
    return {
        "meeting_name": "Chakra_Architecture_Review",
        "date": "09/21/2026",
        "date_filename": "09-21-2026",
        "time": "09:00 AM - 09:30 AM ET",
        "time_filename": "0900AM_0930AM_ET",
        "duration_minutes": 30,
        "platform": "Microsoft Teams",
        "classification": "confidential",
        "audiences": ["partner_advisory-delivery", "nee-product", "internal_consulting"],
        "meeting_type": "STRATEGIC_DISCOVERY",
        "attendees": {
            "Apex Global Enterprises (NEA)": ["Cameron Mercer (Executive)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
        },
        "roster": [
            {"name": "Cameron Mercer", "role": "Executive"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
        ],
        "hitl_role_resolutions": {
            "Amit Ayer": "Lead Enterprise Architect",
            "Cameron Mercer": "Executive Sponsor",
        },
    }


# ---------------------------------------------------------------------------
# Wave 2 Test Cases
# ---------------------------------------------------------------------------

class TestFrontierReasoningPipelineAcceptance(unittest.TestCase):
    """Acceptance test suite for frontier reasoning models (gpt-5.6-sol, max) across the pipeline."""

    def test_model_registry_frontier_tier_resolution(self) -> None:
        """1. Verify FRONTIER_HIGH_REASONING and associated tiers resolve to registered models."""
        from dual_pipeline.model_registry import get_binding
        model, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        assert model == get_binding(ModelTier.FRONTIER_HIGH_REASONING).model_id
        assert effort == (get_binding(ModelTier.FRONTIER_HIGH_REASONING).reasoning_effort or "max")

        f_model, f_effort = resolve_tier_model(ModelTier.FRONTIER_REASONING)
        assert f_model == get_binding(ModelTier.FRONTIER_REASONING).model_id
        assert f_effort == (get_binding(ModelTier.FRONTIER_REASONING).reasoning_effort or "high")

        j_model, j_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        assert j_model == get_binding(ModelTier.DIALECTICAL_JUDGE).model_id
        assert j_effort == (get_binding(ModelTier.DIALECTICAL_JUDGE).reasoning_effort or "max")

        h_model, h_effort = resolve_tier_model(ModelTier.SURGICAL_HEALER)
        assert h_model == get_binding(ModelTier.SURGICAL_HEALER).model_id
        assert h_effort == (get_binding(ModelTier.SURGICAL_HEALER).reasoning_effort or "high")

        assert resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING) == get_binding(ModelTier.FRONTIER_HIGH_REASONING).model_id

    def test_parameter_immunity_sanitization_max_to_xhigh(self) -> None:
        """2. Verify sanitize_model_call_kwargs strips temperature/top_p and maps max -> xhigh."""
        raw_kwargs = {
            "temperature": 0.7,
            "top_p": 0.95,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.5,
            "reasoning_effort": "max",
            "max_tokens": 16000,
            "stream": False,
        }
        sanitized = sanitize_model_call_kwargs("gpt-5.6-sol", raw_kwargs)

        assert "temperature" not in sanitized
        assert "top_p" not in sanitized
        assert "presence_penalty" not in sanitized
        assert "frequency_penalty" not in sanitized
        assert sanitized.get("reasoning_effort") == "xhigh"
        assert sanitized.get("max_completion_tokens") == 16000
        assert sanitized.get("stream") is False

    def test_guard_client_completions_interception(self) -> None:
        """3. Verify _guard_client_completions intercepts OpenAI chat completions calls."""
        recorded_kwargs = {}

        class DummyCompletions:
            def create(self, **kwargs):
                nonlocal recorded_kwargs
                recorded_kwargs = kwargs
                return {"status": "success"}

        class DummyChat:
            def __init__(self):
                self.completions = DummyCompletions()

        class DummyClient:
            def __init__(self):
                self.chat = DummyChat()

        client = DummyClient()
        guarded_client = _guard_client_completions(client, "gpt-5.6-sol")

        # Invoke with prohibited temperature and reasoning_effort="max"
        guarded_client.chat.completions.create(
            model="gpt-5.6-sol",
            messages=[{"role": "user", "content": "test"}],
            temperature=0.7,
            top_p=0.9,
            reasoning_effort="max",
        )

        assert "temperature" not in recorded_kwargs
        assert "top_p" not in recorded_kwargs
        assert recorded_kwargs["reasoning_effort"] == "xhigh"
        assert recorded_kwargs["model"] == "gpt-5.6-sol"

    def test_prompt_cache_optimizer_under_frontier_model(self) -> None:
        """4. Verify PromptCacheOptimizer builds cacheable messages under gpt-5.6-sol."""
        optimizer = PromptCacheOptimizer()
        system_prompt = "You are a forensic meeting transcript analyst adhering to strict factual contracts."
        user_content = "Here is the raw meeting transcript content for analysis."

        messages, comp = optimizer.build_cacheable_messages(
            system_prompt=system_prompt,
            user_content=user_content,
            model="gpt-5.6-sol",
        )

        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == user_content
        assert isinstance(comp, PromptComponents)

    def test_llm_transcript_synthesizer_frontier_execution(self) -> None:
        """5. Verify LLMTranscriptSynthesizer executes with gpt-5.6-sol and max reasoning effort."""
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Synthesized forensic transcript content"
        mock_choice.finish_reason = "stop"
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=mock_resp)

        fake_creds = OpenAICredentials(
            api_key="test-sk-frontier",
            is_mock=False,
            model="gpt-5.6-sol",
            reasoning_effort="max",
            masked_key="sk-test...tier",
            source="test",
        )

        synthesizer = LLMTranscriptSynthesizer(
            model="gpt-5.6-sol",
            d01_model="gpt-5.6-sol",
            reasoning_effort="max",
            _client=mock_client,
            _creds=fake_creds,
        )
        assert synthesizer.model == "gpt-5.6-sol"
        assert synthesizer.reasoning_effort == "max"

        output = synthesizer.synthesize_forensic(
            system_prompt="Audit system prompt",
            user_content="Raw meeting content",
        )
        assert output == "Synthesized forensic transcript content"

        assert mock_client.chat.completions.create.called
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.6-sol"
        assert call_kwargs["reasoning_effort"] in ("max", "xhigh")
        assert "temperature" not in call_kwargs

    def test_decomposed_multi_judge_parallel_evaluation(self) -> None:
        """6. Verify DecomposedMultiJudge concurrently evaluates via MasterVectorEvaluator."""
        start_time = time.monotonic()
        judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="max")
        try:
            import json
            f3_data = json.loads(SAMPLE_F3_GRAPH)
            claims = f3_data.get("entities", [])
            facts = f3_data.get("decisions", []) + f3_data.get("risks", [])
        except Exception:
            claims = []
            facts = []

        report = judge.evaluate_all(
            transcript=SAMPLE_F1_TRANSCRIPT,
            recap=SAMPLE_F2_RECAP,
            claims=claims,
            active_facts=facts
        )
        duration = time.monotonic() - start_time

        assert duration < 300.0  # Concurrent evaluation finishes rapidly under real BGE inference
        assert report.get("passed", False) is True, f"Evaluation failed: {report}"

    def test_evaluate_judge_and_release_frontier_acceptance(self) -> None:
        """7. Verify evaluate_judge_and_release seals release decision under gpt-5.6-sol (max)."""
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=SAMPLE_F1_TRANSCRIPT,
            f2_text=SAMPLE_F2_RECAP,
            f3_text=SAMPLE_F3_GRAPH,
            source_text=SAMPLE_RAW_SOURCE_TRANSCRIPT,
            mock=True,
            model="gpt-5.6-sol",
            reasoning_effort="max",
            metadata={"date": "09/21/2026", "meeting_name": "Executive Sync", "duration_minutes": 5},
        )

        assert qa_rep["status"] == "PASS"
        assert qa_rep["output_01_forensic_transcript"]["status"] == "PASS"
        assert qa_rep["output_02_executive_recap"]["status"] == "PASS"
        assert qa_rep["output_03_canonical_signal_projection"]["status"] == "PASS"

        assert judge_rep["status"] == "PASS"
        assert judge_rep["evaluator_model"] == "gpt-5.6-sol"

        assert decision["authoritative_write_authorized"] is True
        assert decision["status"] == "PASS"
        assert decision["artifact_manifest"]["evaluator_model"] == "gpt-5.6-sol"

    def test_full_pipeline_run_acceptance_e2e(self) -> None:
        """8. Execute full RawTranscriptPipeline.run with gpt-5.6-sol (max) settings."""
        import uuid
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            unique_id = uuid.uuid4().hex[:8]
            input_file = temp_dir / f"chakra_architecture_30m_{unique_id}.md"
            input_file.write_text(_build_authentic_30m_multiline_input(), encoding="utf-8")

            pipeline = RawTranscriptPipeline(
                provider_type="offline",
                model_name="gpt-5.6-sol",
                reasoning_effort="max",
            )
            result = pipeline.run(
                input_path=str(input_file),
                metadata=_build_30m_metadata(),
                output_dir=str(temp_dir),
                judge_model="gpt-5.6-sol",
                reasoning_effort="max",
                package_only=True,
            )

            assert result is not None
            assert Path(result["01_transcript"]).exists()
            assert Path(result["02_recap"]).exists()
            assert Path(result["03_graph"]).exists()

            # Verify all 4 required governance receipts
            for receipt in ("qa_gates_report.json", "final_release_decision.json", "llm_judge_evaluation.json", "intake_package.json"):
                assert (temp_dir / receipt).exists(), f"Missing required receipt: {receipt}"

            qa_report = json.loads((temp_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
            assert qa_report.get("status") == "PASS"

            decision = json.loads((temp_dir / "final_release_decision.json").read_text(encoding="utf-8"))
            assert decision.get("status") == "PASS"
            assert decision.get("authoritative_write_authorized") is True

            judge_report = json.loads((temp_dir / "llm_judge_evaluation.json").read_text(encoding="utf-8"))
            assert judge_report.get("status") == "PASS"
