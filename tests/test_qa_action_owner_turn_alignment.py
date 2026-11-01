"""Wave 3 unit and counterfactual tests: Gate 2.15, Gate Cross.3, and Gate X3 turn-window owner & parity enforcement."""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract
from dual_pipeline.transcript_qa_core.consistency import run_cross_artifact_consistency
from dual_pipeline.transcript_qa_core.gate2_grounding import check_gate_2_15
from dual_pipeline.transcript_qa_core.recap_action_handoff_healer import (
    reconcile_action_ledger_handoff_owners,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = REPO_ROOT / "output/partner_alliances/10-27-2026/Weekly_PartnerAdvisory_Gcp_Alignment"


def test_gate_2_15_rejects_counterfactual_dan_okray_technical_attribution_and_passes_healed() -> None:
    """Counterfactual Output 02 attributing HR technical details to Drew Oakley fails Gate 2.15; healed passes."""
    f1_path = next(WEEKLY_DIR.glob("01_*_hitl_resolved_transcript.md"))
    f2_path = next(WEEKLY_DIR.glob("02_*_executive_recap.md"))
    f1_text = f1_path.read_text(encoding="utf-8")
    f2_unhealed = f2_path.read_text(encoding="utf-8")

    passed_unhealed, fails_unhealed, _ = check_gate_2_15(
        f2_unhealed, metadata={"output_1": f1_text}
    )
    assert not passed_unhealed
    assert any("GATE_2_15_ACTION_OWNER_HANDOFF_DRIFT" in f for f in fails_unhealed)

    f2_healed = reconcile_action_ledger_handoff_owners(f2_unhealed, output_1=f1_text)
    passed_healed, fails_healed, _ = check_gate_2_15(
        f2_healed, metadata={"output_1": f1_text}
    )
    assert passed_healed, f"Expected healed Output 02 to pass Gate 2.15, got: {fails_healed}"


def test_gate_cross_3_rejects_unassigned_missing_locator_or_handoff_mismatch() -> None:
    """Gate Cross.3 checks # Action & Governance Ledger for TBD owners, missing locators, and handoff drift."""
    f1_path = next(WEEKLY_DIR.glob("01_*_hitl_resolved_transcript.md"))
    f2_path = next(WEEKLY_DIR.glob("02_*_executive_recap.md"))
    f3_path = next(WEEKLY_DIR.glob("03_*_canonical_signal_projection.json"))
    f1_text = f1_path.read_text(encoding="utf-8")
    f2_unhealed = f2_path.read_text(encoding="utf-8")
    f3_text = f3_path.read_text(encoding="utf-8")

    res_unhealed = run_cross_artifact_consistency(
        {"passed": True},
        {"passed": True},
        {"passed": True},
        f1_text=f1_text,
        f2_text=f2_unhealed,
        f3_text=f3_text,
        metadata={"date": "10/27/2026"},
    )
    assert not res_unhealed["gates"]["Gate_Cross_3_Action_Item_Coverage"]["passed"]
    assert any("Action Handoff Mismatch" in f for f in res_unhealed["failures"])

    f2_healed = reconcile_action_ledger_handoff_owners(f2_unhealed, output_1=f1_text)
    res_healed = run_cross_artifact_consistency(
        {"passed": True},
        {"passed": True},
        {"passed": True},
        f1_text=f1_text,
        f2_text=f2_healed,
        f3_text=f3_text,
        metadata={"date": "10/27/2026"},
    )
    assert res_healed["gates"]["Gate_Cross_3_Action_Item_Coverage"]["passed"]

    # Counterfactual missing locator in Action & Governance Ledger
    f2_missing_loc = f2_healed.replace("[00:12:24–00:14:06]", "TBD")
    res_missing_loc = run_cross_artifact_consistency(
        {"passed": True},
        {"passed": True},
        {"passed": True},
        f1_text=f1_text,
        f2_text=f2_missing_loc,
        f3_text=f3_text,
        metadata={"date": "10/27/2026"},
    )
    assert not res_missing_loc["gates"]["Gate_Cross_3_Action_Item_Coverage"]["passed"]


def test_gate_x3_rejects_unmapped_action_dri_or_missing_locator() -> None:
    """Exit Gate X3 flags unmapped Action Ledger DRI or missing evidence locators."""
    f1_path = next(WEEKLY_DIR.glob("01_*_hitl_resolved_transcript.md"))
    f2_path = next(WEEKLY_DIR.glob("02_*_executive_recap.md"))
    f3_path = next(WEEKLY_DIR.glob("03_*_canonical_signal_projection.json"))
    f1_text = f1_path.read_text(encoding="utf-8")
    f2_healed = reconcile_action_ledger_handoff_owners(f2_path.read_text(encoding="utf-8"), output_1=f1_text)
    f3_data = json.loads(f3_path.read_text(encoding="utf-8"))

    ok_compliant, errs_compliant = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text, f2_healed, f3_data
    )
    assert ok_compliant, f"Expected compliant X3 pass, got: {errs_compliant}"

    # Counterfactual: replace Amit Ayer with an unmapped owner and empty locator
    f2_bad = f2_healed.replace("| Amit Ayer | Q4 2026 | [00:21:13–00:23:17] |", "| Ghost Owner | Q4 2026 | TBD |")
    ok_bad, errs_bad = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text, f2_bad, f3_data
    )
    assert not ok_bad
    assert any("X3 Action Item DRI Parity" in e for e in errs_bad)
    assert any("X3 Action Item Locator Parity" in e for e in errs_bad)
