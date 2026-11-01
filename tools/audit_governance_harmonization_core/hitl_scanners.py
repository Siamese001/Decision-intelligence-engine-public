"""HITL-specific governance scanners: deprecated tool names, replaced terms, and literal margins."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import (
    REPO_ROOT,
    Finding,
    HarmonizationReport,
    read_text_safe,
    relative,
)

from config.hitl_tool_name import DEPRECATED_NAMES
from config.operator_decision_vocabulary import (
    DEPRECATED_TERMS,
    DISAMBIGUATION_MAP,
)

DEPRECATED_TOOL_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "config/hitl_tool_name.py",
        "config/operator_decision_vocabulary.py",
        "dual_pipeline/hitl_governance_core/runtime_tool_adapter.py",
        "tools/lint_hitl_tool_name.py",
        "tools/sync_hitl_tool_name.py",
        "tools/mcp_deepwiki_server.py",
        "tests/test_mcp_deepwiki_server.py",
        "tools/check_ask_user_question.py",
        "dual_pipeline/agent_sdk/policies.py",
        "tests/test_agent_policies.py",
        "governance/OPERATOR_DECISION_GLOSSARY.md",
        ".agents/hooks.json",
        "hitl_decision_ledger.json",
        "implementation_plan.md",
    }
)


def _load_baseline_term_inventory() -> set[str]:
    inventory_path = REPO_ROOT / "reports" / "ask_user_question_enforcement" / "term_inventory.json"
    if not inventory_path.exists():
        return set()
    try:
        data = json.loads(inventory_path.read_text(encoding="utf-8"))
        return {item["file"] for item in data.get("inventory", [])}
    except Exception:
        return set()


def scan_deprecated_tool_names(files: list[Path], report: HarmonizationReport) -> None:
    """Detect deprecated operator questioning tool names across governance files."""
    for f in files:
        rel = relative(f).replace("\\", "/")
        if rel in DEPRECATED_TOOL_EXEMPTIONS:
            continue
        if any(rel.startswith(p) for p in ("reports/", "receipts/", "archive/", "migrations/", "docs/plans/", "governance/rca/", "governance/decision_intelligence/RCA_")):
            continue
        if any(rel.startswith(p) for p in ("tests/test_hitl_tool_name_ssot", "tests/end_to_end/test_hitl_tool_name_ssot")):
            continue

        content = read_text_safe(f)
        if not content:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            for dep in DEPRECATED_NAMES:
                if re.search(r"(?<![A-Za-z0-9_])" + re.escape(dep) + r"(?![A-Za-z0-9_])", line, re.IGNORECASE):
                    report.add(
                        Finding(
                            category="deprecated_tool_name",
                            severity="WARNING",
                            title=f"Deprecated Tool Name: '{dep}'",
                            description=(
                                f"File uses deprecated tool name '{dep}'. The sole canonical SSOT name "
                                f"for presenting interactive questions to the operator is 'ask_user_question'."
                            ),
                            ssot_file="config/hitl_tool_name.py",
                            ssot_value="ask_user_question",
                            affected_files=[
                                {
                                    "path": rel,
                                    "line": i,
                                    "current_value": line.strip()[:120],
                                    "expected_value": "ask_user_question",
                                }
                            ],
                            proposed_fix=f"Replace '{dep}' with 'ask_user_question'.",
                        )
                    )


REPLACED_TERMS: dict[str, str] = {
    "requires_hitl": "needs_operator_decision",
    "HITL_REQUIRED": "OPERATOR_INPUT_REQUIRED",
    "hitl_receipt_bound": "approval_signature_valid",
    "HITL_REQUIRED_TOOLS": "TOOL_APPROVAL_REQUIRED",
}

REPLACED_TERMS_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "config/operator_decision_vocabulary.py",
        "config/hitl_tool_name.py",
        "governance/OPERATOR_DECISION_GLOSSARY.md",
        "tools/lint_operator_decision_terms.py",
        "tools/reconcile_decision_ledger.py",
    }
)


def scan_replaced_decision_terms(files: list[Path], report: HarmonizationReport) -> None:
    """Detect overloaded legacy HITL terms replaced by Operator Decision Vocabulary SSOT."""
    baseline_files = _load_baseline_term_inventory()

    for f in files:
        rel = relative(f).replace("\\", "/")
        if rel in REPLACED_TERMS_EXEMPTIONS or rel in baseline_files:
            continue
        if any(rel.startswith(p) for p in ("reports/", "receipts/", "archive/", "migrations/", "docs/plans/", "governance/rca/", "governance/decision_intelligence/RCA_")):
            continue
        if any(rel.startswith(p) for p in ("tests/test_operator_decision_vocabulary", "tests/end_to_end/test_operator_decision_vocabulary")):
            continue

        content = read_text_safe(f)
        if not content:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            for term, canonical in REPLACED_TERMS.items():
                if re.search(r"\b" + re.escape(term) + r"\b", line):
                    report.add(
                        Finding(
                            category="replaced_decision_term",
                            severity="WARNING",
                            title=f"Replaced Operator Decision Term: '{term}'",
                            description=(
                                f"File uses deprecated/replaced term '{term}'. Replaced by '{canonical}' "
                                f"per the Operator Decision Vocabulary SSOT."
                            ),
                            ssot_file="config/operator_decision_vocabulary.py",
                            ssot_value=canonical,
                            affected_files=[
                                {
                                    "path": rel,
                                    "line": i,
                                    "current_value": line.strip()[:120],
                                    "expected_value": canonical,
                                }
                            ],
                            proposed_fix=f"Replace '{term}' with '{canonical}'.",
                        )
                    )


LITERAL_MARGIN_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "dual_pipeline/parameters.py",
        "dual_pipeline/policy.py",
        "tools/lint_margin_ssot.py",
        "governance/OPERATOR_DECISION_GLOSSARY.md",
        "config/operator_decision_vocabulary.py",
        "config/prompts/testing/REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md",
    }
)


def scan_literal_margins(files: list[Path], report: HarmonizationReport) -> None:
    """Detect literal margin thresholds (e.g. 15%, 30%) instead of SSOT accessors in active rules."""
    pattern = re.compile(
        r"(?:confidence\s+margin|ambiguity\s+margin|margin\s+rule)\s*(?:Δ|Delta)?\s*(?:<|<=|>=|>)\s*\d+%",
        re.IGNORECASE,
    )
    for f in files:
        rel = relative(f).replace("\\", "/")
        if rel in LITERAL_MARGIN_EXEMPTIONS:
            continue
        if any(rel.startswith(p) for p in ("reports/", "receipts/", "archive/", "migrations/", "docs/plans/", "tests/", "governance/rca/")):
            continue

        content = read_text_safe(f)
        if not content:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if (
                pattern.search(line)
                and "needs_operator_decision" not in line
                and "dual_pipeline.parameters" not in line
            ):
                report.add(
                        Finding(
                            category="literal_margin",
                            severity="INFO",
                            title="Literal Ambiguity Margin in Governance Rule",
                            description=(
                                "Governance rule specifies a literal percentage margin instead of referencing "
                                "the SSOT accessor needs_operator_decision(c_top, c_second) in dual_pipeline.parameters."
                            ),
                            ssot_file="dual_pipeline/parameters.py",
                            ssot_value="needs_operator_decision(c_top, c_second)",
                            affected_files=[
                                {
                                    "path": rel,
                                    "line": i,
                                    "current_value": line.strip()[:120],
                                    "expected_value": "needs_operator_decision(c_top, c_second)",
                                }
                            ],
                            proposed_fix="Reference dual_pipeline.parameters.needs_operator_decision instead of hardcoding literal margin.",
                        )
                    )
