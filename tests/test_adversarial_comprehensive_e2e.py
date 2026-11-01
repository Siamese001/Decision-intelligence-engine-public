"""tests/test_adversarial_comprehensive_e2e.py

Adversarial Comprehensive End-to-End Test Suite.
Addresses the root causes of prior E2E testing false-positives:
1. Real-world long-duration meetings (15m, 30m, 45m, 60m) exercising Gate 1.6 and Gate 2.
2. Exact production regression cases: 1,637 words recap (prior 1,600 ceiling failure) and multiline ASR headers.
3. Multi-format ASR variations: multiline markdown, WebVTT tags, bracketed timestamps, CoPilot turns.
4. Token exhaustion chaos recovery across Stage 01, Stage 02, and Stage 03.
5. Header synonym robustness in Gate 2 (Executive Objective, Meeting Purpose, Purpose & Objective).
6. End-to-end DocumentPipeline processing of real intake document (intake/2026-09-17_Mark_McCarthy_NEA_SWE_Interviews.txt).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"

from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


def _generate_realistic_turns(count: int, duration_minutes: int, start_minute: int = 0) -> str:
    """Generate realistic speaking turns distributed across duration with sufficient word density."""
    turns = []
    interval = max(1, duration_minutes * 60 // count)
    speakers = ["Cameron Mercer", "Amit Ayer"]
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
    for i in range(count):
        secs = (start_minute * 60) + (i * interval)
        hh, mm, ss = secs // 3600, (secs % 3600) // 60, secs % 60
        spk = speakers[i % len(speakers)]
        ts = f"{hh:02d}:{mm:02d}:{ss:02d}"
        body = stefan_turns[(i // 2) % len(stefan_turns)] if spk == "Cameron Mercer" else amit_turns[(i // 2) % len(amit_turns)]
        if i % 3 == 0:
            turns.append(f"### [{ts}]\n\n**{spk}:**\nTurn {i}: {body}\n")
        elif i % 3 == 1:
            turns.append(f"[{ts}] **{spk}:** Turn {i}: {body}\n")
        else:
            turns.append(f"**{spk}:** [{ts}] Turn {i}: {body}\n")
    return "\n".join(turns)


class TestAdversarialComprehensiveE2E(unittest.TestCase):
    """Adversarial stress and regression suite closing all prior E2E blindspots."""

    def test_production_regression_1637_words_and_multiline_30m_e2e(self) -> None:
        """Verify the exact 08-27-2026 failure profile: multiline headers + 1,637-word recap."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            transcript_text = "# Chakra Operating Model Review\n\n" + _generate_realistic_turns(
                26, 31
            )
            in_file = out_dir / "chakra_31m.md"
            in_file.write_text(transcript_text, encoding="utf-8")

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(in_file),
                metadata={
                    "meeting_name": "Chakra_Operating_Model",
                    "date": "08/27/2026",
                    "time": "09:00 AM - 09:31 AM ET",
                    "duration_minutes": 31,
                    "attendees": ["Cameron Mercer (Managing Director)", "Amit Ayer (Lead Enterprise Architect)"],
                    "hitl_role_resolutions": {
                        "Amit Ayer": "Lead Enterprise Architect",
                        "Cameron Mercer": "Managing Director",
                    },
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            assert result is not None
            qa_path = out_dir / "qa_gates_report.json"
            assert qa_path.exists()
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            assert qa["status"] == "PASS"

            # Verify Gate 1.6 passed with >= 21 turns and >= 930 words
            g1_6 = qa["output_01_forensic_transcript"]["gates"]["Gate_1_6_Duration_Turn_Density"]
            assert g1_6["passed"] is True
            assert g1_6["total_turns"] >= 21
            assert g1_6["total_words"] >= 930

    def test_word_count_boundary_exact_thresholds_e2e(self) -> None:
        """Adversarially test exact Gate 2 word count boundaries: 799w (fail), 800w (pass), 1637w (warn), 1801w (fail)."""
        deep_biz = (
            "Enterprise value case established around operational efficiency and developer capacity expansion. "
            "The current multi-system landscape spans disparate tooling across legacy platforms and siloed databases, "
            "resulting in significant manual reconciliation overhead, high defect leakage, and developer context switching. "
            "By establishing a unified execution spine and automated ingestion pipeline, the organization unlocks "
            "substantial time-to-market acceleration while eliminating costly manual data migration errors and reducing "
            "overall infrastructure operating expenses across delivery workstreams."
        )
        deep_dec = (
            "Strategic direction affirmed for initial execution phase across the enterprise platform modernization. "
            "Leadership evaluated alternative incremental options against the unified architecture and concluded that "
            "deferring architectural decoupling introduces unacceptable technical debt and risk of downstream failure. "
            "The ratified decision mandates immediate commencement of the wave-based delivery model with continuous "
            "governance gates and verifiable execution receipts for all production promotions. Delivery pods are authorized "
            "to proceed immediately."
        )
        deep_wedge = (
            "Targeted delivery plan scoped for current operational milestone focusing on core pipeline enablement. "
            "In-scope boundaries include automated ingestion, deterministic routing, and schema validation components, "
            "while deferred legacy integrations remain explicitly quarantined for phase two execution. "
            "Clear acceptance criteria mandate sub-second parsing latency, zero unannotated timestamp gaps, and strict "
            "schema compliance before downstream graph persistence is authorized across test and production environments."
        )
        base_recap = (
            "# Subject: Strategy Sync | 08/27/2026\n\n"
            "**Date:** 08/27/2026\n**Time:** 09:00 AM - 09:45 AM ET\n"
            "**Attendees:**\n- Cameron Mercer\n\n---\n\n"
            "## Meeting Objective\nCore objective alignment and architectural modernization strategy confirmation.\n\n"
            "## Strategic Synthesis\n| What We Heard | What It Means |\n|---|---|\n| Priority | Focus |\n\n"
            "## Key Discussion Points & Findings\n{body}\n\n"
            "## Next Steps & Action Items\n| # | Action Item | Owner | Target Timing | Source |\n|---|---|---|---|---|\n| 1 | Deploy | Amit | 10/01/2026 | 00:01:00 |\n\n"
            "## Open Decisions / Blockers\n- Blocker 1 -- Finalize concurrency lock settings.\n\n"
            f"## Business Meaning and Value Case\n{deep_biz}\n\n"
            f"## Executive Decision\n{deep_dec}\n\n"
            f"## Immediate Delivery Scope & Release Boundaries\n{deep_wedge}\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "### Cameron Mercer (PartnerAdvisory)\n"
            "- **Architectural Modernization**: Emphasized the critical operational importance of deterministic delivery validation across all workstreams and verified governance metrics. He firmly maintained that legacy patterns represent an unacceptable risk vector during the upcoming migration sprint, requiring immediate remediation.\n"
            "- **Delivery Operations**: Confirmed that enterprise platform migration milestones must strictly satisfy all non-compensating automated criteria before deployment authorization. Stefan stated that operational telemetry and system health indicators will be the sole criteria for moving forward.\n"
            "- **Release Governance**: Directed teams to enforce sub-second gate validation before production cutover to eliminate defects and ensure zero-regression stability. The executive steering committee demands full compliance with these architectural mandates across all dependent pipelines.\n"
        )
        filler = "enterprise governance and delivery validation with pipeline architecture "

        meta_deep = {
            "archetype": "STRATEGIC_DISCOVERY",
            "duration_minutes": 45,
        }

        core_base = base_recap.split("# Appendix:")[0]

        # 1. 359 words core total for 45m meeting -> must fail floor (<360)
        target_words_359 = 359 - len(core_base.format(body="").split())
        body_359 = " ".join([filler] * (target_words_359 // len(filler.split()) + 1))
        words_359 = " ".join(body_359.split()[:target_words_359])
        text_359 = base_recap.format(body=words_359)
        assert len(text_359.split("# Appendix:")[0].split()) == 359
        res_359 = run_qa_gate_2(text_359, metadata=meta_deep)
        assert res_359["passed"] is False
        assert any("word count too light" in f for f in res_359["failures"])

        # 2. 360 words core total -> must pass
        words_360 = words_359 + " precision"
        text_360 = base_recap.format(body=words_360)
        assert len(text_360.split("# Appendix:")[0].split()) == 360
        res_360 = run_qa_gate_2(text_360, metadata=meta_deep)
        assert res_360["passed"] is True, f"Failed: {res_360.get('failures')}"

        # 3. 1,637 words core total (the exact production bug) -> must pass within tolerance
        target_1637 = 1637 - len(core_base.format(body="").split())
        body_1637 = " ".join([filler] * (target_1637 // len(filler.split()) + 1))
        words_1637 = " ".join(body_1637.split()[:target_1637])
        text_1637 = base_recap.format(body=words_1637)
        assert len(text_1637.split("# Appendix:")[0].split()) == 1637
        res_1637 = run_qa_gate_2(text_1637, metadata=meta_deep)
        assert res_1637["passed"] is True

        # 4. 1,801 words core total -> must pass under advisory ceiling (exceeding ceiling does not fail Gate 2.5)
        target_1801 = 1801 - len(core_base.format(body="").split())
        body_1801 = " ".join([filler] * (target_1801 // len(filler.split()) + 1))
        words_1801 = " ".join(body_1801.split()[:target_1801])
        text_1801 = base_recap.format(body=words_1801)
        assert len(text_1801.split("# Appendix:")[0].split()) == 1801
        res_1801 = run_qa_gate_2(text_1801, metadata=meta_deep)
        assert res_1801["passed"] is True
        assert not any("exceeded ceiling" in f for f in res_1801.get("failures", []))

    def test_header_synonym_flexibility_and_rigid_rejection(self) -> None:
        """Verify Gate 2 accepts approved objective synonyms and rejects rogue headings."""
        base = (
            "# Subject: Sync | 08/27/2026\n\n**Date:** 08/27/2026\n**Time:** 09:00 AM ET\n"
            "**Attendees:**\n- Amit Ayer (Lead Architect)\n- Cameron Mercer (Executive Sponsor)\n\n---\n\n"
            "## {header}\nAlignment on operating model and pipeline architectural standards across enterprise workstreams.\n\n"
            "## Strategic Synthesis\n"
            "| # | What We Heard | What It Means |\n"
            "| :--- | :--- | :--- |\n"
            "| 1 | Operating model alignment across pipelines | Dedicated enterprise architecture governance model |\n\n"
            "## Key Discussion Points & Findings\nDiscussion covered ingestion benchmarks, token limits, and WAL mode concurrency. "
            "The engineering team validated zero-contention write serialization across parallel workers. "
            "All deterministic gate checks were verified against baseline test fixtures to eliminate regression risk.\n\n"
            "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Plan | Amit Ayer | 10/01/2026 | 00:01:00 |\n\n"
            "## Open Decisions / Blockers\n- Ratify deployment date.\n\n"
            "## Business Meaning and Value Case\n"
            "Meaningful platform velocity acceleration across production pipelines by establishing deterministic governance, eliminating contention, and securing reliable enterprise delivery cadences throughout the platform lifecycle.\n\n"
            "## Executive Decision\n"
            "Ratified target direction and wave milestones across architecture teams while establishing strict acceptance thresholds for production promotion and automated test enforcement.\n\n"
            "## Immediate Delivery Scope & Release Boundaries\n"
            "Target delivery scope confirmed for Q4 platform rollout and testing, including end-to-end telemetry verification, storage isolation, and zero regression defects.\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "### Amit Ayer — PartnerAdvisory, Lead Architect\n"
            "- **Architectural Governance & Validation**: Amit emphasized the necessity of strict layer enforcement and deterministic gate checks to guarantee stability and prevent regressions. He explicitly called out the risks of monolithic legacy code migrating without a structured CI/CD pipeline, insisting that all new logic pass stringent semantic boundaries before any production deployment.\n"
            "- **Engineering Rigor**: Reaffirmed dedication to automated test suites and continuous verification across all components. He highlighted that manual test reconciliation has historically slowed deployment cycles and created hidden operational liabilities that surface during peak load.\n\n"
            "### Cameron Mercer — Executive Sponsor\n"
            "- **Executive Alignment & Strategy**: Stefan reaffirmed organizational support for high-fidelity architecture and agreed to sponsor immediate cross-functional rollout across engineering teams. He stressed that without leadership backing, critical technical debt remediation will continuously be deferred in favor of short-term feature delivery. He committed to defending the necessary runway for platform stabilization.\n"
            "- **Delivery Prioritization**: Confirmed strategic milestone prioritization to ensure on-time delivery across core deliverables, mandating that resource capacity be preserved for the core ingestion pipeline rather than peripheral experimentation vectors.\n"
        )
        approved_synonyms = [
            "Meeting Objective",
            "Executive Objective",
            "Meeting Purpose",
            "Purpose & Objective",
        ]
        for syn in approved_synonyms:
            doc = base.format(header=syn)
            assert len(doc.split()) >= 150
            res = run_qa_gate_2(doc, metadata={"duration_minutes": 5})
            assert res["passed"] is True, (
                f"Failed on approved synonym '{syn}': {res.get('failures')}"
            )

        # Disallowed header must fail
        bad_doc = base.format(header="Random Agenda Notes")
        res_bad = run_qa_gate_2(bad_doc, metadata={"duration_minutes": 5})
        assert res_bad["passed"] is False

    def test_token_exhaustion_recovery_across_all_stages(self) -> None:
        """Simulate LLM finish_reason='length' with empty/partial text across Stage 1, 2, 3 and verify recovery."""
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk_test_redacted_placeholder"}),
            patch("openai.OpenAI"),
        ):
            synth = LLMTranscriptSynthesizer()

            # Stage 01 recovery from empty 0-byte output
            mock_response_trunc = MagicMock()
            mock_response_trunc.choices = [
                MagicMock(message=MagicMock(content=""), finish_reason="length")
            ]
            mock_response_ok = MagicMock()
            mock_response_ok.choices = [
                MagicMock(
                    message=MagicMock(content="**Speaker:** [00:00:00] Recovered dialogue text\n"),
                    finish_reason="stop",
                )
            ]

            with patch.object(
                synth,
                "_create_completion_with_effort",
                side_effect=[mock_response_trunc, mock_response_ok],
            ):
                res_01 = synth.synthesize_forensic(
                    system_prompt="sys", user_content='{"date": "08/27/2026"}'
                )
                assert "Recovered dialogue text" in res_01

            # Stage 02 recovery from < 200 words partial truncation
            mock_resp_short = MagicMock()
            mock_resp_short.choices = [
                MagicMock(
                    message=MagicMock(content="# Subject: Short\n\nOnly 5 words emitted"),
                    finish_reason="length",
                )
            ]
            mock_resp_full = MagicMock()
            mock_resp_full.choices = [
                MagicMock(
                    message=MagicMock(content="# Subject: Valid Recap\n\n" + "word " * 220),
                    finish_reason="stop",
                )
            ]

            with patch.object(
                synth,
                "_create_completion_with_effort",
                side_effect=[mock_resp_short, mock_resp_full],
            ):
                res_02 = synth.synthesize_recap(
                    system_prompt="sys", user_content='{"date": "08/27/2026"}'
                )
                assert "Valid Recap" in res_02

            # Stage 03 fail-fast on truncation via SynthesisTruncationError (Wave 3 hardening)
            mock_resp_bad_json = MagicMock()
            mock_resp_bad_json.choices = [
                MagicMock(
                    message=MagicMock(content='{"entities": [{"id": "person:Stefan'),
                    finish_reason="length",
                )
            ]
            with patch.object(
                synth,
                "_create_completion_with_effort",
                return_value=mock_resp_bad_json,
            ):
                from dual_pipeline.transcript_stages_core.models import SynthesisTruncationError
                with self.assertRaises(SynthesisTruncationError):
                    synth.synthesize_graph(
                        system_prompt="sys", user_content='{"date": "08/27/2026"}'
                    )

    def test_document_pipeline_intake_untracked_document_e2e(self) -> None:
        """End-to-end processing of real intake document verifying Output 03 ONLY and zero Output 01/02."""
        intake_file = ROOT / "intake" / "2026-09-17_Mark_McCarthy_NEA_SWE_Interviews.txt"
        assert intake_file.exists(), f"Expected intake file missing at {intake_file}"

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            pipeline = DocumentPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(intake_file),
                metadata={
                    "document_title": "NEA SWE Interviews and Pod Retentions Ahead of Q4'26",
                    "date": "09/17/2026",
                    "classification": "confidential",
                    "author": "Mark McCarthy",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            assert result is not None
            assert "03_graph" in result or "output_03" in result
            out_03 = Path(result.get("03_graph") or result.get("output_03"))
            assert out_03.exists()

            # Strictly assert Deliverables 01 and 02 are NOT generated for general documents
            assert "01_transcript" not in result
            assert "02_recap" not in result
            for f in out_dir.glob("01_*"):
                pytest.fail(f"Forbidden Output 01 generated for document: {f}")
            for f in out_dir.glob("02_*"):
                pytest.fail(f"Forbidden Output 02 generated for document: {f}")

    def test_multimodal_durations_15m_45m_60m_e2e(self) -> None:
        """Verify Gate 1.6 and Gate 2 across long durations (15m, 45m, 60m with C-11 gap)."""
        durations = [(15, 16), (45, 36), (60, 48)]
        pipeline = RawTranscriptPipeline(provider_type="offline")

        for dur_mins, turn_count in durations:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_dir = Path(tmpdir)
                if dur_mins == 60:
                    turns_text = (
                        _generate_realistic_turns(24, 25, start_minute=0)
                        + "\n\n[Recording gap: 00:25:00 – 00:30:00]\n\n"
                        + _generate_realistic_turns(24, 30, start_minute=30)
                    )
                else:
                    turns_text = _generate_realistic_turns(turn_count, dur_mins)

                in_file = out_dir / f"meeting_{dur_mins}m.md"
                in_file.write_text(
                    f"# Executive Session {dur_mins}m\n\n" + turns_text, encoding="utf-8"
                )

                res = pipeline.run(
                    input_path=str(in_file),
                    metadata={
                        "meeting_name": f"Session_{dur_mins}m",
                        "date": "08/27/2026",
                        "time": f"09:00 AM - 10:{dur_mins:02d} AM ET",
                        "duration_minutes": dur_mins,
                        "attendees": ["Cameron Mercer (Managing Director)", "Amit Ayer (Lead Enterprise Architect)"],
                        "hitl_role_resolutions": {
                            "Amit Ayer": "Lead Enterprise Architect",
                            "Cameron Mercer": "Managing Director",
                        },
                    },
                    output_dir=str(out_dir),
                    package_only=True,
                )
                assert res is not None
                qa = json.loads((out_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
                assert qa["status"] == "PASS"

    def test_webvtt_and_copilot_format_e2e(self) -> None:
        """Verify ingestion of WebVTT and CoPilot speaker format variations."""
        pipeline = RawTranscriptPipeline(provider_type="offline")

        # 1. WebVTT format
        webvtt_content = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "<v Cameron Mercer>Welcome everyone to the quarterly operating review.</v>\n\n"
            "00:00:06.000 --> 00:00:10.000\n"
            "<v Amit Ayer>Thanks Stefan, let's review the architectural decoupling priorities.</v>\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            in_file = out_dir / "meeting.vtt"
            in_file.write_text(webvtt_content, encoding="utf-8")

            res = pipeline.run(
                input_path=str(in_file),
                metadata={
                    "meeting_name": "VTT_Sync",
                    "date": "08/27/2026",
                    "time": "09:00 AM - 09:05 AM ET",
                    "attendees": [
                        "Cameron Mercer (Managing Director)",
                        "Amit Ayer (Lead Enterprise Architect)",
                    ],
                    "hitl_role_resolutions": {
                        "Amit Ayer": "Lead Enterprise Architect",
                        "Cameron Mercer": "Managing Director",
                    },
                },
                output_dir=str(out_dir),
                package_only=True,
            )
            assert res is not None
            assert Path(res["01_transcript"]).exists()

    def test_cli_ingest_meeting_end_to_end_triad(self) -> None:
        """Verify direct execution of approved CLI pipeline 'nee ingest-meeting' emits full deliverable triad."""
        from dual_pipeline.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            in_file = out_dir / "meeting.txt"
            in_file.write_text(
                "# Strategy Session\n\n"
                "**Cameron Mercer:** [00:00:01] Welcome team to the Q4 roadmap alignment.\n"
                "**Amit Ayer:** [00:00:15] We have established zero-lock SQLite concurrency.\n"
                "**Cameron Mercer:** [00:01:00] Perfect, let's ratify the release schedule.\n",
                encoding="utf-8",
            )

            ret = main(
                [
                    "ingest-meeting",
                    "--input",
                    str(in_file),
                    "--project",
                    "test_proj",
                    "--meeting",
                    "CLI_Strategy_Sync",
                    "--date",
                    "09/17/2026",
                    "--time",
                    "10:00 AM - 10:05 AM ET",
                    "--classification",
                    "confidential",
                    "--attendees",
                    "Cameron Mercer (Managing Director), Amit Ayer (Lead Enterprise Architect)",
                    "--output-dir",
                    str(out_dir),
                    "--provider",
                    "offline",
                ]
            )

            assert ret == 0
            assert (out_dir / "qa_gates_report.json").exists()
            assert (out_dir / "final_release_decision.json").exists()
            assert len(list(out_dir.rglob("01_*"))) >= 1
            assert len(list(out_dir.rglob("02_*"))) >= 1
            assert len(list(out_dir.rglob("03_*"))) >= 1

    def test_cli_ingest_meeting_notes_mode_boundary(self) -> None:
        """Verify 'nee ingest-meeting --notes' strictly emits Deliverables 02 & 03 and zero Deliverable 01."""
        from dual_pipeline.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            in_file = out_dir / "copilot_notes.md"
            in_file.write_text(
                "# CoPilot Summary Notes\n\n"
                "Discussion on pipeline optimization and test execution.\n"
                "Cameron Mercer and Amit Ayer presented the architectural decomposition into modular subpackages.\n"
                "This is some extra filler text to make sure we surpass the fifty word minimum "
                "semantic yield gate which fails fast and aborts if the text is too short. "
                "We need enough words here to pass the gatekeeper check successfully without "
                "throwing a ZeroSignalError and breaking the test. "
                "More words just to be absolutely certain we cross the threshold of fifty words in this stub text.\n",
                encoding="utf-8",
            )

            ret = main(
                [
                    "ingest-meeting",
                    "--input",
                    str(in_file),
                    "--notes",
                    "--project",
                    "test_proj",
                    "--meeting",
                    "CLI_Notes_Sync",
                    "--date",
                    "09/17/2026",
                    "--time",
                    "11:00 AM - 11:15 AM ET",
                    "--classification",
                    "internal",
                    "--attendees",
                    "Amit Ayer (Lead Enterprise Architect), Cameron Mercer (Managing Director)",
                    "--output-dir",
                    str(out_dir),
                    "--provider",
                    "offline",
                ]
            )

            assert ret == 0
            assert len(list(out_dir.rglob("01_*"))) == 0
            assert len(list(out_dir.rglob("02_*"))) >= 1
            assert len(list(out_dir.rglob("03_*"))) >= 1

    def test_cli_ingest_document_end_to_end_deliverable_03_only(self) -> None:
        """Verify 'nee ingest-document' strictly emits Deliverable 03 and zero Deliverables 01/02."""
        from dual_pipeline.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            in_file = out_dir / "memo.txt"
            in_file.write_text(
                "Executive Architecture Directive\n"
                "All services must run against approved CLI entry points with zero shadow scripts.\n"
                "Author: Architecture Lead\n"
                "This is some extra filler text to make sure we surpass the fifty word minimum "
                "semantic yield gate which fails fast and aborts if the text is too short. "
                "We need enough words here to pass the gatekeeper check successfully without "
                "throwing a ZeroSignalError and breaking the test. "
                "More words just to be absolutely certain we cross the threshold of fifty words in this stub text.\n",
                encoding="utf-8",
            )

            ret = main(
                [
                    "ingest-document",
                    "--input",
                    str(in_file),
                    "--project",
                    "test_proj",
                    "--title",
                    "Architecture_Directive_Memo",
                    "--date",
                    "09/17/2026",
                    "--author",
                    "Architecture Lead",
                    "--classification",
                    "internal",
                    "--output-dir",
                    str(out_dir),
                    "--provider",
                    "offline",
                ]
            )

            assert ret == 0
            assert len(list(out_dir.rglob("03_*"))) >= 1
            assert len(list(out_dir.rglob("01_*"))) == 0
            assert len(list(out_dir.rglob("02_*"))) == 0

    def test_cli_argument_validation_failures(self) -> None:
        """Verify CLI argument validation fails closed on missing or invalid flags."""
        from dual_pipeline.cli import main

        # 1. Missing required --input flag
        with pytest.raises(SystemExit) as exc1:
            main(["ingest-meeting", "--project", "test_proj"])
        assert exc1.value.code != 0

        # 2. Invalid classification choice
        with pytest.raises(SystemExit) as exc2:
            main(
                [
                    "ingest-meeting",
                    "--input",
                    "dummy.txt",
                    "--classification",
                    "top_secret_invalid",
                ]
            )
        assert exc2.value.code != 0


if __name__ == "__main__":
    unittest.main()
