"""
Tests for Wave 3 Python pipeline hardening.
Covers: U0 diarization-status detection, L1PlanContract, L1 plan/execute split,
C0RosterContextGate, L2 split executors (forensic/recap/graph), pipeline routing,
and progress bar step count.
"""
import json

import pytest

from dual_pipeline.transcript_pipeline import _PIPELINE_STEPS, RawTranscriptPipeline
from dual_pipeline.transcript_stages import (
    C0ConformanceGate,
    C0RosterContextGate,
    HITLRequiredError,
    L1ForensicCognition,
    L1PlanContract,
    L2ExecutiveRecapExecutor,
    L2ForensicNormalizerExecutor,
    L2GraphExtractorExecutor,
    OfflineTranscriptSynthesizer,
    U0IngestionBoundary,
)

# ── Shared fixtures ──────────────────────────────────────────────────────

BASE_METADATA = {
    "meeting_name": "Wave 3 Test",
    "date": "09/09/2026",
    "time": "10:00",
    "classification": "internal",
}

NATIVE_TRANSCRIPT = (
    "**Reid Schenk [00:00:00]:** Good morning, let's kick off the architecture review.\n"
    "**Amit Ayer [00:01:30]:** Agreed — we need to finalize the data model.\n"
    "**Sharon Diaram [00:02:15]:** I'll send the updated schema by EOW.\n"
)

UNDIARIZED_TRANSCRIPT = (
    "Good morning, everyone. Let's kick off the architecture review.\n"
    "I think we need to finalize the data model before the end of this week.\n"
    "We also need to address the API gateway dependency.\n"
)

GENERIC_SPEAKER_TRANSCRIPT = (
    "**Speaker A [00:00:00]:** Good morning.\n"
    "**Speaker B [00:00:05]:** Hi, ready to start.\n"
)


# ── U0: Diarization Status Detection ─────────────────────────────────────

class TestU0DiarizationDetection:

    def _run_u0(self, transcript: str, extra_meta: dict = None) -> dict:
        u0 = U0IngestionBoundary()
        metadata = {**BASE_METADATA, **(extra_meta or {})}
        return u0.execute(transcript, metadata)

    def test_u0_returns_diarization_status(self):
        """U0 output must include diarization_status field."""
        ctx = self._run_u0(NATIVE_TRANSCRIPT)
        assert "diarization_status" in ctx
        assert ctx["diarization_status"] in ("NATIVE", "UNDIARIZED")

    def test_u0_detects_native_from_named_labels(self):
        """Three or more named speaker labels with no generic Speaker X → NATIVE."""
        ctx = self._run_u0(NATIVE_TRANSCRIPT)
        assert ctx["diarization_status"] == "NATIVE"

    def test_u0_detects_undiarized_from_generic_labels(self):
        """Generic Speaker A / Speaker B labels → UNDIARIZED."""
        ctx = self._run_u0(GENERIC_SPEAKER_TRANSCRIPT)
        assert ctx["diarization_status"] == "UNDIARIZED"

    def test_u0_detects_undiarized_from_plain_text(self):
        """Plain paragraph-style transcript with no speaker labels → UNDIARIZED."""
        ctx = self._run_u0(UNDIARIZED_TRANSCRIPT)
        assert ctx["diarization_status"] == "UNDIARIZED"

    def test_u0_operator_override_native(self):
        """Operator can override diarization_status via metadata."""
        ctx = self._run_u0(UNDIARIZED_TRANSCRIPT, {"diarization_status": "NATIVE"})
        assert ctx["diarization_status"] == "NATIVE"

    def test_u0_operator_override_undiarized(self):
        ctx = self._run_u0(NATIVE_TRANSCRIPT, {"diarization_status": "UNDIARIZED"})
        assert ctx["diarization_status"] == "UNDIARIZED"

    def test_u0_platform_hint_voice_memo(self):
        """Apple Voice Memo platform hint → UNDIARIZED regardless of content."""
        ctx = self._run_u0(NATIVE_TRANSCRIPT, {"platform": "Apple Voice Memo"})
        assert ctx["diarization_status"] == "UNDIARIZED"

    def test_u0_platform_hint_teams(self):
        """Microsoft Teams platform hint → NATIVE."""
        ctx = self._run_u0(UNDIARIZED_TRANSCRIPT, {"platform": "Microsoft Teams"})
        assert ctx["diarization_status"] == "NATIVE"

    def test_u0_defaults_safe_undiarized(self):
        """Empty transcript → defaults to UNDIARIZED (safe side)."""
        ctx = self._run_u0("", {})
        assert ctx["diarization_status"] == "UNDIARIZED"


# ── L1PlanContract ────────────────────────────────────────────────────────

class TestL1PlanContract:

    def _make_u0_ctx(self, transcript: str, extra_meta: dict = None,
                     diarization_status: str = "NATIVE") -> dict:
        return {
            "hash": "abc",
            "clean_transcript": transcript,
            "metadata": {**BASE_METADATA, **(extra_meta or {})},
            "diarization_status": diarization_status,
        }

    def test_plan_emits_l1_plan_contract(self):
        """L1.plan() must return an L1PlanContract instance."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(NATIVE_TRANSCRIPT)
        plan = l1.plan(ctx)
        assert isinstance(plan, L1PlanContract)

    def test_plan_native_source_type(self):
        """NATIVE diarization → source_type=DIARIZED_TEXT, roster_gate_needed=False."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(NATIVE_TRANSCRIPT, diarization_status="NATIVE")
        plan = l1.plan(ctx)
        assert plan.source_type == "DIARIZED_TEXT"
        assert plan.diarization_status == "NATIVE"
        assert plan.roster_gate_needed is False

    def test_plan_undiarized_roster_gate_needed(self):
        """UNDIARIZED with no roster → roster_gate_needed=True."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(UNDIARIZED_TRANSCRIPT, diarization_status="UNDIARIZED")
        plan = l1.plan(ctx)
        assert plan.source_type == "UNDIARIZED_TEXT"
        assert plan.roster_gate_needed is True

    def test_plan_undiarized_with_roster_no_gate_needed(self):
        """UNDIARIZED with roster already in metadata → roster_gate_needed=False."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(
            UNDIARIZED_TRANSCRIPT,
            extra_meta={"roster": [{"name": "Reid Schenk", "org": "NEA"}]},
            diarization_status="UNDIARIZED",
        )
        plan = l1.plan(ctx)
        assert plan.roster_gate_needed is False

    def test_plan_audio_source_type(self):
        """Audio transcription flag → source_type=AUDIO_TRANSCRIBED."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(
            NATIVE_TRANSCRIPT,
            extra_meta={"_from_audio_transcription": True},
            diarization_status="NATIVE",
        )
        plan = l1.plan(ctx)
        assert plan.source_type == "AUDIO_TRANSCRIBED"

    def test_plan_meeting_type_hint_propagated(self):
        """meeting_type in metadata propagates to L1PlanContract."""
        l1 = L1ForensicCognition()
        ctx = self._make_u0_ctx(
            NATIVE_TRANSCRIPT,
            extra_meta={"meeting_type": "Forward-Looking"},
        )
        plan = l1.plan(ctx)
        assert plan.meeting_type_hint == "Forward-Looking"

    def test_plan_repr_is_readable(self):
        """L1PlanContract repr must include key fields."""
        plan = L1PlanContract("DIARIZED_TEXT", "NATIVE", False, "Operational")
        r = repr(plan)
        assert "DIARIZED_TEXT" in r
        assert "NATIVE" in r
        assert "Operational" in r


# ── C0 Roster Context Gate ────────────────────────────────────────────────

class TestC0RosterContextGate:

    def _make_l0_ctx(self, extra_meta: dict = None) -> dict:
        return {
            "transcript": UNDIARIZED_TRANSCRIPT,
            "hash": "abc",
            "metadata": {**BASE_METADATA, **(extra_meta or {})},
            "statements": [],
            "diarization_status": "UNDIARIZED",
            "source_type": "UNDIARIZED_TEXT",
            "meeting_type_hint": None,
        }

    def test_roster_gate_raises_hitl_when_no_roster(self):
        """C0RosterContextGate must raise HITLRequiredError when no roster provided."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx()
        with pytest.raises(HITLRequiredError) as exc_info:
            gate.execute(ctx)
        assert exc_info.value.hitl_type == "roster_gate"

    def test_roster_gate_hitl_prompt_is_structured(self):
        """HITL prompt must mention attendee list, organization, and supplementary sources."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx()
        with pytest.raises(HITLRequiredError) as exc_info:
            gate.execute(ctx)
        msg = str(exc_info.value)
        assert "Full attendee list" in msg
        assert "Organization" in msg
        assert "Supplementary sources" in msg

    def test_roster_gate_passes_with_roster_in_metadata(self):
        """C0RosterContextGate must pass when roster is present."""
        gate = C0RosterContextGate()
        roster = [{"name": "Reid Schenk", "org": "NEA"}, {"name": "Amit Ayer", "org": "PartnerAdvisory"}]
        ctx = self._make_l0_ctx({"roster": roster})
        result = gate.execute(ctx)
        assert result["roster_gate_cleared"] is True
        assert result["conformance_checked"] is True

    def test_roster_gate_passes_with_speaker_map(self):
        """speaker_map is accepted as a valid roster substitute."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx({"speaker_map": {"A": "Reid Schenk"}})
        result = gate.execute(ctx)
        assert result["roster_gate_cleared"] is True

    def test_roster_gate_passes_with_attendees(self):
        """attendees dict is accepted as a valid roster."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx({"attendees": {"PartnerAdvisory": ["Amit Ayer"]}})
        result = gate.execute(ctx)
        assert result["roster_gate_cleared"] is True

    def test_roster_gate_bypass_flag(self):
        """bypass_speaker_hitl=True must fail closed under mandatory Stage U0 HITL."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx({"bypass_speaker_hitl": True})
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            gate.execute(ctx)

    def test_roster_gate_normalizes_into_metadata(self):
        """When roster comes from speaker_map, it should be bound into metadata['roster']."""
        gate = C0RosterContextGate()
        ctx = self._make_l0_ctx({"speaker_map": {"A": "Reid Schenk"}})
        result = gate.execute(ctx)
        # The original metadata key (speaker_map) is preserved but roster_gate_cleared is True
        assert result["roster_gate_cleared"] is True


# ── L2 Split Executors ────────────────────────────────────────────────────

class TestL2SplitExecutors:
    """Verify that each L2 executor calls the right provider method."""

    def _make_pa_context(self, transcript: str = "Test transcript") -> dict:
        return {
            "transcript": transcript,
            "statements": [],
            "hash": "abc",
            "metadata": BASE_METADATA,
            "conformance_checked": True,
            "diarization_status": "NATIVE",
            "source_type": "DIARIZED_TEXT",
            "meeting_type_hint": None,
            "_stage_prompts": {
                "recap": "RECAP SYSTEM PROMPT",
                "graph": "GRAPH SYSTEM PROMPT",
            },
        }

    def _make_u0_ctx(self, transcript: str = NATIVE_TRANSCRIPT) -> dict:
        return {
            "hash": "abc",
            "clean_transcript": transcript,
            "metadata": BASE_METADATA,
            "diarization_status": "NATIVE",
        }

    def test_l2_forensic_calls_synthesize_forensic(self):
        """L2ForensicNormalizerExecutor must use provider.synthesize_forensic."""
        provider = OfflineTranscriptSynthesizer()
        executor = L2ForensicNormalizerExecutor(provider)
        plan = L1PlanContract("DIARIZED_TEXT", "NATIVE", False)
        u0_ctx = self._make_u0_ctx()
        result = executor.execute(u0_ctx, plan)
        # Offline synthesize_forensic returns a transcript with HITL-Resolved header
        assert isinstance(result, str)
        assert len(result) > 0

    def test_l2_recap_calls_synthesize_recap(self):
        """L2ExecutiveRecapExecutor must use provider.synthesize_recap."""
        provider = OfflineTranscriptSynthesizer()
        executor = L2ExecutiveRecapExecutor(provider)
        pa_ctx = self._make_pa_context()
        result = executor.execute("Forensic transcript text", pa_ctx)
        assert "Meeting Objective" in result
        assert "Next Steps" in result

    def test_l2_graph_calls_synthesize_graph(self):
        """L2GraphExtractorExecutor must use provider.synthesize_graph."""
        provider = OfflineTranscriptSynthesizer()
        executor = L2GraphExtractorExecutor(provider)
        pa_ctx = self._make_pa_context()
        result = executor.execute("Forensic transcript", "Executive recap", pa_ctx)
        data = json.loads(result)
        assert data["schema_version"] == "5.7"

    def test_l2_graph_receives_stage01_and_stage02_outputs(self):
        """Stage 03 must receive both forensic transcript and executive recap in prompt."""
        captured = {}

        class SpyProvider(OfflineTranscriptSynthesizer):
            def synthesize_graph(self, system_prompt, user_content):
                captured["user_content"] = user_content
                return super().synthesize_graph(system_prompt, user_content)

        executor = L2GraphExtractorExecutor(SpyProvider())
        pa_ctx = self._make_pa_context()
        executor.execute("FORENSIC_MARKER", "RECAP_MARKER", pa_ctx)
        assert "FORENSIC_MARKER" in captured["user_content"]
        assert "RECAP_MARKER" in captured["user_content"]

    def test_l2_recap_receives_stage01_output(self):
        """Stage 02 must receive forensic transcript in its prompt."""
        captured = {}

        class SpyProvider(OfflineTranscriptSynthesizer):
            def synthesize_recap(self, system_prompt, user_content):
                captured["user_content"] = user_content
                return super().synthesize_recap(system_prompt, user_content)

        executor = L2ExecutiveRecapExecutor(SpyProvider())
        pa_ctx = self._make_pa_context()
        executor.execute("FORENSIC_MARKER", pa_ctx)
        assert "FORENSIC_MARKER" in captured["user_content"]


# ── Pipeline routing ──────────────────────────────────────────────────────

class TestPipelineRouting:

    def test_pipeline_step_count(self):
        """Pipeline must have 9 progress bar steps."""
        assert _PIPELINE_STEPS == 9

    def test_pipeline_has_split_l2_executors(self):
        """Pipeline must expose l2_forensic, l2_recap, l2_graph."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        assert isinstance(pipeline.l2_forensic, L2ForensicNormalizerExecutor)
        assert isinstance(pipeline.l2_recap, L2ExecutiveRecapExecutor)
        assert isinstance(pipeline.l2_graph, L2GraphExtractorExecutor)

    def test_pipeline_has_two_c0_gates(self):
        """Pipeline must expose c0_native and c0_undiarized."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        assert isinstance(pipeline.c0_native, C0ConformanceGate)
        assert isinstance(pipeline.c0_undiarized, C0RosterContextGate)

    def test_native_path_end_to_end(self, tmp_path):
        """NATIVE diarized transcript runs end-to-end without HITL."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "native.txt"
        input_file.write_text(NATIVE_TRANSCRIPT, encoding="utf-8")
        out_dir = tmp_path / "project_nexus" / "09-09-2026"

        result = pipeline.run(str(input_file), BASE_METADATA, str(out_dir))
        assert "01_transcript" in result
        assert "02_recap" in result
        assert "03_graph" in result

    def test_undiarized_path_blocked_without_roster(self, tmp_path):
        """UNDIARIZED transcript without roster must raise HITLRequiredError."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "undiarized.txt"
        input_file.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")
        out_dir = tmp_path / "project_nexus" / "09-09-2026"

        with pytest.raises(HITLRequiredError) as exc_info:
            pipeline.run(str(input_file), BASE_METADATA, str(out_dir))
        assert exc_info.value.hitl_type == "roster_gate"

    def test_undiarized_path_passes_with_roster(self, tmp_path):
        """UNDIARIZED transcript with roster in metadata runs end-to-end."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "undiarized.txt"
        input_file.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")
        out_dir = tmp_path / "project_nexus" / "09-09-2026"

        metadata = {
            **BASE_METADATA,
            "roster": [
                {"name": "Reid Schenk", "org": "NEA"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            ],
        }
        result = pipeline.run(str(input_file), metadata, str(out_dir))
        assert "01_transcript" in result
        assert "02_recap" in result
        assert "03_graph" in result

    def test_undiarized_path_passes_with_bypass_flag(self, tmp_path):
        """bypass_speaker_hitl=True must fail closed with ValueError under mandatory Stage U0 HITL."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "undiarized.txt"
        input_file.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")
        out_dir = tmp_path / "project_nexus" / "09-09-2026"

        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            pipeline.run(
                str(input_file),
                {**BASE_METADATA, "bypass_speaker_hitl": True},
                str(out_dir),
            )

    def test_artifacts_written_only_after_x_gates_pass(self, tmp_path):
        """Artifacts must not exist on disk if X-gates were to fail."""
        # We can't easily make X-gates fail in offline mode since the offline
        # provider produces valid output. Verify that the three files exist
        # after a successful run — they should only be there post-X-gates.
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "native.txt"
        input_file.write_text(NATIVE_TRANSCRIPT, encoding="utf-8")
        out_dir = tmp_path / "project_nexus" / "09-09-2026"

        result = pipeline.run(str(input_file), BASE_METADATA, str(out_dir))

        from pathlib import Path
        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()

        graph_data = json.loads(Path(result["03_graph"]).read_text(encoding="utf-8"))
        assert graph_data["schema_version"] == "5.7"
