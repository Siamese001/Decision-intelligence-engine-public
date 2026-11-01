"""Wave 2 unit tests: Output 02 to Output 03 & intake_package.json action porting and evidence binding."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.statement_filter import _filter_turn_dialogue
from dual_pipeline.transcript_qa_core.action_ledger_porter import port_output02_actions_to_output03
from tools.prepare_evidence_envelope_intake import parse_output02_action_ledger, prepare_package

REPO_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = REPO_ROOT / "output/partner_alliances/10-27-2026/Weekly_PartnerAdvisory_Gcp_Alignment"
MONTHLY_DIR = REPO_ROOT / "output/partner_alliances/10-27-2026/Google_Nee_PartnerAdvisory_Monthly"


def test_statement_filter_preserves_chat_share_and_technical_handoff_turns() -> None:
    """Verify statement_filter preserves [00:13:17] chat-share and [00:13:57-00:14:06] handoff turns."""
    amit_turn = (
        "Yeah, I just put in a high-level summary, Dan, if you want to just look at it. "
        "I just sent it in the chat. So, it takes the commercial terms out. "
        "It's just the activities and outcomes that we have."
    )
    dialogue, score, _ = _filter_turn_dialogue(amit_turn)
    assert score > 0
    assert "sent it in the chat" in dialogue
    assert "commercial terms" in dialogue

    robson_turn = (
        "Dan, do you think you can send the technical details behind that over email? "
        "I want to run this by engineer Amit. We can figure out how we can collaborate."
    )
    r_dialogue, r_score, _ = _filter_turn_dialogue(robson_turn)
    assert r_score > 0
    assert "technical details" in r_dialogue


def test_port_output02_actions_to_output03_injects_evidence_and_reconciles_owner() -> None:
    """Verify port_output02_actions_to_output03 binds Action #2 to Amit Ayer with verbatim Output 01 evidence."""
    f1_path = next(WEEKLY_DIR.glob("01_*_hitl_resolved_transcript.md"))
    f2_path = next(WEEKLY_DIR.glob("02_*_executive_recap.md"))
    f3_path = next(WEEKLY_DIR.glob("03_*_canonical_signal_projection.json"))

    f1_text = f1_path.read_text(encoding="utf-8")
    f2_text = f2_path.read_text(encoding="utf-8")
    f3_dict = json.loads(f3_path.read_text(encoding="utf-8"))

    ported = port_output02_actions_to_output03(
        f2_text, f3_dict, f1_text=f1_text, preserve_action_updates=True
    )
    action_updates = ported["graph_mutations"]["action_updates"]
    assert len(action_updates) == 7

    act_2 = action_updates[1]
    assert act_2["owner"] == "Amit Ayer"
    assert act_2["owner_id"] == "person:amit-ayer"
    assert act_2["evidence_ids"], "Expected non-empty evidence_ids for Action #2"

    ev_index = ported["evidence_index"]
    ev_texts = [ev_index[eid]["text"] for eid in act_2["evidence_ids"] if eid in ev_index]
    assert any("I just sent it in the chat" in t for t in ev_texts)


def test_prepare_package_weekly_partner_gcp_alignment_ports_all_7_actions_without_turn1_fallback(
    tmp_path: Path,
) -> None:
    """Verify prepare_package on Weekly_PartnerAdvisory_Gcp_Alignment resolves exact timestamps and action-evidence-ids."""
    sandbox = tmp_path / "Weekly_PartnerAdvisory_Gcp_Alignment"
    sandbox.mkdir(parents=True, exist_ok=True)
    for src_file in WEEKLY_DIR.iterdir():
        if src_file.is_file() and src_file.name != "intake_package.json":
            shutil.copy2(src_file, sandbox / src_file.name)

    f1_path = next(sandbox.glob("01_*_hitl_resolved_transcript.md"))
    f3_path = next(sandbox.glob("03_*_canonical_signal_projection.json"))
    out_pkg_path = sandbox / "intake_package.json"

    pkg = prepare_package(
        draft_path=f3_path,
        source_path=f1_path,
        output_path=out_pkg_path,
        source_location="output/partner_alliances/10-27-2026/Weekly_PartnerAdvisory_Gcp_Alignment/" + f1_path.name,
        classification="restricted",
        audiences=["internal_consulting", "nee-engineering"],
        overwrite=True,
    )

    actions = pkg["actions"]
    assert len(actions) == 7
    assert all(a["locator"]["value"] not in ("00:00:00", "00:00:32") for a in actions)

    act_2 = actions[1]
    assert act_2["reported_owner"] == "Amit Ayer"
    assert act_2["locator"]["value"] == "00:13:17"

    action_ev_map = pkg["extensions"]["com.partner_advisory.nee.action-evidence-ids"]
    assert len(action_ev_map) == 7
    assert all(len(eids) >= 1 for eids in action_ev_map.values())


def test_parse_output02_action_ledger_monthly_sync_resolves_non_zero_locators() -> None:
    """Verify Google_Nee_PartnerAdvisory_Monthly Action Ledger rows resolve to their true timestamps rather than 00:00:00."""
    f1_path = next(MONTHLY_DIR.glob("01_*_hitl_resolved_transcript.md"))
    f2_path = next(MONTHLY_DIR.glob("02_*_executive_recap.md"))
    actions = parse_output02_action_ledger(f2_path.read_text(encoding="utf-8"), source_path=f1_path)
    assert len(actions) == 7
    assert all(a["locator"]["value"] != "00:00:00" for a in actions)
