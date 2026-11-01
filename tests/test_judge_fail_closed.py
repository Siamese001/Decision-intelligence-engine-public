"""
tests/test_judge_fail_closed.py

Regression test verifying that LLM judges and manifest pipeline executor
strictly fail closed (raise RuntimeError) when mock=False and live credentials
are absent or client is None, adhering to the Zero-Mocks invariant.
"""

import pytest
from dual_pipeline.llm_judge_core.judges import (
    AgenticLeverageJudge,
    EnterpriseGraphReviewJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
    HolisticReleaseJudge,
)

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.manifest_runner.executor import _run_judge


@pytest.fixture
def mock_creds_without_live_key(monkeypatch):
    """Ensure hermetic environment without live credentials under Zero Mocks invariant."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from config.credentials import OpenAICredentials
    yield OpenAICredentials(
        api_key=None, is_mock=True, model="gpt-5.6-sol", reasoning_effort="max",
        masked_key="[MOCK]", source="test",
    )


def test_enterprise_judge_fails_closed(mock_creds_without_live_key):
    judge = EnterpriseGraphReviewJudge(mock=False)
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate(canonical_graph_data={"nodes": []}, client=None, mock=False)


def test_leverage_judge_fails_closed(mock_creds_without_live_key):
    judge = AgenticLeverageJudge(mock=False)
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate_agentic_leverage(node={"speaker": "Alice"}, client=None, mock=False)


def test_forensic_judge_fails_closed(mock_creds_without_live_key):
    judge = ForensicTranscriptJudge()
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate(f1_text="hello", raw_source_text="hello", client=None, mock=False)


def test_recap_judge_fails_closed(mock_creds_without_live_key):
    judge = ExecutiveRecapJudge()
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate(f2_text="hello", f1_text="hello", client=None, mock=False)


def test_graph_judge_fails_closed(mock_creds_without_live_key):
    judge = GraphExtractionJudge(mock=False)
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate(f3_text="{}", f1_text="hello", client=None, mock=False)


def test_holistic_judge_fails_closed(mock_creds_without_live_key):
    judge = HolisticReleaseJudge(mock=False)
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        judge.evaluate(
            output_1_text="t1",
            output_2_text="t2",
            output_3_text="{}",
            raw_transcript="src",
            client=None,
            mock=False,
        )


def test_manifest_runner_run_judge_fails_closed(mock_creds_without_live_key, tmp_path):
    manifest = IngestionMeetingManifest(
        project_name="project_test",
        meeting_title="Test Meeting",
        meeting_date="09/14/2026",
        start_time_et="10:00 AM",
        end_time_et="11:00 AM",
    )
    with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
        _run_judge(
            manifest=manifest,
            raw_text="raw",
            f1_text="f1",
            f2_text="f2",
            f3_data={},
            qa_meta={},
            mock=False,
        )
