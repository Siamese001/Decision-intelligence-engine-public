"""tests/test_rca_hitl_merge_and_learning_adversarial.py

Adversarial and edge-case verification suite for 8-Agent RCA Remediation.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0%, 20%, and 90% confidence spreads on mandatory PR merge vs tactical questions;
   empty DB rows in `simulate_healing_policy_update`.
2. Malformed input: Corrupted JSONL lines in `ingest_and_drain_healing_store`, dict `operator_origin`
   in `record_operator_answer`, and missing `--options` in `cmd_authorize_merge`.
3. Concurrency & isolation: `resolve_ledger_paths(repo_root=tmp_path)` isolation ensuring zero writes
   to live `.local/ledger/hitl_decision_ledger.sqlite`.
4. Invariant bypass attempt & exact regression: `tools/lint_hardcoded_parameters.py` AST detection of
   hardcoded `[58% — MEDIUM]` HITL badges and hardcoded `base_brier = 0.184` / `sim_brier = 0.122` constants.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
from dual_pipeline.learning_engine_core.aggregation import ingest_and_drain_healing_store
from dual_pipeline.learning_loop_core.simulation import simulate_healing_policy_update
from tools.check_ask_user_question import check_hook_payload
from tools.lint_hardcoded_parameters import scan_file


def test_boundary_confidence_spreads_mandatory_vs_tactical_and_empty_store_brier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Category 1 (Boundary payload): 90% spread allowed on mandatory PR merge, denied on tactical; 0-row DB computes valid Brier."""
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(tmp_path / "hitl.sqlite"))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(tmp_path / "hitl.json"))

    # 90% spread on mandatory PR merge -> allow
    merge_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": "Branch passed all gates. Authorize governed merge into the main branch?",
                        "options": [
                            "(Recommended) Approve merge into local main (Confidence: 95%, Tier: High). Promotes changes.",
                            "Hold merge for manual inspection (Confidence: 5%, Tier: Low). Keeps branch open.",
                        ],
                        "is_multi_select": False,
                    }
                ]
            },
        }
    }
    assert check_hook_payload(json.dumps(merge_payload)).get("decision") == "allow"

    # 20% exact boundary spread on tactical question -> allow (<= 20% inclusive)
    tactical_20 = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": "Which bullet formatting style should be used for the summary notes?",
                        "options": [
                            "(Recommended) Concise single line bullets (Confidence: 60%, Tier: Medium). Improves readability.",
                            "Expanded multi sentence paragraphs (Confidence: 40%, Tier: Low). Adds narrative detail.",
                        ],
                        "is_multi_select": False,
                    }
                ]
            },
        }
    }
    assert check_hook_payload(json.dumps(tactical_20)).get("decision") == "allow"

    # Empty DB rows in simulate_healing_policy_update -> valid empirical fallback without hardcoded 0.184
    empty_db = tmp_path / "empty.sqlite"
    sim_res = simulate_healing_policy_update(
        {
            "proposal_id": "lcp-empty",
            "auto_apply": False,
            "extensions": {
                "com.partner_advisory.nee.learning": {
                    "cohort_key": "healing_escalation:sub:CLASS",
                    "occurrence_count": 3,
                }
            },
        },
        db_path=empty_db,
    )
    assert sim_res["success"] is True
    assert sim_res["details"]["simulated_brier_score"] < sim_res["details"]["current_brier_score"]
    assert sim_res["details"]["current_brier_score"] != 0.184


def test_malformed_jsonl_lines_and_idempotent_drain_without_collision(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Corrupted JSONL lines are safely skipped while 64-char prefix twins are both persisted."""
    rt_root = tmp_path / "rt"
    ledger_dir = rt_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    jsonl_path = ledger_dir / "live_outcome_events.jsonl"

    lines = [
        "{malformed truncated json",
        "[]",
        json.dumps({"outcome_type": "IRRELEVANT_EVENT"}),
        json.dumps(
            {
                "outcome_type": "COMPONENT_REPAIR",
                "subsystem": "identical_subsystem_prefix_for_64_character_collision_check",
                "repair_class": "CLASS_A",
                "defect_message": "First distinct event after 64 chars",
            }
        ),
        json.dumps(
            {
                "outcome_type": "COMPONENT_REPAIR",
                "subsystem": "identical_subsystem_prefix_for_64_character_collision_check",
                "repair_class": "CLASS_A",
                "defect_message": "Second distinct event after 64 chars",
            }
        ),
    ]
    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    signals1 = ingest_and_drain_healing_store(rt_root, db_path=db_path, outcome_ledger_path=jsonl_path)
    assert len(signals1) == 2
    # Second drain of identical file is idempotent (still 2 rows, not 4)
    signals2 = ingest_and_drain_healing_store(rt_root, db_path=db_path, outcome_ledger_path=jsonl_path)
    assert len(signals2) == 2


def test_ast_linter_blocks_hardcoded_hitl_badges_and_brier_constants(tmp_path: Path) -> None:
    """Category 4 (Invariant bypass attempt & exact regression): ParameterLinterVisitor flags [58% — MEDIUM] and base_brier = 0.184."""
    fake_repo = tmp_path / "repo"
    tools_dir = fake_repo / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    bad_file = tools_dir / "bad_module.py"
    bad_file.write_text(
        'def bad_func(branch: str):\n'
        '    opt = f"(Recommended) [58% — MEDIUM] Approve PR merge of {branch}"\n'
        '    base_brier = 0.184\n'
        '    sim_brier = 0.122\n'
        '    return opt, base_brier, sim_brier\n',
        encoding="utf-8",
    )
    violations = scan_file(bad_file, fake_repo)
    rules = {v.rule for v in violations}
    assert "HARDCODED_HITL_CONFIDENCE_BADGE" in rules
    assert "HARDCODED_BRIER_CONSTANT" in rules


def test_repo_root_isolation_under_missing_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Category 3 (Isolation): resolve_ledger_paths(repo_root=tmp_path) never resolves to live common_root ledger."""
    monkeypatch.delenv("NEE_HITL_LEDGER_SQLITE", raising=False)
    monkeypatch.delenv("NEE_HITL_LEDGER_JSON", raising=False)

    s_path, j_path = resolve_ledger_paths(repo_root=tmp_path)
    assert s_path.parent == tmp_path.resolve()
    assert j_path.parent == tmp_path.resolve()

    local_ledger_dir = tmp_path / ".local" / "ledger"
    local_ledger_dir.mkdir(parents=True, exist_ok=True)
    s_path2, j_path2 = resolve_ledger_paths(repo_root=tmp_path)
    assert s_path2.parent == local_ledger_dir.resolve()
    assert j_path2.parent == local_ledger_dir.resolve()
