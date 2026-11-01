"""Wave 1 unit and counterfactual tests for Output 01 -> Output 02 multi-turn handoff owner distillation."""

from __future__ import annotations

from pathlib import Path

from dual_pipeline.transcript_qa_core.recap_grounding_healer import reconcile_action_ledger_handoff_owners
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_stages_core.recap_pass_2b import PASS_2B_SYSTEM_PROMPT

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_01_PATH = (
    WORKTREE_ROOT
    / "output"
    / "partner_alliances"
    / "10-27-2026"
    / "Weekly_PartnerAdvisory_Gcp_Alignment"
    / "01_Weekly_PartnerAdvisory_Gcp_Alignment_10-27-2026_0300PM_0332PM_ET_hitl_resolved_transcript.md"
)
WEEKLY_02_PATH = (
    WORKTREE_ROOT
    / "output"
    / "partner_alliances"
    / "10-27-2026"
    / "Weekly_PartnerAdvisory_Gcp_Alignment"
    / "02_Weekly_PartnerAdvisory_Gcp_Alignment_10-27-2026_0300PM_0332PM_ET_executive_recap.md"
)


def test_reconcile_action_ledger_handoff_owners_on_weekly_partner_advisory_alignment(tmp_path: Path) -> None:
    f1_text = WEEKLY_01_PATH.read_text(encoding="utf-8")
    f2_text = WEEKLY_02_PATH.read_text(encoding="utf-8")

    changes: list[str] = []
    audit_log: list[dict] = []
    healed_f2 = reconcile_action_ledger_handoff_owners(
        f2_text, output_1=f1_text, changes=changes, audit_log=audit_log
    )

    sandbox_out = tmp_path / "healed_02_executive_recap.md"
    sandbox_out.write_text(healed_f2, encoding="utf-8")

    assert (
        "| 2 | Action | Share PartnerAdvisory’s HR-work activities, outcomes, and technical details without commercial terms. "
        "| Amit Ayer | Post-meeting follow-up | [00:12:24–00:14:06] | Proposed / Open |"
    ) in healed_f2
    assert any("Reconciled Action Ledger handoff owner 'Drew Oakley' -> 'Amit Ayer'" in c for c in changes)
    assert any(entry["rule"] == "RULE-H6-TECHNICAL-HANDOFF" for entry in audit_log)


def test_heal_output_2_recap_end_to_end_reconciles_technical_handoff() -> None:
    f1_text = WEEKLY_01_PATH.read_text(encoding="utf-8")
    f2_text = WEEKLY_02_PATH.read_text(encoding="utf-8")
    healed = heal_output_2_recap(f2_text, metadata={"meeting_date": "10/27/2026"}, output_1=f1_text)
    assert "| 2 | Action | Share PartnerAdvisory’s HR-work activities, outcomes, and technical details without commercial terms. | Amit Ayer |" in healed
    assert "[00:12:24–00:14:06]" in healed


def test_counterfactual_non_technical_action_preserves_original_owner() -> None:
    f1_text = WEEKLY_01_PATH.read_text(encoding="utf-8")
    f2_text = WEEKLY_02_PATH.read_text(encoding="utf-8")
    healed = reconcile_action_ledger_handoff_owners(f2_text, output_1=f1_text)
    # Row 1 (Infrastructure automation staffing profiles) has no technical disclaimer or chat override; stays Drew Oakley
    assert "| 1 | Action | Provide the remaining infrastructure-automation team profiles. | Drew Oakley |" in healed
    # Row 7 (Identify why transmission project is proceeding on AWS) stays Drew Oakley
    assert "| 7 | Action | Identify why the transmission project is proceeding on AWS and report back. | Drew Oakley |" in healed


def test_prompts_enforce_h6_technical_handoff_and_in_call_chat_fulfillment() -> None:
    v57 = (WORKTREE_ROOT / "config" / "prompts" / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md").read_text(
        encoding="utf-8"
    )
    stage02 = (WORKTREE_ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md").read_text(
        encoding="utf-8"
    )
    assert "sent it in the chat" in v57
    assert "our work is not technical" in v57
    assert "sent it in the chat" in stage02
    assert "not technical" in stage02
    assert "sent it in the chat" in PASS_2B_SYSTEM_PROMPT
