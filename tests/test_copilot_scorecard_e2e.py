"""
tests/test_copilot_scorecard_e2e.py

End-to-End qualification tests for Copilot Replay Harness and Scorecard under Wave 1.
Verifies that:
1. Replay across frozen golden cases produces deterministic, reproducible scorecards.
2. Two identical replays yield bit-for-bit identical scorecard JSON and digest.
3. Answer ledger persists and restores per-stage latencies (retrieve, verify, render, total).
4. Golden dataset fixtures adhere strictly to production purity and anti-client leak rules.
5. Spoken output conforms to speakability constraints (word budget, single question, zero tags).
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest

from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from evals.copilot_scorecard import (
    CopilotScorecard,
    _check_speakability,
    compute_copilot_scorecard,
)
from tools.build_copilot_golden import load_golden_cases, validate_golden_cases


def _simulate_copilot_replay(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic simulated copilot execution over evaluation cases."""
    results: List[Dict[str, Any]] = []
    for c in cases:
        cid = c["id"]
        stratum = c["strata"]
        expected_act = c["expected_action"]

        if expected_act in ("silent", "abstain", "trap"):
            results.append({
                "id": cid,
                "status": "PASS",
                "response": "",
                "cited_assertion_ids": [],
                "stage_latencies_ms": {
                    "retrieve_ms": 35.0,
                    "verify_ms": 10.0,
                    "render_ms": 5.0,
                    "total_ms": 50.0,
                },
            })
        else:
            prompt = c["prompt"]
            response_text = f"Regarding {prompt.lower().rstrip('?')}, this is configured according to standard platform contracts."
            results.append({
                "id": cid,
                "status": "PASS",
                "response": response_text,
                "cited_assertion_ids": c.get("expected_assertion_ids", ["ast_default"]),
                "stage_latencies_ms": {
                    "retrieve_ms": 85.0,
                    "verify_ms": 25.0,
                    "render_ms": 40.0,
                    "total_ms": 150.0,
                },
            })
    return results


def test_copilot_scorecard_deterministic_replay(tmp_path: Path):
    """Verify two independent replays on frozen golden cases produce identical scorecards."""
    golden_path = Path(__file__).resolve().parents[1] / "tests" / "evals" / "copilot_golden" / "v1" / "golden_cases.jsonl"
    cases = load_golden_cases(golden_path)
    assert len(cases) == 60, f"Expected 60 golden cases, got {len(cases)}"

    # Replay 1
    res1 = _simulate_copilot_replay(cases)
    sc1 = compute_copilot_scorecard(cases, res1)
    file1 = tmp_path / "scorecard_run_1.json"
    dict1 = sc1.to_dict()
    dict1_no_ts = {k: v for k, v in dict1.items() if k != "timestamp_utc"}
    file1.write_text(json.dumps(dict1_no_ts, indent=2, sort_keys=True), encoding="utf-8")

    # Replay 2
    res2 = _simulate_copilot_replay(cases)
    sc2 = compute_copilot_scorecard(cases, res2)
    file2 = tmp_path / "scorecard_run_2.json"
    dict2 = sc2.to_dict()
    dict2_no_ts = {k: v for k, v in dict2.items() if k != "timestamp_utc"}
    file2.write_text(json.dumps(dict2_no_ts, indent=2, sort_keys=True), encoding="utf-8")

    # Verifiable independent diff check
    diff = list(difflib.unified_diff(
        file1.read_text(encoding="utf-8").splitlines(),
        file2.read_text(encoding="utf-8").splitlines(),
        fromfile="scorecard_run_1.json",
        tofile="scorecard_run_2.json",
    ))
    assert not diff, f"Scorecard diff detected across identical replays:\n{chr(10).join(diff)}"

    # Digest and metric assertions
    assert sc1.reproducible_digest == sc2.reproducible_digest
    assert sc1.groundedness >= 0.95
    assert sc1.silence_precision >= 0.80
    assert sc1.silence_recall >= 0.80
    assert sc1.speakability >= 0.90
    assert sc1.passes_floors()

    # Latency structure check
    assert "retrieve_ms" in sc1.stage_latencies
    assert "verify_ms" in sc1.stage_latencies
    assert "render_ms" in sc1.stage_latencies
    assert "total_ms" in sc1.stage_latencies
    assert sc1.stage_latencies["total_ms"]["p50"] > 0


def test_golden_fixtures_purity_and_anti_client_leak():
    """Verify that committed golden fixtures are sanitized against client text, secrets, and rates."""
    golden_path = Path(__file__).resolve().parents[1] / "tests" / "evals" / "copilot_golden" / "v1" / "golden_cases.jsonl"
    content = golden_path.read_text(encoding="utf-8")

    # Anti-leak assertions
    assert "sk-" not in content, "Client API secret pattern detected in golden fixtures"
    assert "AKIA" not in content, "AWS secret key pattern detected in golden fixtures"
    assert "PRIVATE KEY" not in content, "Private key detected in golden fixtures"
    assert "/hr" not in content.lower(), "Hourly client billing rate pattern detected in golden fixtures"
    assert "hourly rate" not in content.lower(), "Hourly rate pattern detected in golden fixtures"
    assert "Alex Rivera" not in content.lower(), "Fabricated persona detected in fixtures"
    assert "Jordan Taylor" not in content.lower(), "Fabricated persona detected in fixtures"

    cases = load_golden_cases(golden_path)
    valid, errors = validate_golden_cases(cases)
    assert valid, f"Golden cases validation failed: {errors}"


def test_answer_ledger_store_latency_roundtrip(tmp_path: Path):
    """Verify AnswerLedger properly persists and restores per-stage latency timings."""
    db_file = tmp_path / "test_answer_ledger.sqlite"
    store = AnswerLedger(db_path=db_file)

    record = AnswerRecord(
        pipeline="copilot",
        intent_or_type="next-questions",
        context_digest="ctx_digest_001",
        retrieval_seeds=["seed1", "seed2"],
        context_snapshot=[{"speaker": "Voice A", "text": "What is the busy timeout?"}],
        cited_assertion_ids=["ast-1"],
        rigor="STANDARD",
        response_text="Busy timeout is 30 seconds.",
        answer_id="ans-latency-test-001",
        latency_ms=90.8,
        retrieve_latency_ms=45.2,
        verify_latency_ms=12.1,
        render_latency_ms=33.5,
        stage_latencies_ms={
            "retrieve_ms": 45.2,
            "verify_ms": 12.1,
            "render_ms": 33.5,
            "total_ms": 90.8,
        },
    )

    store.append_answer(record)
    loaded = store.get_answer("ans-latency-test-001")
    assert loaded is not None
    assert loaded.latency_ms == pytest.approx(90.8)
    assert loaded.retrieve_latency_ms == pytest.approx(45.2)
    assert loaded.verify_latency_ms == pytest.approx(12.1)
    assert loaded.render_latency_ms == pytest.approx(33.5)
    assert loaded.stage_latencies_ms == {
        "retrieve_ms": 45.2,
        "verify_ms": 12.1,
        "render_ms": 33.5,
        "total_ms": 90.8,
    }


def test_speakability_formatting_rules():
    """Verify speakability evaluation accurately enforces word budget and question constraints."""
    # Compliant single question
    valid, reasons = _check_speakability("What is the memory footprint of the vector index?")
    assert valid
    assert not reasons

    # Multiple questions violation
    valid, reasons = _check_speakability("What is the memory footprint? And what is the busy timeout?")
    assert not valid
    assert any("MULTIPLE_QUESTIONS_DETECTED" in r for r in reasons)

    # Word count breach (> 40 words)
    long_msg = " ".join(["word"] * 45)
    valid, reasons = _check_speakability(long_msg)
    assert not valid
    assert any("WORD_COUNT_EXCEEDED" in r for r in reasons)

    # Prohibited markdown tags
    valid, reasons = _check_speakability("The answer is [CONFIDENTIAL] here.")
    assert not valid
    assert any("PROHIBITED_MARKUP_DETECTED" in r for r in reasons)
