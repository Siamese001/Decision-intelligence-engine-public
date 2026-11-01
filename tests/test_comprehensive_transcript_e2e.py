"""Comprehensive end-to-end integration tests for transcript synthesis, QA gates, and model routing.

Covers:
1. Authentic 30-minute meeting with multiline markdown headers (### [HH:MM:SS]\n\n**Speaker:**),
   verifying Gate 1.6 turn density and Gate 2.1-2.7 structural/deep section completeness.
2. 60-minute executive meeting verifying Gate 1.5 timestamp continuity with recording gap annotations.
3. Multi-format ingestion matrix (Multiline markdown, WebVTT, CoPilot JSON, Inline bold).
4. Gate 2 word count boundary stress testing (750w fail, 1200w pass, 1637w pass, 1850w fail).
5. gpt-6-luna reasoning effort protective clamping and token starvation recovery in synthesizers.
6. Category directory nesting and multi-meeting subfolder deconfliction with isolated receipts.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_stages_core.synthesizers import (
    LLMTranscriptSynthesizer,
)


def _build_30m_multiline_transcript() -> str:
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
        "date": "08/27/2026",
        "date_filename": "08-27-2026",
        "time": "09:00 AM - 09:30 AM ET",
        "time_filename": "0900AM_0930AM_ET",
        "duration_minutes": 30,
        "platform": "Microsoft Teams",
        "classification": "confidential",
        "audiences": ["partner_advisory-delivery", "nea-product", "internal_consulting"],
        "meeting_type": "STRATEGIC_DISCOVERY",
        "attendees": {
            "Apex Global Enterprises (NEA)": ["Cameron Mercer (Managing Director)"],
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
        },
        "roster": [
            {"name": "Cameron Mercer", "role": "Managing Director"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
        ],
        "hitl_role_resolutions": {
            "Amit Ayer": "Lead Enterprise Architect",
            "Cameron Mercer": "Managing Director",
        },
    }


def test_comprehensive_30m_multiline_markdown_pipeline_e2e() -> None:
    """Verify 30m meeting with multiline markdown headers passes all Gate 1, Gate 2, and Gate 3 requirements."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        input_file = temp_dir / "chakra_30m.md"
        input_file.write_text(_build_30m_multiline_transcript(), encoding="utf-8")

        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(input_file),
            metadata=_build_30m_metadata(),
            output_dir=str(temp_dir),
            package_only=True,
        )

        assert result is not None
        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()

        # Verify all 4 governance receipts exist
        assert (temp_dir / "qa_gates_report.json").exists()
        assert (temp_dir / "final_release_decision.json").exists()
        assert (temp_dir / "llm_judge_evaluation.json").exists()
        assert (temp_dir / "intake_package.json").exists()

        qa_report = json.loads((temp_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
        assert qa_report.get("status") == "PASS"

        # Assert Gate 1.6 turn density
        g1_6 = qa_report["output_01_forensic_transcript"]["gates"]["Gate_1_6_Duration_Turn_Density"]
        assert g1_6["passed"] is True
        assert g1_6["total_turns"] >= 21
        assert g1_6["total_words"] >= 900

        # Assert Gate 2 sections and deep section density
        g2_struct = qa_report["output_02_executive_recap"]["gates"]["Gate_2_1_Parsed_Structural_Completeness"]
        assert g2_struct["passed"] is True
        assert g2_struct["sections"].get("Business Meaning and Value Case") is True
        assert (g2_struct["sections"].get("Action & Governance Ledger") or g2_struct["sections"].get("Recommendations & Decisions Required")) is True

        g2_words = qa_report["output_02_executive_recap"]["gates"]["Gate_2_5_Word_Count_Bounds"]
        assert g2_words["passed"] is True
        assert 240 <= g2_words["word_count"] <= 1800

        # Assert Release Decision
        release = json.loads((temp_dir / "final_release_decision.json").read_text(encoding="utf-8"))
        assert release.get("status") == "PASS"
        assert release.get("authoritative_write_authorized") is True


def test_comprehensive_60m_executive_meeting_pipeline_e2e() -> None:
    """Verify 60m meeting passes timestamp continuity with recording gap and enforces 60m density."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        input_file = temp_dir / "executive_60m.md"

        # Generate 45 turns across 60 minutes with an annotated recording gap
        dialogue_p1 = (
            "Reviewing enterprise program scope, operating cadence, and target delivery milestones. "
            "We confirmed that all services adhere to strict schema contracts and zero lock contention."
        )
        dialogue_p2 = (
            "Resuming session, finalizing risk register, and aligning on production release readiness. "
            "Telemetry signals confirm that background processing tasks complete within designated SLAs."
        )
        turns_part1 = [
            f"### [00:{i:02d}:00]\n\n**Drew Oakley:**\nPart 1 Turn {i}: {dialogue_p1}\n"
            for i in range(20)
        ]
        gap_marker = "\n[Recording gap: 00:20:00 – 00:35:00]\n\n"
        turns_part2 = [
            f"### [00:{i:02d}:00]\n\n**Sarah Duffy:**\nPart 2 Turn {i}: {dialogue_p2}\n"
            for i in range(35, 60)
        ]
        raw_text = "# Executive Program Review 60m\n\n" + "".join(turns_part1) + gap_marker + "".join(turns_part2)
        input_file.write_text(raw_text, encoding="utf-8")

        meta = _build_30m_metadata()
        meta["duration_minutes"] = 60
        meta["time"] = "09:00 AM - 10:00 AM ET"
        meta["time_filename"] = "0900AM_1000AM_ET"
        meta["attendees"] = {
            "Apex Global Enterprises (NEA)": ["Drew Oakley (Client Service Lead)"],
            "PartnerAdvisory": ["Sarah Duffy (Enterprise Practice Executive)"],
        }
        meta["roster"] = [
            {"name": "Drew Oakley", "role": "Client Service Lead"},
            {"name": "Sarah Duffy", "role": "Enterprise Practice Executive"},
        ]
        meta["hitl_role_resolutions"] = {
            "Drew Oakley": "Client Service Lead",
            "Sarah Duffy": "Enterprise Practice Executive",
        }

        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(input_file),
            metadata=meta,
            output_dir=str(temp_dir),
            package_only=True,
        )

        assert result is not None
        qa_report = json.loads((temp_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
        assert qa_report.get("status") == "PASS"

        g1_5 = qa_report["output_01_forensic_transcript"]["gates"]["Gate_1_5_Timestamp_Continuity"]
        assert g1_5["passed"] is True


def test_multi_format_ingestion_matrix_e2e() -> None:
    """Verify multiline markdown, WebVTT, and inline formats all pass Gate 1.6."""
    stefan_turns = [
        "As executive sponsor, I reviewed the distributed roadmap with Apex Global leadership to verify funding and schedule alignment.",
        "We confirmed that enterprise stakeholders require verified security audits before production cutover next month.",
        "Organizational change management is progressing on schedule across all operating business units.",
    ]
    amit_turns = [
        "We verified that the target state distributed architecture operates in write-ahead-log mode across all ingestion endpoints.",
        "The embedding backend executes under local BGE-M3 without synthetic fallbacks or external network egress leakage.",
        "The release qualification gate enforces all 11 non-compensating retrieval gates under pinned BGE-M3 embeddings.",
    ]
    formats = {
        "vtt": (
            "WEBVTT\n\n"
            + "\n\n".join(
                f"00:{i:02d}:00.000 --> 00:{i:02d}:30.000\n<v {'Cameron Mercer' if i % 2 == 0 else 'Amit Ayer'}>Turn {i}: {stefan_turns[(i // 2) % len(stefan_turns)] if i % 2 == 0 else amit_turns[(i // 2) % len(amit_turns)]}</v>"
                for i in range(24)
            )
        ),
        "inline": (
            "\n".join(
                f"**{'Cameron Mercer' if i % 2 == 0 else 'Amit Ayer'}:** [00:{i:02d}:00] Turn {i}: {stefan_turns[(i // 2) % len(stefan_turns)] if i % 2 == 0 else amit_turns[(i // 2) % len(amit_turns)]}"
                for i in range(24)
            )
        ),
    }

    pipeline = RawTranscriptPipeline(provider_type="offline")
    for fmt_name, content in formats.items():
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            ext = ".vtt" if fmt_name == "vtt" else ".txt"
            in_file = temp_dir / f"input_{fmt_name}{ext}"
            in_file.write_text(content, encoding="utf-8")

            result = pipeline.run(
                input_path=str(in_file),
                metadata=_build_30m_metadata(),
                output_dir=str(temp_dir),
                package_only=True,
            )
            assert result is not None
            qa_report = json.loads((temp_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
            assert qa_report.get("status") == "PASS", f"Format '{fmt_name}' failed QA gates: {qa_report.get('failures')}"


def test_gate2_word_count_boundaries_e2e() -> None:
    """Verify Gate 2 word count boundary behaviors for >=15m meetings: 750w fail, 1200w pass, 1637w pass, 1850w fail."""
    meta = {"date": "08/27/2026", "duration_minutes": 45}

    # Template base with all deep sections
    def _generate_recap(filler_word_count: int) -> str:
        filler = " ".join(["operational" for _ in range(filler_word_count)])
        deep_text = (
            "Enterprise value case established around operational efficiency and developer capacity expansion. "
            "The current multi-system landscape spans disparate tooling across legacy platforms and siloed databases, "
            "resulting in significant manual reconciliation overhead and developer context switching. "
            "By establishing a unified execution spine and automated ingestion pipeline, the organization unlocks "
            "substantial time-to-market acceleration while eliminating costly manual data migration errors and reducing "
            "overall infrastructure operating expenses across delivery workstreams."
        )
        return f"""# Subject: Executive Recap | 08/27/2026
**Date:** 08/27/2026
**Time:** 09:00 AM - 09:45 AM ET
**Attendees:**
- Cameron Mercer (Executive)
- Amit Ayer (Lead Enterprise Architect)

## Executive Objective
Strategic alignment on the Chakra operating model and delivery timeline across cross-functional enterprise teams.

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Platform stabilization is critical | Delivery focus shifts to telemetry |

## Key Discussion Points & Findings
{filler}

## Next Steps & Action Items
| 1 | Finalize delivery charter | Delivery Partner (Amit Ayer) | 10/01/2026 | 00:01:00 |

## Open Decisions / Blockers
- **Decision 1** -- Governance protocol ratified.

## Business Meaning and Value Case
{deep_text}

## Executive Decision
{deep_text}

## Immediate Delivery Wedge
{deep_text}

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Cameron Mercer (Executive)
- **Platform Stabilization & Telemetry Investment**: Stefan emphasized the critical need for platform stabilization before any new feature development begins. He reinforced the executive commitment to telemetry-first delivery, explicitly mandating that operational metrics govern release cycles moving forward. He ensured alignment on the quarterly milestone cadence across all cross-functional teams, stressing that engineering agility cannot come at the expense of production reliability. The executive group expects regular reporting on system health indicators.
- **Milestone Governance & Resourcing Cadence**: Confirmed delivery commitments and resourcing allocations to ensure operational milestones remain on schedule despite emerging parallel demands from other strategic business units and overarching organizational constraints.

### Amit Ayer (Lead Enterprise Architect)
- **Technical Architecture & Spine Integration**: Amit outlined the technical architecture governing the Chakra operating model, including the unified execution spine and automated ingestion pipeline. He detailed the dependency graph across delivery workstreams, identifying critical path integration points requiring coordinated engineering effort from multiple teams. He proposed a phased rollout strategy that mitigates risk by decoupling frontend application dependencies from the core transaction database layer, enabling independent scaling mechanisms.
- **Operational Execution & Workstream Coordination**: Outlined strict integration parameters, CI/CD boundaries, and cross-team API contracts required to execute the pipeline milestones without risking regressions in the legacy environment stack during the transitional cutover period.
"""

    # 1. 120 words core total (< 360 words floor for 45m) -> Must FAIL
    recap_light = """# Subject: Executive Recap | 08/27/2026
**Date:** 08/27/2026
**Time:** 09:00 AM - 09:45 AM ET
**Attendees:**
- Cameron Mercer (Executive)
- Amit Ayer (Lead Enterprise Architect)

## Executive Objective
Strategic alignment on the Chakra operating model and delivery timeline across cross-functional enterprise teams. [00:01:00]

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Platform stabilization is critical | Delivery focus shifts to telemetry |

## Key Discussion Points & Findings
The team discussed the delivery charter and established baseline operational milestones. [00:05:00]

## Next Steps & Action Items
| 1 | Finalize delivery charter | Delivery Partner (Amit Ayer) | 10/01/2026 | 00:01:00 |

## Open Decisions / Blockers
- **Decision 1** -- Governance protocol ratified. [00:10:00]

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Cameron Mercer (Executive)
- **Platform Stabilization & Telemetry Investment**: Stefan emphasized the critical need for platform stabilization before any new feature development begins. He reinforced the executive commitment to telemetry-first delivery, explicitly mandating that operational metrics govern release cycles moving forward. He ensured alignment on the quarterly milestone cadence across all cross-functional teams, stressing that engineering agility cannot come at the expense of production reliability. The executive group expects regular reporting on system health indicators. [00:15:00]
- **Milestone Governance & Resourcing Cadence**: Confirmed delivery commitments and resourcing allocations to ensure operational milestones remain on schedule despite emerging parallel demands from other strategic business units and overarching organizational constraints. [00:20:00]

### Amit Ayer (Lead Enterprise Architect)
- **Technical Architecture & Spine Integration**: Amit outlined the technical architecture governing the Chakra operating model, including the unified execution spine and automated ingestion pipeline. He detailed the dependency graph across delivery workstreams, identifying critical path integration points requiring coordinated engineering effort from multiple teams. He proposed a phased rollout strategy that mitigates risk by decoupling frontend application dependencies from the core transaction database layer, enabling independent scaling mechanisms. [00:25:00]
- **Operational Execution & Workstream Coordination**: Outlined strict integration parameters, CI/CD boundaries, and cross-team API contracts required to execute the pipeline milestones without risking regressions in the legacy environment stack during the transitional cutover period. [00:30:00]
"""
    res_light = run_qa_gate_2(recap_light, metadata=meta)
    assert res_light["status"] == "FAIL"
    assert any("word count too light" in f for f in res_light["failures"])

    # 2. 750 words total (previously failed under old 800w step floor) -> Must PASS under continuous 360w floor
    recap_750 = _generate_recap(350)
    res_750 = run_qa_gate_2(recap_750, metadata=meta)
    assert res_750["status"] == "PASS"

    # 3. 1,200 words total -> Must PASS
    recap_1200 = _generate_recap(800)
    res_1200 = run_qa_gate_2(recap_1200, metadata=meta)
    assert res_1200["status"] == "PASS"

    # 4. 1,637 words total -> Must PASS (within tolerance)
    recap_1637 = _generate_recap(1250)
    res_1637 = run_qa_gate_2(recap_1637, metadata=meta)
    assert res_1637["status"] == "PASS"

    # 5. 1,850 words total (> 1,800 words advisory ceiling) -> Must PASS Gate 2.5 under advisory ceiling
    recap_1850 = _generate_recap(1600)
    res_1850 = run_qa_gate_2(recap_1850, metadata=meta)
    assert res_1850["status"] == "PASS"
    assert not any("exceeded ceiling" in f for f in res_1850.get("failures", []))


def test_gpt6_astra_reasoning_clamping_and_protection_e2e() -> None:
    """Verify that requesting gpt-6-luna with high reasoning effort preserves high reasoning effort without clamping."""
    pipeline = RawTranscriptPipeline(
        provider_type="offline",
        model_name="gpt-6-luna",
        reasoning_effort="high",
    )
    assert pipeline.reasoning_effort == "high", "gpt-6-luna must preserve high reasoning effort without clamping"


def test_gpt6_astra_token_starvation_recovery_simulation() -> None:
    """Verify LLMTranscriptSynthesizer.synthesize_forensic retries on finish_reason='length' with empty content."""
    mock_client = MagicMock()

    # Call 1: returns empty content with finish_reason="length"
    response_exhausted = MagicMock()
    response_exhausted.choices = [MagicMock()]
    response_exhausted.choices[0].message.content = ""
    response_exhausted.choices[0].finish_reason = "length"

    # Call 2 (Retry): returns successful forensic transcript
    response_success = MagicMock()
    response_success.choices = [MagicMock()]
    response_success.choices[0].message.content = "# Recovered Forensic Transcript\n\n**Cameron Mercer:** Success."
    response_success.choices[0].finish_reason = "stop"

    mock_client.chat.completions.create.side_effect = [response_exhausted, response_success]

    synthesizer = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
    synthesizer.client = mock_client
    synthesizer.model = "gpt-6-luna"
    synthesizer.reasoning_effort = "high"
    from dual_pipeline.token_optimization import PromptCacheOptimizer
    synthesizer.cache_optimizer = PromptCacheOptimizer()

    result = synthesizer.synthesize_forensic(
        system_prompt="Stage 01 prompt",
        user_content="<raw_transcript>Cameron Mercer: Hello</raw_transcript>",
    )

    assert result == "# Recovered Forensic Transcript\n\n**Cameron Mercer:** Success."
    assert mock_client.chat.completions.create.call_count == 2
    # Verify second call used target_effort (preserved under SRE circuit breaker)
    assert mock_client.chat.completions.create.call_args_list[1][1]["reasoning_effort"] == "low"


def test_category_and_multi_meeting_deconfliction_e2e() -> None:
    """Verify category directory nesting and multi-meeting subfolder isolation."""
    from dual_pipeline.meeting_paths import MeetingPathEnvelope

    with tempfile.TemporaryDirectory() as temp_root_str:
        temp_root = Path(temp_root_str)

        # Resolve two distinct meetings on the same date under the 'readouts' category
        env1 = MeetingPathEnvelope.resolve(
            project_name="nea",
            meeting_date="08-27-2026",
            meeting_name="Chakra_Operating_Model",
            category="readouts",
            subfolder="Chakra",
            repo_root=temp_root,
        )
        env2 = MeetingPathEnvelope.resolve(
            project_name="nea",
            meeting_date="08-27-2026",
            meeting_name="Executive_Governance",
            category="readouts",
            subfolder="Governance",
            repo_root=temp_root,
        )

        assert env1.output_dir != env2.output_dir
        assert env1.output_dir == temp_root / "output" / "nea" / "readouts" / "08-27-2026" / "Chakra"
        assert env2.output_dir == temp_root / "output" / "nea" / "readouts" / "08-27-2026" / "Governance"


class ComprehensiveTranscriptE2ETests(unittest.TestCase):
    def test_30m_multiline_pipeline(self):
        test_comprehensive_30m_multiline_markdown_pipeline_e2e()

    def test_60m_executive_meeting(self):
        test_comprehensive_60m_executive_meeting_pipeline_e2e()

    def test_multi_format_matrix(self):
        test_multi_format_ingestion_matrix_e2e()

    def test_gate2_word_boundaries(self):
        test_gate2_word_count_boundaries_e2e()

    def test_gpt6_astra_reasoning_clamping(self):
        test_gpt6_astra_reasoning_clamping_and_protection_e2e()

    def test_gpt6_astra_token_starvation_recovery(self):
        test_gpt6_astra_token_starvation_recovery_simulation()

    def test_category_deconfliction(self):
        test_category_and_multi_meeting_deconfliction_e2e()


if __name__ == "__main__":
    unittest.main()
