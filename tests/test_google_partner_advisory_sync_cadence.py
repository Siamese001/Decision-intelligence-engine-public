"""Unit and counterfactual tests for Google x PartnerAdvisory Weekly Sync Cadence, Layman Judge Gate & 12-Flaw System Learning."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.query_router_core.project_router import determine_query_project
from tools.google_partner_advisory_sync_core import compute_directive_effective_weight, resolve_mirror_ledger_path
from tools.validate_google_partner_advisory_sync_agenda import (
    evaluate_layman_linguistics_gate,
    load_cadence_ledger,
    main as cli_main,
    record_operator_feedback,
    validate_agenda_text,
)

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = WORKTREE_ROOT / "state" / "google_partner_advisory_sync_cadence_ledger.json"
AGENDA_PATH = WORKTREE_ROOT / "reports" / "google_partner_advisory_weekly_sync_agenda.md"


def test_compliant_cadence_agenda_passes() -> None:
    """Validate the real reports/google_partner_advisory_weekly_sync_agenda.md file on disk (Flaw 12 fix)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    ok, errors, metrics = validate_agenda_text(agenda_text, ledger)
    assert ok is True, f"Expected real agenda file to pass, got errors: {errors}"
    assert metrics["max_agenda_words"] == 800
    assert metrics["agenda_word_count"] <= metrics["max_agenda_words"]
    assert "**Completed by Amit Ayer Oct 31**" in agenda_text
    assert metrics["layman_readability_score"] >= 85
    assert metrics["layman_judge_report"]["subscores"]["non_redundancy"] == 100
    assert metrics["top_bullets"] >= 1
    assert metrics["sub_bullets"] >= 1
    assert metrics["trailing_owner_subbullets"] == metrics["sub_bullets"]
    assert metrics["has_lineage_footer"] is True


def test_retracted_specious_delay_claims_blocked() -> None:
    """Ensure 50-to-60-day, 50-day, 40-day, 2-to-3-day, setup delay, and meta-filler variants fail closed (Flaws 2 & 3)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    for specious_snippet in (
        "cut cloud provisioning from 50 to 60 days down to 2 to 3 days",
        "avoid a 50-to-60-day cloud setup delay",
        "replace 40-day provisioning",
        "review with Google's internal HR engineer (Amit)",
        "run this by engineer Amit at Google",
        "lock target dates and owners for every open item above",
    ):
        tainted = agenda_text.replace(
            "automated firewall and security-intake workflows",
            specious_snippet,
        )
        ok, errors, _ = validate_agenda_text(tainted, ledger)
        assert ok is False, f"Expected specious snippet '{specious_snippet}' to fail"
        assert any("RETRACTED_OR_SPECIOUS_CLAIM_DETECTED" in e for e in errors)


def test_completed_marker_and_open_action_items_enforced() -> None:
    """Ensure COMPLETED items require bold marker and OPEN items cannot be dropped (Flaw 4)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")

    missing_bold_marker = agenda_text.replace("**Completed by Amit Ayer Oct 31**", "")
    ok, errors, _ = validate_agenda_text(missing_bold_marker, ledger)
    assert ok is False
    assert any("MISSING_COMPLETED_ACTION_MARKER" in e for e in errors)

    missing_robson_hr = "\n".join(
        ln for ln in agenda_text.splitlines() if "16-week backend SAP position" not in ln
    )
    ok2, errors2, _ = validate_agenda_text(missing_robson_hr, ledger)
    assert ok2 is False
    assert any("MISSING_OPEN_ACTION_ITEM_COVERAGE" in e for e in errors2)


def test_vague_subbullet_fails_specificity_and_judge_gate() -> None:
    """Ensure vague 11-word sub-bullets fail both INSUFFICIENT_SUBBULLET_DETAIL and the judge score (Flaws 5 & 9)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    vague_agenda = agenda_text.replace(
        "Review those HR deliverables with Google's HR engineering team and project contacts to map out two concrete handoffs: how Google's AI platform plugs into PartnerAdvisory's initial rollout plan to orchestrate requests across the employee portal and Moveworks, and how future Google HR assistants can reuse the 16-week backend SAP position and requisition pipeline without building duplicate connectors (Robson Silva, Craig Finch).",
        "Connect with Google's HR contacts to align where the workstreams link (Robson Silva, Craig Finch).",
    )
    ok, errors, metrics = validate_agenda_text(vague_agenda, ledger)
    assert ok is False
    assert any("INSUFFICIENT_SUBBULLET_DETAIL" in e for e in errors)
    assert metrics["layman_judge_report"]["subscores"]["layman_business_clarity"] < 85


def test_query_router_routes_partner_sync_with_sap_to_partner_alliances() -> None:
    """Ensure Stage 1 nee_query mentioning Google, PartnerAdvisory, SAP, and Salesforce routes to partner_alliances (Flaw 6)."""
    q2 = (
        "What is the current status of Google PartnerAdvisory partner alignment on March funding validation, "
        "HR workflow assistant, reusable BigQuery SAP Salesforce data hub, solar and battery development "
        "dashboard Q4 deadline, infrastructure automation cloud provisioning, and transmission command center Amazon incumbency?"
    )
    assert determine_query_project(q2) == "partner_alliances"


def test_temporal_decay_and_robson_directive_weighting() -> None:
    """Ensure W_effective(dt) = M_partner * exp(-0.05 * dt) is computed and Robson's PSD-20261027-02 is enforced (Flaw 7)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    directives = ledger["partner_strategic_directives"]
    w_oct31 = compute_directive_effective_weight(directives[0], reference_date="2026-10-31")
    w_stale = compute_directive_effective_weight(directives[0], reference_date="2026-12-15")
    assert 1.20 <= w_oct31 <= 1.25
    assert w_stale < 0.20

    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    no_robson_data_hub = agenda_text.replace(
        "Anchor that first HR assistant on one shared Google data hub connected to SAP and Salesforce so every future business assistant plugs into the same foundation without starting over (Robson Silva, Amit Ayer).",
        "Review general partnership scheduling logistics and calendar invites for the upcoming holiday quarter across teams (Amit Ayer, Drew Oakley).",
    ).replace("SAP", "system").replace("Robson Silva", "Craig Finch")
    ok, errors, _ = validate_agenda_text(no_robson_data_hub, ledger)
    assert ok is False
    assert any("MISSING_ROBSON_STRATEGIC_INSIGHT" in e for e in errors)


def test_cross_bullet_repetition_fails_gate_and_llm_judge() -> None:
    """Ensure repeated workstreams and inverted word order ('funding before March' vs 'March funding') fail closed (Flaw 8)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    repetitive_agenda = agenda_text.replace(
        "  - Review joint opportunities around migrating and modernizing the CGI Grid 360 power delivery asset-management platform on Google Cloud (Drew Oakley, Rusty Gattmann, Bryan Armalavage).",
        "  - Review joint opportunities around migrating and modernizing the CGI Grid 360 power delivery asset-management platform on Google Cloud and set up the cloud environment for the Solar and Battery dashboard to validate March funding (Drew Oakley, Rusty Gattmann, Bryan Armalavage).",
    )
    ok, errors, metrics = validate_agenda_text(repetitive_agenda, ledger)
    assert ok is False
    assert any("CROSS_BULLET_REPETITION_DETECTED" in e for e in errors)
    assert any("LAYMAN_JUDGE_SCORE_BELOW_THRESHOLD" in e for e in errors)
    assert metrics["layman_readability_score"] < 85
    assert metrics["layman_judge_report"]["subscores"]["non_redundancy"] < 50


def test_offboarded_personnel_and_confidentiality_quarantine() -> None:
    """Ensure dollar figures ($6.4M, $60K, <$500K), internal strategy terms, and offboarded aliases fail closed (Flaw 11)."""
    ledger = load_cadence_ledger(LEDGER_PATH)
    assert ledger["retrieval_protocol"]["invariant_id"] == "INV-RETRIEVAL-HYBRID-FIRST"
    assert "Databricks (Power Generation Division operational store)" in ledger["cross_platform_data_hub"]
    assert ledger["workstream_lifecycle_states"]["Nuclear Business Gemini SOW"] == "NEGOTIATION_STAGE_5_STAFFING_RESOLVED"
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")

    priced_agenda = agenda_text.replace(
        "Infrastructure Automation kickoff",
        "Infrastructure Automation ($6.4M) kickoff with $60K rebate and 18-month loss leader",
    )
    ok_price, errors_price, _ = validate_agenda_text(priced_agenda, ledger)
    assert ok_price is False
    assert any("COMMERCIAL_PRICING_LEAK_DETECTED" in e for e in errors_price)
    assert any("INTERNAL_CONFIDENTIALITY_LEAK" in e for e in errors_price)

    alias_agenda = agenda_text.replace("(Drew Oakley, Amit Ayer", "(Amanda, Jen, Drew Oakley, Amit Ayer")
    ok_alias, errors_alias, _ = validate_agenda_text(alias_agenda, ledger)
    assert ok_alias is False
    assert any("QUARANTINED_OFFBOARDED_PERSONNEL_DETECTED" in e for e in errors_alias)


def test_closed_loop_feedback_recording_persists_in_sandbox(tmp_path: Path) -> None:
    """Verify feedback writes to HITLSQLiteStore, learning.sqlite, and out-of-tree mirror, surviving git reset (Flaws 1, 2 & 10)."""
    sandbox_ledger = tmp_path / "cadence_ledger.json"
    sandbox_ledger.write_text(LEDGER_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    updated = record_operator_feedback(
        sandbox_ledger,
        "Keep agenda strictly under 180 words and highlight March funding gate.",
        applied_rules=["MAX_WORDS_180", "HIGHLIGHT_MARCH_GATE"],
        max_words=180,
    )
    assert updated["learned_operator_constraints"]["max_agenda_words"] == 180

    rc = cli_main(
        [
            "--ledger",
            str(sandbox_ledger),
            "--record-feedback",
            "Retract 75-day delay rumor and cap words at 420.",
            "--max-words",
            "420",
            "--retracted-claim",
            "75-day delay",
            "--complete-action",
            "ACT-20261027-01",
            "--completed-by",
            "Alex Brannan",
            "--applied-rule",
            "RETRACT_75_DAY_RUMOR",
        ]
    )
    assert rc == 0

    mirror_path = resolve_mirror_ledger_path(sandbox_ledger)
    assert mirror_path.is_file()
    hitl_sqlite = mirror_path.parent / "hitl_decision_ledger.sqlite"
    learning_sqlite = mirror_path.parent / "learning.sqlite"
    assert hitl_sqlite.is_file()
    assert learning_sqlite.is_file()

    with sqlite3.connect(str(hitl_sqlite)) as conn:
        hitl_count = conn.execute(
            "SELECT COUNT(*) FROM hitl_decisions WHERE category = 'automation.google_partner_advisory_weekly_sync'"
        ).fetchone()[0]
    assert hitl_count >= 1

    with sqlite3.connect(str(learning_sqlite)) as conn:
        learn_count = conn.execute(
            "SELECT COUNT(*) FROM learning_event WHERE stage = 'LP3'"
        ).fetchone()[0]
    assert learn_count >= 1

    # Simulate git checkout / branch reset overwriting the in-tree ledger with stale state
    sandbox_ledger.write_text(LEDGER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    recovered = load_cadence_ledger(sandbox_ledger)
    assert recovered["learned_operator_constraints"]["max_agenda_words"] == 420
    assert "75-day delay" in recovered["retracted_or_specious_claims_quarantine"]
    act_01 = next(a for a in recovered["open_action_items"] if a["action_id"] == "ACT-20261027-01")
    assert act_01["status"] == "COMPLETED"


def test_host_launch_adapter_dispatches_google_partner_advisory_weekly_sync(tmp_path: Path) -> None:
    """Verify AntigravityHostLaunchAdapter dispatches google_partner_advisory_weekly_sync and writes learning_feedback_receipt.json (Flaw 1)."""
    from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
    from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter

    adapter = AntigravityHostLaunchAdapter(repo_root=WORKTREE_ROOT)
    result = adapter.launch_task(
        task_id="google_partner_advisory_weekly_sync",
        mode=ExecutionMode.INTERACTIVE,
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        fixture_root=tmp_path,
        run_id="test-google-partner_advisory-sync-run",
    )
    assert result.terminal_outcome == "COMPLETE"
    assert result.findings_count >= 1
    assert "learning_feedback_loop" in result.metadata
    assert result.metadata["learning_feedback_loop"]["feedback_recorded"] is True


def test_counterfactual_over_200_words_or_missing_continuity_fails() -> None:
    ledger = load_cadence_ledger(LEDGER_PATH)
    siloed_long_agenda = "# Weekly Sync\n" + ("This is a long prose paragraph without bullets or prior action items. " * 75)
    ok, errors, metrics = validate_agenda_text(siloed_long_agenda, ledger)
    assert ok is False
    assert metrics["agenda_word_count"] > 800
    assert any("AGENDA_WORD_LIMIT_EXCEEDED" in e for e in errors)
    assert any("BULLET_SUBBULLET_STRUCTURE_REQUIRED" in e for e in errors)
    assert any("MISSING_PRIOR_ACTION_CONTINUITY" in e for e in errors)
    assert any("MISSING_ALEX_STRATEGIC_INSIGHT" in e for e in errors)
    assert any("MISSING_RUSTY_STRATEGIC_INSIGHT" in e for e in errors)


def test_layman_linguistics_cheap_judge_gate_blocks_technical_jargon() -> None:
    ledger = load_cadence_ledger(LEDGER_PATH)
    jargon_agenda = (
        "# Weekly Sync\n"
        "- 1. Prior Action Tracker\n"
        "  - Deploy ENTERPRISE4U on G EAP / JEEP with Negotiation 5, "
        "WON_MOBILIZING Terraform landing zone, Identity-Aware Proxy (Entra ID), ArcGIS, Primavera P6, "
        "AI-DLC on Cloud Run, VPC skill agents in SuccessFactors and S/4HANA hub-and-spoke vs AWS Kiro before March SOW rebate (Alex, Rusty, Robson, Craig, Dan).\n"
    )
    ok, errors, metrics = validate_agenda_text(jargon_agenda, ledger)
    assert ok is False
    assert metrics["layman_readability_score"] < 85
    assert any("LAYMAN_JARGON_DETECTED" in e for e in errors)
    assert any("LAYMAN_JUDGE_SCORE_BELOW_THRESHOLD" in e for e in errors)

    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    gate_ok, score, report, _ = evaluate_layman_linguistics_gate(agenda_text, min_score=85, ledger=ledger)
    assert gate_ok is True
    assert score >= 85
    assert report["judge_tier"] == "FAST_ROUTING"
    assert "stage_b_llm_verdict" in report
    assert report["stage_b_llm_verdict"]["passed"] is True


def test_trailing_owner_format_and_partner_only_attendees() -> None:
    ledger = load_cadence_ledger(LEDGER_PATH)
    agenda_text = AGENDA_PATH.read_text(encoding="utf-8")
    leading_and_client_agenda = agenda_text.replace(
        "Confirm if Google accepts an alternative to a signed contract to approve partner funding before the March cutoff, focusing first on a scoped employee workflow assistant that finishes on time (Alex Brannan, Anya Gayvoronskaya).",
        "Alex Brannan & Stefanos Georgiades: Schedule the Legal session and confirm if Google accepts an alternative before March.",
    )
    ok, errors, _ = validate_agenda_text(leading_and_client_agenda, ledger)
    assert ok is False
    assert any("TRAILING_OWNER_FORMAT_REQUIRED" in e for e in errors)
    assert any("CLIENT_ATTENDEE_OR_CROSS_MEETING_LEAK" in e for e in errors)


def test_sidecar_hybrid_retrieval_linter_and_counterfactual(tmp_path: Path) -> None:
    from tools.lint_prompt_ssot import audit_sidecar_hybrid_retrieval

    errors = audit_sidecar_hybrid_retrieval(WORKTREE_ROOT)
    assert errors == [], f"Expected zero sidecar SSOT errors, got: {errors}"

    fake_root = tmp_path / "repo"
    sidecars_dir = fake_root / "automations" / "prompts" / "sidecars"
    sidecars_dir.mkdir(parents=True)
    state_dir = fake_root / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "google_partner_advisory_sync_cadence_ledger.json").write_text(
        LEDGER_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (sidecars_dir / "google_partner_advisory_weekly_sync_agent.md").write_text(
        "WHERE src.source_name LIKE '%PartnerAdvisory Gcp Alignment%' and 50 to 60 days delay", encoding="utf-8"
    )
    (sidecars_dir / "nea_charter_portfolio_roster_agent.md").write_text(
        "WHERE s.source_name = 'Document 20261020 Nea Q4 Planning Assignments'", encoding="utf-8"
    )
    cf_errors = audit_sidecar_hybrid_retrieval(fake_root)
    assert any("prohibited brittle SQL source filter" in e for e in cf_errors)
    assert any("missing required hybrid retrieval" in e for e in cf_errors)
    assert any("retracted/quarantined claim from ledger" in e for e in cf_errors)
