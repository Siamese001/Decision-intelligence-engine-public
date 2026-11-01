#!/usr/bin/env python3
"""Generates durable Wave 1 runtime receipt for D02 Executive Recap call budget and telemetry.

Implements the operator-approved HITL decision:
- Default: Deterministic mock transport reproducing exact baseline call budget (0 token cost).
- Optional: --live-baseline flag executing against live Gemini API on demand.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger  # noqa: E402
from dual_pipeline.transcript_stages_core.recap_modular_dag import (  # noqa: E402
    execute_d02_3pass_dag,
)


def generate_baseline_receipt(live_baseline: bool = False, output_path: Optional[str] = None) -> Path:
    fixture_path = ROOT / "inputs" / "project_atlas_transcript_attributed_08_28_2026.md"
    if fixture_path.exists():
        with open(fixture_path, "r", encoding="utf-8") as f:
            transcript_text = f.read()
    else:
        transcript_text = "Speaker 1: Reviewing system architecture.\nSpeaker 2: Agree on milestones.\n" * 50

    sample_metadata = {
        "meeting": "Project Atlas Architecture Review",
        "date": "08/28/2026",
        "archetype": "TECHNICAL_ARCHITECTURE_REVIEW",
        "attendees": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Principal Architect"},
            {"name": "Jen", "org": "PartnerAdvisory", "role": "Delivery Lead"},
        ],
    }

    user_content = (
        "<mandatory_structure>\n"
        "Must include: ## Executive Summary, ## Key Discussion Points, ## Architecture / Technical Decisions, "
        "## Action & Governance Ledger, and # Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown.\n"
        "</mandatory_structure>\n\n"
        f"<meeting_metadata>\n{json.dumps(sample_metadata, indent=2)}\n</meeting_metadata>\n\n"
        f"<forensic_transcript>\n{transcript_text}\n</forensic_transcript>\n\n"
        "<prior_decisions>\n"
        "[{\"decision_id\": \"DEC-20260820-01\", \"summary\": \"Adopt modular DAG for synthesis\"}]\n"
        "</prior_decisions>\n"
    )

    system_prompt = (
        "You are an executive intelligence analyst synthesizing Artifact 02 (executive_recap.md) "
        "under RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md."
    )

    out_file = Path(output_path) if output_path else ROOT / "receipts" / "d02_call_budget_baseline_w1.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ledger = D02CallLedger(operation_id="d02-baseline-w1")

    if live_baseline:
        print("[BASELINE] Running live Gemini API baseline...")
        stitched = execute_d02_3pass_dag(
            model="gemini-3.8-flash",  # noqa: SSOT_EXEMPT
            system_prompt=system_prompt,
            user_content=user_content,
            min_words_2a=800,
            min_words_2c=600,
            ledger=ledger,
            receipt_path=str(out_file),
        )
    else:
        print("[BASELINE] Running deterministic mock baseline (0 token cost)...")
        narrative_mock = "# Project Atlas Executive Recap\n\n## Strategic Narrative\n" + (" substantive technical finding and architectural decision details" * 160)
        ledger_mock = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Validate Stage 02 Call Budget | Amit Ayer | 10/03/2026 | Enforce W1 Telemetry Gate |\n"
            "| 2 | Finalize Modular DAG Refactor | Jen | 10/04/2026 | Review Gate Receipts |"
        )
        perspectives_mock = (
            "# Amit Ayer - PartnerAdvisory, Principal Architect\n"
            "- **Architecture**: Spearheaded call budget observability and modular DAG hardening.\n\n"
            "# Jen - PartnerAdvisory, Delivery Lead\n"
            "- **Delivery**: Aligned timeline and verified sprint deliverables.\n"
        ) + (" forensic contributions and technical review points" * 120)

        def make_mock_resp(text: str, p_toks: int, o_toks: int):
            payload = {
                "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": {
                    "promptTokenCount": p_toks,
                    "candidatesTokenCount": o_toks,
                    "totalTokenCount": p_toks + o_toks,
                    "cachedContentTokenCount": 0,
                },
            }
            m = MagicMock()
            m.read.return_value = json.dumps(payload).encode("utf-8")
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        def urlopen_side_effect(req, timeout=120.0):
            req_body = json.loads(req.data.decode("utf-8"))
            sys_inst = req_body.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
            # Estimate prompt tokens based on payload size
            prompt_toks = len(req.data) // 4
            if "Action & Governance Ledger" in sys_inst:
                return make_mock_resp(ledger_mock, prompt_toks, 180)
            elif "executive forensic analyst" in sys_inst:
                return make_mock_resp(perspectives_mock, prompt_toks, 650)
            else:
                return make_mock_resp(narrative_mock, prompt_toks, 850)

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), \
             patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake-api-key"):
            stitched = execute_d02_3pass_dag(
                model="gemini-3.8-flash",  # noqa: SSOT_EXEMPT
                system_prompt=system_prompt,
                user_content=user_content,
                min_words_2a=800,
                min_words_2c=600,
                ledger=ledger,
                receipt_path=str(out_file),
            )
            assert stitched is not None

    print(f"[RECEIPT] Baseline receipt emitted to: {out_file}")
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"  Total Calls: {data['total_calls']}")
    print(f"  Scenario: {data['scenario']}")
    print(f"  Token Totals: {data['token_totals']}")
    print("  Prompt Blocks Byte Breakdown:")
    for k, v in data.get("prompt_block_bytes", {}).items():
        print(f"    {k}: {v:,} bytes")

    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit D02 call budget baseline receipt.")
    parser.add_argument("--live-baseline", action="store_true", help="Execute against live Gemini API.")
    parser.add_argument("--output", "-o", type=str, default=None, help="Target receipt JSON path.")
    args = parser.parse_args()
    generate_baseline_receipt(live_baseline=args.live_baseline, output_path=args.output)


if __name__ == "__main__":
    main()
