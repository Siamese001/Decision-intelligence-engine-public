"""tests/test_disposition_advice_anti_disjunction.py

Unit and regression tests for Wave 3: Ban Disjunctive Agent Advice.
Enforces INV-ZERO-DISJUNCTIVE-ADVICE and INV-DECISIVE-RECOMMENDATION.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DISJUNCTIVE_ADVICE_PATTERNS = [
    re.compile(r"\brebase(?:\s+onto\s+main)?\s+or\s+archive\b", re.IGNORECASE),
    re.compile(r"\barchive\s+or\s+rebase(?:\s+onto\s+main)?\b", re.IGNORECASE),
    re.compile(r"\brebase\s+or\s+prune\b", re.IGNORECASE),
    re.compile(r"\bprune\s+or\s+rebase\b", re.IGNORECASE),
    re.compile(r"\bmerge\s+or\s+archive\b", re.IGNORECASE),
    re.compile(r"\barchive\s+or\s+merge\b", re.IGNORECASE),
]


def check_for_disjunctive_advice(text: str) -> list[str]:
    violations = []
    for pat in DISJUNCTIVE_ADVICE_PATTERNS:
        match = pat.search(text)
        if match:
            violations.append(f"Disjunctive advice detected: '{match.group(0)}'")
    return violations


def test_disjunctive_advice_rejection() -> None:
    bad_advice = "Commit authored diagnostic data to the branch first to prevent loss, then rebase onto main or archive."
    violations = check_for_disjunctive_advice(bad_advice)
    assert len(violations) > 0
    assert "rebase onto main or archive" in violations[0].lower()


def test_decisive_advice_acceptance() -> None:
    good_advice_1 = "Archive (Do Not Rebase): The branch is completely superseded by main@05124c464."
    good_advice_2 = "Prune: Zero unique commits ahead of main; worktree clean."
    good_advice_3 = "Rebase: Clean divergence, rebase onto main and run review."

    assert len(check_for_disjunctive_advice(good_advice_1)) == 0
    assert len(check_for_disjunctive_advice(good_advice_2)) == 0
    assert len(check_for_disjunctive_advice(good_advice_3)) == 0


def test_governance_rules_contain_anti_disjunction_invariants() -> None:
    pr_queue_rule = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
    eng_output_rule = ROOT / ".agents" / "rules" / "engineering-output.md"

    assert pr_queue_rule.exists()
    assert eng_output_rule.exists()

    pr_text = pr_queue_rule.read_text(encoding="utf-8")
    eng_text = eng_output_rule.read_text(encoding="utf-8")

    assert "INV-ZERO-DISJUNCTIVE-ADVICE" in pr_text
    assert "INV-DECISIVE-RECOMMENDATION" in eng_text
