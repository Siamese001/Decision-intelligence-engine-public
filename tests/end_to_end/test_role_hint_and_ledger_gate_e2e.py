"""
tests/end_to_end/test_role_hint_and_ledger_gate_e2e.py

End-to-end verification of Wave 1 (Live Role-Hint Persistence, Ledger-Bound Role Gate,
and BCG Managing Director Enterprise Directory Priors).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.u0_hint_reconciler import (
    extract_manifest_roles,
    extract_role_hints_from_records,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    hydrate_live_container_artifacts,
)
from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from tools.check_ask_user_question import check_hook_payload


def test_bcg_managing_director_directory_priors_e2e(tmp_path: Path) -> None:
    """Verify Will Booth and John Snipes resolve to Managing Director at Boston Consulting Group (BCG)."""
    wb = resolve_person_entity("Will Booth")
    js = resolve_person_entity("John Snipes")
    assert wb is not None
    assert wb.canonical_name == "Will Booth"
    assert "Managing Director" in wb.role
    assert "BCG" in wb.org
    assert js is not None
    assert js.canonical_name == "John Snipes"
    assert "Managing Director" in js.role
    assert "BCG" in js.org

    # Also verify hermetic directory fallback when no GraphDB is present
    wb_dir = resolve_person_entity("Will Booth", repo_root=tmp_path, runtime_root=tmp_path)
    js_dir = resolve_person_entity("John Snipes", repo_root=tmp_path, runtime_root=tmp_path)
    assert wb_dir is not None and wb_dir.role == "Managing Director"
    assert js_dir is not None and js_dir.role == "Managing Director"


def test_live_role_hint_persistence_and_u0_hydration_e2e(tmp_path: Path) -> None:
    """Verify live role hints via ChatAttributionCollector and `nee speaker-hint --role` persist and hydrate in U0."""
    meeting_dir = tmp_path / "output" / "apex_trans" / "09-28-2026" / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter"
    meeting_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = meeting_dir / "session_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "meeting": "Introduction PartnerAdvisory Bcg Mitchell Peter",
                "project": "apex_trans",
                "date": "09/28/2026",
                "time": "01:00 PM - 01:30 PM ET",
                "attendees": ["Alex Rivera", "Peter Ashley", "Will Booth", "John Snipes"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 1. ChatAttributionCollector parses role_hint
    collector = ChatAttributionCollector(
        attendees=["Alex Rivera", "Peter Ashley", "Will Booth", "John Snipes"],
        output_path=meeting_dir / "chat_attributions.json",
    )
    added = collector.record_attribution(
        "Will Booth - Managing Director, BCG; John Snipes is Managing Director at BCG",
        elapsed_seconds=42.0,
    )
    assert len(added) == 2
    assert added[0].attribution_type == "role_hint"
    assert added[0].speaker_name == "Will Booth"
    assert added[0].metadata["role"] == "Managing Director"
    assert added[1].attribution_type == "role_hint"
    assert added[1].speaker_name == "John Snipes"
    assert added[1].metadata["role"] == "Managing Director"

    # 2. CLI speaker-hint handler persists role hint to manifest and speaker_hints.json
    args = argparse.Namespace(
        speaker="Will Booth",
        role="Managing Director",
        org="Boston Consulting Group (BCG)",
        quote="",
        meeting_dir=str(meeting_dir),
        runtime_root=str(tmp_path),
        json=True,
        force=False,
        remove_attendee=None,
    )
    res = handle_speaker_hint(args)
    assert res["status"] == "PASS"
    assert res["action"] == "ROLE_APPLIED"
    assert res["role"] == "Managing Director"

    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wb_entries = [
        a for a in updated_manifest["attendees"] if isinstance(a, dict) and a.get("name") == "Will Booth"
    ]
    assert len(wb_entries) == 1
    assert wb_entries[0]["role"] == "Managing Director"

    # 3. Parenthetical attendee filter in extract_manifest_roles preserves manifest roles
    roles_extracted = extract_manifest_roles(
        "Alex Rivera (Technology Lead, APEX_TRANS), Will Booth (BCG), John Snipes (BCG)",
        updated_manifest["attendees"],
    )
    assert roles_extracted.get("Will Booth") == "Managing Director"

    # 4. U0 hydration loads role hints from both manifest and chat_attributions.json
    meta: Dict[str, Any] = {
        "attendees": "Alex Rivera (Technology Lead, APEX_TRANS), Will Booth (BCG), John Snipes (BCG)",
    }
    hydrate_live_container_artifacts(meta, input_path=meeting_dir)
    assert meta["pre_enrichment_roles"].get("Will Booth") == "Managing Director"
    assert meta["pre_enrichment_roles"].get("John Snipes") == "Managing Director"


def test_ledger_backed_role_gate_and_ask_question_exemption_e2e(tmp_path: Path) -> None:
    """Verify authentic ledger role resolution passes strict C0.5/C0.6 gates and role prompts pass hook validation."""
    meta: Dict[str, Any] = {
        "attendees": "Alex UnknownPerson (Client)",
        "require_ledger_role_receipt": True,
        "repo_root": str(tmp_path),
    }
    receipt = apply_hitl_role_resolution(
        meta,
        "Alex UnknownPerson",
        "Managing Director",
        repo_root=tmp_path,
        is_human_confirmed=True,
        provenance="HUMAN_OPERATOR",
        operator_origin={"source": "human_operator", "text": "Confirmed Managing Director"},
    )
    assert receipt["is_human_confirmed"] is True
    assert receipt["ledger_entry_id"] is not None

    c05_ctx = {"metadata": meta, "repo_root": tmp_path}
    C05TitleCompletenessGate(repo_root=tmp_path).execute(c05_ctx)
    assert c05_ctx["c05_title_gate_cleared"] is True

    c06_ctx = {"metadata": meta, "repo_root": tmp_path}
    C06GraphDBRoleVerificationGate(repo_root=tmp_path).execute(c06_ctx)
    assert c06_ctx["c06_role_gate_cleared"] is True

    # Verify check_ask_user_question allows role_clarification even when spread > 20%
    hook_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": "Attendee Will Booth lacks a verified project role in the directory. What is Will Booth's verified project role?",
                        "category": "role_clarification",
                        "options": [
                            "(Recommended) Managing Director at BCG (Confidence: HIGH (85%)) - Confirmed from operator meeting hint.",
                            "Consultant at BCG (Confidence: LOW (40%)) - Fallback generic consulting role.",
                        ],
                    }
                ]
            },
        }
    }
    decision = check_hook_payload(json.dumps(hook_payload))
    assert decision.get("decision") == "allow"
