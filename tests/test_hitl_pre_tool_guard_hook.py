#!/usr/bin/env python3
"""tests/test_hitl_pre_tool_guard_hook.py

Tests for the PreToolUse lifecycle hook script (scripts/hitl_pre_tool_guard.py)
and validator integration (tools/check_ask_user_question.py --hook).
Mechanically verifies that tool execution is denied whenever confidence percentage
or tier is missing from interactive question options.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_ask_user_question import check_hook_payload


class HitlPreToolGuardHookTests(unittest.TestCase):
    """Test PreToolUse hook validation and mechanical denial logic."""

    def test_compliant_question_payload_allowed(self) -> None:
        """Verify that a compliant question with calibrated confidence % and tier is allowed."""
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Which database cleanup strategy should be executed?",
                            "options": [
                                "(Recommended) Safely archive stale nodes (Confidence: HIGH (92%)) - Moves inactive records to cold storage.",
                                "Retain all records indefinitely (Confidence: LOW (18%)) - Avoids any data mutation.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_screenshot_uncalibrated_payload_denied(self) -> None:
        """Verify that options lacking confidence % or tier are mechanically denied."""
        # Exact payload structure from operator screenshot
        uncalibrated_payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "How would you like to handle this worktree?",
                            "options": [
                                "(Recommended) Decommission and prune redundant feature worktree",
                                "Re-run full document ingestion to overwrite with fresh projection",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(uncalibrated_payload))
        self.assertEqual(res.get("decision"), "deny")
        reason = res.get("reason", "")
        self.assertIn("[HITL Governance Violation]", reason)
        self.assertIn("MISSING_CONFIDENCE_IN_TEXT", reason)
        self.assertIn("MISSING_TIER_IN_TEXT", reason)

    def test_bulk_questions_payload_denied(self) -> None:
        """Verify that presenting multiple questions simultaneously is rejected under the atomic invariant."""
        bulk_payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "First decision?",
                            "options": [
                                "(Recommended) Option A (Confidence: HIGH (90%)) - First choice.",
                                "Option B (Confidence: LOW (20%)) - Second choice.",
                            ],
                        },
                        {
                            "question": "Second decision?",
                            "options": [
                                "(Recommended) Option C (Confidence: HIGH (85%)) - Third choice.",
                                "Option D (Confidence: LOW (15%)) - Fourth choice.",
                            ],
                        },
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(bulk_payload))
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Atomic Presentation Violation", res.get("reason", ""))

    def test_unrelated_tool_call_allowed(self) -> None:
        """Verify that tool calls other than operator questioning are passed through immediately."""
        unrelated_payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git status"},
            }
        }
        res = check_hook_payload(json.dumps(unrelated_payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_flat_args_compatibility(self) -> None:
        """Verify that direct flat arguments format is supported and validated."""
        flat_payload = {
            "toolCall": {
                "name": "ask_user_question",
                "args": {
                    "question": "Should release activation proceed?",
                    "options": [
                        "(Recommended) Activate release (Confidence: HIGH (95%)) - Promotes release candidate.",
                        "Abort activation (Confidence: LOW (10%)) - Leaves previous release active.",
                    ],
                },
            }
        }
        res = check_hook_payload(json.dumps(flat_payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_script_subprocess_allow_execution(self) -> None:
        """Verify scripts/hitl_pre_tool_guard.py executable outputs decision allow on compliant payload."""
        hook_script = ROOT / "scripts" / "hitl_pre_tool_guard.py"
        self.assertTrue(hook_script.exists())

        valid_payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Select candidate disposition:",
                            "options": [
                                "(Recommended) Promote candidate (Confidence: HIGH (91%)) - Passes all verification gates.",
                                "Quarantine candidate (Confidence: LOW (15%)) - Fails to establish empirical proof.",
                            ],
                        }
                    ]
                },
            }
        }
        proc = subprocess.run(
            [sys.executable, str(hook_script)],
            input=json.dumps(valid_payload),
            text=True,
            capture_output=True,
            cwd=str(ROOT),
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out.get("decision"), "allow")

    def test_script_subprocess_deny_execution(self) -> None:
        """Verify scripts/hitl_pre_tool_guard.py executable outputs decision deny on uncalibrated options."""
        hook_script = ROOT / "scripts" / "hitl_pre_tool_guard.py"
        uncalibrated_payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Select candidate disposition:",
                            "options": [
                                "(Recommended) Promote candidate without confidence score",
                                "Quarantine candidate without confidence score",
                            ],
                        }
                    ]
                },
            }
        }
        proc = subprocess.run(
            [sys.executable, str(hook_script)],
            input=json.dumps(uncalibrated_payload),
            text=True,
            capture_output=True,
            cwd=str(ROOT),
            timeout=10,
        )
        self.assertIn(proc.returncode, (0, 2))
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("MISSING_CONFIDENCE_IN_TEXT", out.get("reason", ""))

    def test_hooks_json_configuration(self) -> None:
        """Verify that .agents/hooks.json wires scripts/hitl_pre_tool_guard.py."""
        hooks_path = ROOT / ".agents" / "hooks.json"
        self.assertTrue(hooks_path.exists())
        data = json.loads(hooks_path.read_text(encoding="utf-8"))

        # Verify top-level Antigravity hook definition
        self.assertIn("nee-hitl-atomic-guard", data)
        guard_hook = data["nee-hitl-atomic-guard"]
        self.assertIn("PreToolUse", guard_hook)
        pre_tool = guard_hook["PreToolUse"][0]
        self.assertIn("ask_question", pre_tool["matcher"])
        self.assertIn("ask_user_question", pre_tool["matcher"])
        self.assertIn("scripts/hitl_pre_tool_guard.py", pre_tool["hooks"][0]["command"])

        # Verify hooks array entry
        hooks_array = data.get("hooks", [])
        entry = next((h for h in hooks_array if h.get("id") == "nee-hitl-atomic-guard"), None)
        self.assertIsNotNone(entry)
        self.assertIn("scripts/hitl_pre_tool_guard.py", entry.get("command", ""))

        post_inv = next((h for h in hooks_array if h.get("event") == "post_invocation"), None)
        self.assertIsNotNone(post_inv)
        self.assertIn("scripts/hitl_post_invocation_guard.py", post_inv.get("command", ""))

    def test_post_invocation_guard_intercepts_disjunctive_prose_without_question_tool(self) -> None:
        """Verify exact production regression string triggers injectSteps when ask_question was omitted."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run live replay"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": (
                        "Ready for next direction: we can address the specific transcript rubric notes "
                        "for Output 01, or proceed with PR creation and governance review."
                    ),
                    "tool_calls": [],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(payload))
        inject_steps = out.get("injectSteps", [])
        self.assertEqual(len(inject_steps), 1)
        msg_content = inject_steps[0].get("userMessage") or inject_steps[0].get("content") or ""
        self.assertIn("INV-DECISIVE-RECOMMENDATION", msg_content)
        self.assertIn("ask_question", msg_content)

    def test_post_invocation_guard_intercepts_unprompted_repository_edits(self) -> None:
        """Verify Step 307 reproduction: modifying files without ask_question triggers promotion prompt."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        step_307_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Debug rule errors"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "args": {
                                "Path": str(ROOT / ".agents" / "rules" / "contract-enforcement.md"),
                                "Target": "foo",
                                "Replacement": "bar",
                            },
                        }
                    ],
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": "### Root Cause & Resolution\nUpdated the frontmatter across all 13 rule files.",
                    "tool_calls": [],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(step_307_payload))
        self.assertEqual(out.get("terminationBehavior"), "force_continue")
        inject_steps = out.get("injectSteps", [])
        self.assertEqual(len(inject_steps), 1)
        msg_content = inject_steps[0].get("userMessage") or inject_steps[0].get("content") or ""
        self.assertIn("POST-EDIT PROMOTION GATE", msg_content)
        self.assertIn("ask_question", msg_content)

    def test_post_invocation_guard_allows_turn_when_ask_question_called_after_edits(self) -> None:
        """Verify turn concludes cleanly when ask_question was called after repository edits."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        compliant_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Apply rule fix"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "args": {"Path": str(ROOT / ".agents" / "rules" / "contract-enforcement.md")},
                        }
                    ],
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [{"name": "ask_question", "args": {}}],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(compliant_payload))
        self.assertEqual(out.get("injectSteps"), [])

    def test_post_invocation_guard_exempts_planning_artifact_writes(self) -> None:
        """Verify writing implementation plans or conversation brain artifacts does not trigger promotion gate."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        planning_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Create plan"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "write_file",
                            "args": {
                                "Path": str(Path.home() / ".gemini" / "antigravity" / "brain" / "test-id" / "implementation_plan.md"),
                                "Content": "# Plan",
                            },
                        }
                    ],
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": "Plan created successfully.",
                    "tool_calls": [],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(planning_payload))
        self.assertEqual(out.get("injectSteps"), [])

    def test_post_invocation_guard_allows_turn_when_question_tool_was_invoked(self) -> None:
        """Verify no injectSteps are emitted when ask_question was called in the turn."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run live replay"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": "We can either address rubric notes, or proceed with PR creation.",
                    "tool_calls": [{"name": "default_api:ask_question", "args": {}}],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(payload))
        self.assertEqual(out.get("injectSteps"), [])

    def test_post_invocation_guard_subprocess_with_transcript_file(self) -> None:
        """Verify scripts/hitl_post_invocation_guard.py reads transcriptPath JSONL and emits injectSteps."""
        import tempfile

        guard_script = ROOT / "scripts" / "hitl_post_invocation_guard.py"
        self.assertTrue(guard_script.exists())

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps({"type": "USER_INPUT", "content": "Check status"}) + "\n")
            tmp.write(
                json.dumps(
                    {
                        "type": "PLANNER_RESPONSE",
                        "content": "Ready for next direction: we can tune the prompt, or proceed with review.",
                        "tool_calls": [],
                    }
                )
                + "\n"
            )
            tmp_path = Path(tmp.name)

        try:
            proc = subprocess.run(
                [sys.executable, str(guard_script)],
                input=json.dumps({"transcriptPath": str(tmp_path)}),
                text=True,
                capture_output=True,
                cwd=str(ROOT),
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            out = json.loads(proc.stdout.strip())
            self.assertEqual(len(out.get("injectSteps", [])), 1)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_speaker_attribution_with_context_allowed(self) -> None:
        """Verify that a speaker attribution decision with -2/+2 context is allowed."""
        speaker_q = (
            "Meeting/source: test.wav\n\n"
            "Review one target speaker assignment.\n\n"
            "Transcript start: 2 earlier turn(s) unavailable at this boundary.\n\n"
            "TARGET [00:00:00] **UNRESOLVED — Speaker 1**: we have anything else outstanding we have\n\n"
            "Transcript end: 2 later turn(s) unavailable at this boundary.\n\n"
            "Who is speaking in the TARGET turn?"
        )
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": speaker_q,
                            "options": [
                                "(Recommended) Alex Rivera (Confidence: HIGH (90%)) - Primary meeting organizer.",
                                "Jordan Taylor (Confidence: LOW (10%)) - Secondary participant.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_speaker_attribution_without_context_denied(self) -> None:
        """Verify that an ad-hoc speaker attribution question lacking -2/+2 context is mechanically denied."""
        adhoc_q = (
            "Acoustic diarization identified one voice cluster (Speaker 1) saying: 'we have anything else outstanding'. "
            "Which participant should Speaker 1 be mapped to?"
        )
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": adhoc_q,
                            "options": [
                                "(Recommended) Alex Rivera (Confidence: HIGH (90%)) - Primary meeting organizer.",
                                "Jordan Taylor (Confidence: LOW (10%)) - Secondary participant.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Speaker Attribution Context Incomplete", res.get("reason", ""))

    def test_speaker_attribution_with_question_marks_in_turn_allowed(self) -> None:
        """Verify that verbatim transcript speech containing question marks does not violate atomic question checks."""
        speaker_q = (
            "Meeting/source: test.wav\n\n"
            "Review one target speaker assignment.\n\n"
            "Transcript start: 2 earlier turn(s) unavailable at this boundary.\n\n"
            "TARGET [00:00:00] **UNRESOLVED — Speaker 1**: do we have anything else outstanding?\n\n"
            "Transcript end: 2 later turn(s) unavailable at this boundary.\n\n"
            "Who is speaking in the TARGET turn?"
        )
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": speaker_q,
                            "options": [
                                "(Recommended) Alex Rivera (Confidence: HIGH (90%)) - Primary meeting organizer.",
                                "Jordan Taylor (Confidence: LOW (10%)) - Secondary participant.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_speaker_attribution_collapsed_context_rows_denied(self) -> None:
        """Verify that an attribution question where context turns are collapsed onto a single row is mechanically denied."""
        collapsed_q = (
            "Meeting/source: LENS Working Session OCR (10/16/2026) "
            "BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff "
            "BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration? "
            "TARGET [00:07:01] **UNRESOLVED — Unknown**: Oh, both. "
            "AFTER 1 [00:07:03] **UNRESOLVED — Unknown**: I mean "
            "AFTER 2 [00:07:15] Amit Ayer: What is the response time? "
            "Who is speaking in the TARGET turn?"
        )
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": collapsed_q,
                            "options": [
                                "(Recommended) Eduardo Juan More (Confidence: HIGH (90%)) - Primary speaker.",
                                "Alex Sanchez (Confidence: LOW (10%)) - Other participant.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "deny")
        self.assertIn("Speaker Attribution Context Incomplete", res.get("reason", ""))
        self.assertIn("collapsed onto the same line", res.get("reason", ""))

    def test_wave4_margin_gate_denies_wide_spread_and_records_autonomous_ledger_entry(self) -> None:
        """Verify non-Tier-0 choice with spread > 20% (90% vs 50% = 40%) is denied by PreToolUse and recorded as AUTONOMOUS."""
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            sqlite_path = tmp_root / "hitl_decision_ledger.sqlite"
            json_path = tmp_root / "hitl_decision_ledger.json"

            payload = {
                "toolCall": {
                    "name": "ask_question",
                    "args": {
                        "questions": [
                            {
                                "decision_id": "dec-tactical-90-50",
                                "category": "tactical",
                                "question": "Which summary formatting style should be applied to the meeting notes?",
                                "options": [
                                    "(Recommended) Concise bulleted executive takeaways (Confidence: HIGH (90%)) - Prioritizes rapid scanning.",
                                    "Detailed narrative paragraph summary (Confidence: MEDIUM (50%)) - Preserves extended prose context.",
                                ],
                                "is_multi_select": False,
                            }
                        ]
                    },
                }
            }
            with patch.dict(
                os.environ,
                {
                    "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                    "NEE_HITL_LEDGER_JSON": str(json_path),
                },
            ):
                res = check_hook_payload(json.dumps(payload))

            self.assertEqual(res.get("decision"), "deny")
            self.assertIn("[HITL_MARGIN_BYPASS_REQUIRED]", res.get("reason", ""))
            self.assertIn("40%", res.get("reason", ""))
            self.assertTrue(json_path.is_file(), "Autonomous decision must be exported to hitl_decision_ledger.json")
            ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
            records = ledger_data.get("decisions") or ledger_data.get("records") or []
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec["decision_id"], "dec-tactical-90-50")
            self.assertEqual(rec["operator_id"], "autonomous_engine")
            self.assertEqual(rec["metadata"]["outcome_type"], "AUTONOMOUS")
            self.assertAlmostEqual(rec["metadata"]["margin_at_decision"], 0.40, places=2)

    def test_wave4_margin_gate_allows_narrow_spread_within_20_percent(self) -> None:
        """Verify choice with spread <= 20% (58% vs 42% = 16% <= 20%) is allowed to fire ask_question."""
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Which summary formatting style should be applied to the meeting notes?",
                            "options": [
                                "(Recommended) Concise bulleted executive takeaways (Confidence: MEDIUM (58%)) - Prioritizes rapid scanning.",
                                "Detailed narrative paragraph summary (Confidence: LOW (42%)) - Preserves extended prose context.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_wave4_pr_merge_authorization_always_allowed_regardless_of_confidence_spread(self) -> None:
        """Verify PR Merge question is allowed even when confidence spread is 79% (94% vs 15% > 20%)."""
        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Should we merge this pull request into main now that all verification gates passed?",
                            "options": [
                                "(Recommended) Merge pull request into main (Confidence: HIGH (94%)) - Promotes verified branch to production mainline.",
                                "Hold pull request on feature branch (Confidence: LOW (15%)) - Defers mainline promotion for manual inspection.",
                            ],
                            "is_multi_select": False,
                        }
                    ]
                },
            }
        }
        res = check_hook_payload(json.dumps(payload))
        self.assertEqual(res.get("decision"), "allow")

    def test_wave4_pr_governance_merge_prerequisite_blocks_without_hitl_authorization(self) -> None:
        """Verify _check_merge_prerequisites blocks PR merge without human PR_MERGE_AUTHORIZATION in ledger and passes once recorded."""
        import os
        import tempfile
        from unittest.mock import patch
        from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
        from tools.pr_governance_core.lifecycle import _check_merge_prerequisites

        branch = "feat/stage01-scrubber-judge-retry"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            sqlite_path = tmp_root / ".local" / "ledger" / "hitl_decision_ledger.sqlite"
            json_path = tmp_root / ".local" / "ledger" / "hitl_decision_ledger.json"
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps({"schema_version": "1.1.0", "records": []}), encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                    "NEE_HITL_LEDGER_JSON": str(json_path),
                },
            ), patch("tools.pr_governance_core.lifecycle.run_git", side_effect=["head123", "base456"]), \
                 patch("tools.pr_governance_core.lifecycle.verify_branch_wave_completion", return_value=(True, "—", "1, 2, 3, 4")):
                # 1. Blocked when ledger has no PR_MERGE_AUTHORIZATION record for branch
                rc_blocked = _check_merge_prerequisites(
                    branch,
                    repo_root=tmp_root,
                    require_hitl_merge_auth=True,
                )
                self.assertEqual(rc_blocked, 2)

                # 2. Record authentic human operator PR_MERGE_AUTHORIZATION decision
                opts = [
                    "(Recommended) Merge pull request into main (Confidence: HIGH (94%)) - Promotes verified branch.",
                    "Hold pull request on feature branch (Confidence: LOW (15%)) - Defers promotion.",
                ]
                record_operator_answer(
                    decision_id=f"pr-merge-{branch}",
                    question=f"Should we merge pull request {branch} into main?",
                    options=opts,
                    selected_option=opts[0],
                    operator_origin="human_operator",
                    category="pr_merge_authorization",
                    metadata={"branch": branch},
                    repo_root=tmp_root,
                    sqlite_path=sqlite_path,
                    json_path=json_path,
                )

                # 3. Allowed once human PR_MERGE_AUTHORIZATION is recorded
                rc_allowed = _check_merge_prerequisites(
                    branch,
                    repo_root=tmp_root,
                    require_hitl_merge_auth=True,
                )
                self.assertEqual(rc_allowed, 0)

    def test_wave5_post_tool_use_ledger_writer_records_merkle_linked_decision(self) -> None:
        """Verify scripts/hitl_post_tool_ledger_writer.py parses PostToolUse output and appends a Merkle-linked record."""
        import os
        import tempfile
        from scripts.hitl_post_tool_ledger_writer import process_post_tool_payload

        hook_script = ROOT / "scripts" / "hitl_post_tool_ledger_writer.py"
        self.assertTrue(hook_script.exists())

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            sqlite_path = tmp_root / "hitl_decision_ledger.sqlite"
            json_path = tmp_root / "hitl_decision_ledger.json"

            opts = [
                "(Recommended) Address specific transcript rubric notes (Confidence: MEDIUM (55%)) - Resolves Output 01 formatting items.",
                "Proceed directly with pull request review (Confidence: LOW (45%)) - Defers minor rubric polish.",
            ]
            post_payload = {
                "toolCall": {
                    "name": "ask_question",
                    "args": {
                        "questions": [
                            {
                                "decision_id": "dec-rubric-vs-pr-001",
                                "question": "How should we proceed after the stage one replay completed?",
                                "options": opts,
                            }
                        ]
                    },
                },
                "toolResult": {
                    "selected": opts[0],
                },
            }

            env = {
                **os.environ,
                "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                "NEE_HITL_LEDGER_JSON": str(json_path),
            }
            proc = subprocess.run(
                [sys.executable, str(hook_script)],
                input=json.dumps(post_payload),
                text=True,
                capture_output=True,
                cwd=str(ROOT),
                env=env,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            out = json.loads(proc.stdout.strip())
            self.assertEqual(out.get("status"), "RECORDED")
            self.assertEqual(out.get("decision_id"), "dec-rubric-vs-pr-001")
            self.assertEqual(out.get("category"), "transcript_rubric")
            self.assertEqual(out.get("outcome_type"), "AFFIRMED")
            self.assertTrue(json_path.is_file())
            self.assertTrue(sqlite_path.is_file())

    def test_wave5_closed_loop_learning_sharpens_nuance_and_widens_confidence_past_20_percent(self) -> None:
        """Verify repeated human affirmations widen posterior confidence spread from <=20% to >20% so ask_question stops firing."""
        import os
        import tempfile
        from unittest.mock import patch
        from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
        from dual_pipeline.learning_engine import ClosedLoopLearningEngine
        from dual_pipeline.parameters import needs_operator_decision
        from scripts.hitl_post_tool_ledger_writer import process_post_tool_payload

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            sqlite_path = tmp_root / "hitl_decision_ledger.sqlite"
            json_path = tmp_root / "hitl_decision_ledger.json"
            learned_store_path = tmp_root / "learned_hitl_option_store.json"

            with patch.dict(
                os.environ,
                {
                    "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                    "NEE_HITL_LEDGER_JSON": str(json_path),
                    "NEE_LEARNED_HITL_OPTION_STORE": str(learned_store_path),
                },
            ):
                # 1. Initially ambiguous tactical question: 55% vs 45% (spread = 10% <= 20%)
                q_text = "Which summary layout should be used for the weekly status notes?"
                opt_a = "Concise executive bullet summary"
                opt_b = "Extended narrative prose summary"

                qb_initial = (
                    QuestionBuilder(q_text, metadata={"category": "tactical_execution"})
                    .add_option(
                        label=opt_a,
                        plain_english_summary="Formats status notes as short bullets",
                        impact_statement="Improves reading speed for reviewers",
                        confidence_score=0.55,
                        recommended=True,
                    )
                    .add_option(
                        label=opt_b,
                        plain_english_summary="Formats status notes as full paragraphs",
                        impact_statement="Preserves surrounding narrative detail",
                        confidence_score=0.45,
                        recommended=False,
                    )
                    .build()
                )
                c0_top = qb_initial.options[0]["confidence_score"]
                c0_sec = qb_initial.options[1]["confidence_score"]
                self.assertTrue(needs_operator_decision(c0_top, c0_sec))
                # PreToolUse allows firing ask_question when spread is 10% (<= 20%)
                pre_initial = check_hook_payload(json.dumps({"toolCall": {"name": "ask_question", "args": qb_initial.to_tool_call()}}))
                self.assertEqual(pre_initial.get("decision"), "allow")

                # 2. Simulate 3 operator affirmations via PostToolUse hook
                for i in range(3):
                    post_event = {
                        "toolCall": {
                            "name": "ask_question",
                            "args": {
                                "questions": [
                                    {
                                        "decision_id": f"dec-weekly-layout-{i}",
                                        "category": "tactical_execution",
                                        "question": q_text,
                                        "options": qb_initial.display_options,
                                    }
                                ]
                            },
                        },
                        "toolResult": {"selected": qb_initial.display_options[0]},
                    }
                    res_post = process_post_tool_payload(json.dumps(post_event))
                    self.assertEqual(res_post.get("status"), "RECORDED")

                # 3. Run offline learning synthesis to sharpen option nuance and compute empirical posteriors
                engine = ClosedLoopLearningEngine(runtime_root=tmp_root, proposals_dir=tmp_root / "proposals")
                learned_payload = engine.synthesize_hitl_option_nuance_and_posteriors(
                    hitl_ledger_path=json_path,
                    output_store_path=learned_store_path,
                )
                self.assertGreaterEqual(learned_payload.get("total_learned_options", 0), 2)

                # 4. Rebuild the same question: verify sharper learned nuance and widened confidence margin (> 20%)
                qb_learned = (
                    QuestionBuilder(q_text, metadata={"category": "tactical_execution"})
                    .add_option(
                        label=opt_a,
                        plain_english_summary="Formats status notes as short bullets",
                        impact_statement="Improves reading speed for reviewers",
                        confidence_score=0.55,
                        recommended=True,
                    )
                    .add_option(
                        label=opt_b,
                        plain_english_summary="Formats status notes as full paragraphs",
                        impact_statement="Preserves surrounding narrative detail",
                        confidence_score=0.45,
                        recommended=False,
                    )
                    .build()
                )
                c1_top = qb_learned.options[0]["confidence_score"]
                c1_sec = qb_learned.options[1]["confidence_score"]
                learned_spread = round(c1_top - c1_sec, 4)

                # Spread widened from 0.10 to > 0.20 (e.g. >= 0.45)
                self.assertGreater(learned_spread, 0.20)
                self.assertFalse(needs_operator_decision(c1_top, c1_sec))
                # Nuance sharpened from ledger history
                self.assertIn("affirmed in 3 of 3 prior reviews", qb_learned.options[0]["plain_english_summary"])

                # 5. PreToolUse now automatically denies ask_question and logs AUTONOMOUS bypass!
                pre_learned = check_hook_payload(json.dumps({"toolCall": {"name": "ask_question", "args": qb_learned.to_tool_call()}}))
                self.assertEqual(pre_learned.get("decision"), "deny")
                self.assertIn("[HITL_MARGIN_BYPASS_REQUIRED]", pre_learned.get("reason", ""))

    def test_post_invocation_guard_intercepts_cli_graph_hygiene_mutation_without_merge(self) -> None:
        """Verify live apply_graph_hygiene_remediation.py without governed PR merge or ask_question triggers promotion gate."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run graph hygiene agent"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "run_command",
                            "args": {
                                "CommandLine": ".venv/bin/python tools/apply_graph_hygiene_remediation.py --promote-generation --cluster-id c1"
                            },
                        }
                    ],
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": "Completed remediation and promoted generation.",
                    "tool_calls": [],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(payload))
        self.assertEqual(out.get("terminationBehavior"), "force_continue")
        self.assertEqual(len(out.get("injectSteps", [])), 1)
        self.assertIn("POST-EDIT PROMOTION GATE", out["injectSteps"][0]["userMessage"])

    def test_post_invocation_guard_allows_cli_mutation_after_governed_pr_merge_or_dry_run(self) -> None:
        """Verify dry-run CLI calls and CLI mutations followed by pr_governance.py merge conclude cleanly."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        dry_run_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Dry run hygiene"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "run_command",
                            "args": {
                                "CommandLine": ".venv/bin/python tools/apply_graph_hygiene_remediation.py --dry-run"
                            },
                        }
                    ],
                },
            ]
        }
        self.assertEqual(evaluate_post_invocation_payload(json.dumps(dry_run_payload)).get("injectSteps"), [])

        merged_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run graph hygiene agent"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "run_command",
                            "args": {
                                "CommandLine": ".venv/bin/python tools/apply_graph_hygiene_remediation.py --promote-generation"
                            },
                        }
                    ],
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "tool_calls": [
                        {
                            "name": "run_command",
                            "args": {
                                "CommandLine": ".venv/bin/python tools/pr_governance.py merge fix/graph-hygiene-remediation-10-25-2026 --title 'fix: hygiene'"
                            },
                        }
                    ],
                },
            ]
        }
        self.assertEqual(evaluate_post_invocation_payload(json.dumps(merged_payload)).get("injectSteps"), [])

    def test_post_invocation_guard_intercepts_technical_jargon_in_rca_or_prose(self) -> None:
        """Verify technical jargon (SQL, snake_case, class/method names) in final response triggers layman rewrite."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload

        jargon_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Explain why promotion failed"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": (
                        "The root cause is that MutationGuard._add_operation_probes ran "
                        "SELECT 1 FROM assertion WHERE canonical_status = 'canonical' against graph.sqlite."
                    ),
                    "tool_calls": [],
                },
            ]
        }
        out = evaluate_post_invocation_payload(json.dumps(jargon_payload))
        self.assertEqual(out.get("terminationBehavior"), "force_continue")
        inject_steps = out.get("injectSteps", [])
        self.assertEqual(len(inject_steps), 1)
        msg = inject_steps[0].get("ephemeralMessage") or inject_steps[0].get("userMessage") or ""
        self.assertIn("LAYMAN ZERO-JARGON GOVERNANCE INTERCEPT", msg)

        # Clean layman response with concrete numbers and plain-English link labels passes cleanly
        clean_payload = {
            "steps": [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Explain why promotion failed"},
                {
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "content": (
                        "The verification check confirmed that all 19 bonus plan facts were written to the database "
                        "and promoted to official status (+1 source, +4 entities, +19 official facts). "
                        "See [Bonus Plan Signal Summary](file:///tmp/03_canonical_signal_projection.json)."
                    ),
                    "tool_calls": [],
                },
            ]
        }
        self.assertEqual(evaluate_post_invocation_payload(json.dumps(clean_payload)).get("injectSteps"), [])

    def test_pre_invocation_and_pre_tool_use_layman_guard_blocks_before_output(self) -> None:
        """Verify PreInvocation checklist injection and PreToolUse user-facing artifact block fire BEFORE output."""
        from scripts.hitl_post_invocation_guard import evaluate_post_invocation_payload
        from scripts.hitl_pre_tool_guard import handle_tool_call_guard

        # 1. PreInvocation injects ephemeral layman checklist before model generates response
        pre_inv_out = evaluate_post_invocation_payload(
            json.dumps(
                {
                    "invocationNum": 1,
                    "steps": [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run testing hotspot agent"}],
                }
            )
        )
        self.assertEqual(len(pre_inv_out.get("injectSteps", [])), 1)
        self.assertIn("PRE-OUTPUT LAYMAN CLARITY GATE ACTIVE", pre_inv_out["injectSteps"][0]["ephemeralMessage"])

        # 2. PreToolUse denies user-facing artifact containing 'import' or snake_case BEFORE file write
        artifact_path = str(Path.home() / ".gemini" / "antigravity" / "brain" / "test-id" / "summary.md")
        bad_artifact = {
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": artifact_path,
                    "Content": "Filtered 685 mixed decoy import files across snake_case modules.",
                    "ArtifactMetadata": {
                        "Summary": "Completion summary",
                        "UserFacing": True,
                        "RequestFeedback": False,
                    },
                },
            }
        }
        res_deny = handle_tool_call_guard(json.dumps(bad_artifact))
        self.assertIsNotNone(res_deny)
        self.assertEqual(res_deny.get("decision"), "deny")
        self.assertIn("PRE_OUTPUT_LAYMAN_GUARD", res_deny.get("reason", ""))

        # 3. PreToolUse allows clean user-facing artifact
        clean_artifact = {
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": artifact_path,
                    "Content": "Filtered 685 mixed decoy reference files across 2,078 scanned modules.",
                    "ArtifactMetadata": {
                        "Summary": "Completion summary",
                        "UserFacing": True,
                        "RequestFeedback": False,
                    },
                },
            }
        }
        res_allow = handle_tool_call_guard(json.dumps(clean_artifact))
        self.assertIsNotNone(res_allow)
        self.assertEqual(res_allow.get("decision"), "allow")

    def test_global_core_governance_layman_output_guard(self) -> None:
        """Verify global core-governance layman_output_guard.py intercepts jargon across PreInvocation, PreToolUse, and PostInvocation."""
        global_script = Path.home() / ".gemini" / "config" / "plugins" / "core-governance" / "scripts" / "layman_output_guard.py"
        if not global_script.is_file():
            return
        jargon_in = json.dumps(
            {
                "steps": [
                    {"type": "USER_INPUT", "content": "Explain why promotion failed"},
                    {
                        "type": "PLANNER_RESPONSE",
                        "source": "MODEL",
                        "content": "MutationGuard._add_operation_probes executed SELECT 1 FROM assertion WHERE canonical_status='canonical'.",
                        "tool_calls": [],
                    },
                ]
            }
        )
        proc = subprocess.run([sys.executable, str(global_script)], input=jargon_in, text=True, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        res = json.loads(proc.stdout.strip())
        self.assertEqual(res.get("terminationBehavior"), "force_continue")
        msg = res["injectSteps"][0].get("ephemeralMessage") or res["injectSteps"][0].get("userMessage") or ""
        self.assertIn("LAYMAN ZERO-JARGON GOVERNANCE INTERCEPT", msg)


if __name__ == "__main__":
    unittest.main()

