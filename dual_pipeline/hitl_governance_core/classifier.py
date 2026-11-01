"""
dual_pipeline/hitl_governance_core/classifier.py

Deterministic Criticality Classifier for HITL Decision Governance.
Classifies proposed operations into categorical domains (TACTICAL, ROUTINE,
ARCHITECTURAL, DESTRUCTIVE, POLICY, GOVERNANCE) to enforce the Dual-Lock Decision Invariant.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Mapping, Optional, Sequence, Set


class DecisionCategory(str, Enum):
    """Categorical taxonomy for proposed pipeline decisions."""
    TACTICAL = "tactical"
    ROUTINE = "routine"
    ARCHITECTURAL = "architectural"
    DESTRUCTIVE = "destructive"
    POLICY = "policy"
    GOVERNANCE = "governance"


# Categories that strictly fail Lock 2, mandating interactive operator review regardless of confidence spread
MANDATORY_HITL_CATEGORIES: Set[DecisionCategory] = {
    DecisionCategory.ARCHITECTURAL,
    DecisionCategory.DESTRUCTIVE,
    DecisionCategory.POLICY,
    DecisionCategory.GOVERNANCE,
}

# Destructive action verbs and roots
DESTRUCTIVE_ACTION_VERBS: Set[str] = {
    "retire", "delete", "destroy", "drop", "truncate", "purge",
    "kill", "decommission", "discard", "deprecate", "terminate",
    "wipe", "erase", "remove", "tear down", "teardown", "prune",
}

# Architectural path triggers
ARCHITECTURAL_PATH_TRIGGERS: Sequence[str] = (
    "contracts/",
    "dual_pipeline/storage/",
    "dual_pipeline/uwg_core/",
    "dual_pipeline/parameters.py",
    "config/runtime/parameters.json",
    "mcp_server.py",
    "tools/nee_mcp_server.py",
    "active_pair.json",
    "generations/",
    "storage/",
)

# Governance path triggers
GOVERNANCE_PATH_TRIGGERS: Sequence[str] = (
    ".agents/rules/",
    ".agents/hooks.json",
    "AGENTS.md",
    "hitl_decision_ledger.json",
    "governance/",
    "tools/pr_governance.py",
    "tools/record_wave_receipt.py",
)

# Governance-specific policy parameter triggers
GOVERNANCE_POLICY_KEYWORDS: Set[str] = {
    "ambiguity_margin", "divergence_margin", "confidence_floor",
    "bayesian_prior", "gate_policy", "ci_gate", "authorization_boundary",
}

# General policy keywords that may appear in ordinary operational prose
POLICY_KEYWORDS: Set[str] = {
    "threshold", "ambiguity_margin", "divergence_margin", "confidence_floor",
    "bayesian_prior", "budget", "timeout", "gate_policy", "rate_limit",
    "ci_gate", "profile", "permission", "authorization_boundary",
}

# Non-waivable PR merge and release promotion triggers (always require HITL regardless of spread)
PR_MERGE_AND_RELEASE_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(
        r"\b(?:pr_merge_authorization|approve\s+(?:the\s+|this\s+)?pr\s+merge|authorize\s+(?:the\s+|this\s+|governed\s+)?(?:pr\s+)?(?:merge|promotion)|merge\s+(?:the\s+|this\s+)?pull\s+request|governed\s+(?:pr\s+)?merge)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:merge|merging|promote|promoting|promotion|publish|publishing|publication|activate|activating|activation)\b[\s\S]{0,200}?\b(?:into\s+(?:the\s+|our\s+|local\s+)?main(?:\s+branch)?|to\s+(?:the\s+|our\s+|local\s+)?main(?:\s+branch)?|(?:the\s+|this\s+)?(?:candidate|branch|release|pull\s+request|knowledge\s+graph))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:promote\s+(?:the\s+|this\s+)?(?:candidate|branch|release)|release\s+activation|candidate\s+disposition|database\s+cleanup)\b", re.IGNORECASE),
    re.compile(r"\b(?:rule\s+files?|agent\s+rules?|governance\s+rules?|frontmatter\s+(?:syntax|schema|errors?|yaml)|hook\s+configuration)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class DecisionClassificationResult:
    """Immutable result of deterministic decision criticality classification."""
    category: DecisionCategory
    is_critical: bool
    requires_mandatory_hitl: bool
    matched_triggers: List[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "is_critical": self.is_critical,
            "requires_mandatory_hitl": self.requires_mandatory_hitl,
            "matched_triggers": self.matched_triggers,
            "rationale": self.rationale,
        }


def _collect_target_files(
    ctx: Mapping[str, Any],
    target_files: Optional[Sequence[str]],
) -> List[str]:
    files_to_check: List[str] = []
    if target_files:
        files_to_check.extend(target_files)
    if "target_files" in ctx and isinstance(ctx["target_files"], (list, tuple)):
        files_to_check.extend(ctx["target_files"])
    for k in ("file", "path"):
        if k in ctx:
            files_to_check.append(str(ctx[k]))
    return files_to_check


def _extract_option_text(opt: Any) -> List[str]:
    if isinstance(opt, str):
        return [opt]
    if isinstance(opt, Mapping):
        return [
            str(opt[k])
            for k in ("label", "text", "description", "plain_english_summary", "impact_statement")
            if k in opt and opt[k]
        ]
    return []


def _collect_inspection_targets(
    context: Optional[Mapping[str, Any]],
    target_files: Optional[Sequence[str]],
    action_description: Optional[str],
    options: Optional[Sequence[Any]],
) -> Tuple[List[str], str, Set[str], str, Set[str], str, Set[str]]:
    ctx = dict(context or {})
    files_to_check = _collect_target_files(ctx, target_files)

    action_corpus: List[str] = []
    if action_description:
        action_corpus.append(action_description)
    for field_name in ("description", "action", "question"):
        if field_name in ctx:
            action_corpus.append(str(ctx[field_name]))
    action_text = " ".join(action_corpus).lower()
    action_words = set(re.findall(r"\b[a-z_\-]+\b", action_text))

    options_corpus: List[str] = []
    if options:
        for opt in options:
            options_corpus.extend(_extract_option_text(opt))
    options_text = " ".join(options_corpus).lower()
    options_words = set(re.findall(r"\b[a-z_\-]+\b", options_text))

    full_text = f"{action_text} {options_text}".strip()
    words = action_words | options_words
    return files_to_check, full_text, words, action_text, action_words, options_text, options_words


def _check_destructive_triggers(
    full_text: str,
    words: Set[str],
    action_words: Set[str],
    is_operational: bool = False,
) -> Optional[DecisionClassificationResult]:
    matched_destructive: Set[str] = set()
    if is_operational:
        # For operational decisions, benign maintenance words (e.g. 'removes clutter', 'discards cache')
        # in option summaries do not trigger Lock 2. Only action-level destructive verbs or catastrophic commands trigger Lock 2.
        matched_destructive = action_words & DESTRUCTIVE_ACTION_VERBS
        for phrase in ("tear down", "tear-down", "drop table", "drop database", "purge database", "rm -rf", "wipe database"):
            if phrase in full_text:
                matched_destructive.add(phrase)
    else:
        matched_destructive = words & DESTRUCTIVE_ACTION_VERBS
        for phrase in ("tear down", "tear-down"):
            if phrase in full_text:
                matched_destructive.add(phrase)

    if matched_destructive:
        triggers = [f"Destructive action verb(s) detected: {sorted(list(matched_destructive))}"]
        return DecisionClassificationResult(
            category=DecisionCategory.DESTRUCTIVE,
            is_critical=True,
            requires_mandatory_hitl=True,
            matched_triggers=triggers,
            rationale=f"DESTRUCTIVE_ACTION_LOCK: Proposed operation involves irreversible component/data action ({', '.join(sorted(matched_destructive))}). Mandatory operator review required.",
        )
    return None


def _check_path_triggers(
    files_to_check: Sequence[str],
    patterns: Sequence[str],
    category: DecisionCategory,
    lock_prefix: str,
    scope_name: str,
) -> Optional[DecisionClassificationResult]:
    for f in files_to_check:
        raw_clean = re.sub(r"/+", "/", str(f).replace("\\", "/"))
        norm_clean = os.path.normpath(raw_clean).replace("\\", "/")
        candidates = {raw_clean, norm_clean, raw_clean.lstrip("./"), norm_clean.lstrip("./")}
        for clean_f in candidates:
            for trig in patterns:
                if trig in clean_f or clean_f.endswith(trig):
                    return DecisionClassificationResult(
                        category=category,
                        is_critical=True,
                        requires_mandatory_hitl=True,
                        matched_triggers=[f"{scope_name} file modified: {clean_f}"],
                        rationale=f"{lock_prefix}: Target file '{clean_f}' controls {scope_name.lower()}. Operator authorization required.",
                    )
    return None


def _check_policy_triggers(
    words: Set[str],
    action_words: Set[str],
    full_text: str,
    is_operational: bool = False,
) -> Optional[DecisionClassificationResult]:
    matched_policy: Set[str] = set()
    if is_operational:
        # Option descriptions containing everyday words like 'threshold', 'budget', 'timeout'
        # in operational contexts do not trigger policy lock unless governing parameters are explicitly targeted.
        matched_policy = (action_words & POLICY_KEYWORDS) | (words & GOVERNANCE_POLICY_KEYWORDS)
        if not matched_policy and re.search(r"\b(?:modify|update|change|alter|adjust|tune|set|override)\b.*?\b(?:threshold|budget|timeout|rate_limit)\b", full_text):
            matched_policy.add("parameter_modification")
    else:
        matched_policy = words & POLICY_KEYWORDS

    if matched_policy:
        return DecisionClassificationResult(
            category=DecisionCategory.POLICY,
            is_critical=True,
            requires_mandatory_hitl=True,
            matched_triggers=[f"Policy keyword(s) detected: {sorted(list(matched_policy))}"],
            rationale=f"POLICY_MODIFICATION_LOCK: Proposed change alters governed parameters or thresholds ({', '.join(sorted(matched_policy))}). Operator authorization required.",
        )
    return None


def classify_decision_criticality(
    context: Optional[Mapping[str, Any]] = None,
    target_files: Optional[Sequence[str]] = None,
    action_description: Optional[str] = None,
    options: Optional[Sequence[Any]] = None,
    policy_snapshot: Optional[Any] = None,
) -> DecisionClassificationResult:
    """
    Deterministically evaluates decision criticality from paths, verbs, and context.
    Ensures that architectural, destructive, governance, and policy actions cannot
    be classified as tactical or routine.
    """
    ctx = dict(context or {})
    declared_category = str(ctx.get("category", "")).lower().strip()
    is_operational = declared_category in ("operational", "routine", "tactical")

    files_to_check, full_text, words, action_text, action_words, options_text, options_words = _collect_inspection_targets(
        context=context,
        target_files=target_files,
        action_description=action_description,
        options=options,
    )

    # Priority A0: Non-waivable PR Merge & Release Promotion Check
    for pat in PR_MERGE_AND_RELEASE_PATTERNS:
        m = pat.search(full_text)
        if m:
            matched_phrase = m.group(0)
            return DecisionClassificationResult(
                category=DecisionCategory.GOVERNANCE,
                is_critical=True,
                requires_mandatory_hitl=True,
                matched_triggers=[f"Mandatory governance gate detected: '{matched_phrase}'"],
                rationale=(
                    f"PR_MERGE_AND_RELEASE_LOCK: Operation involves '{matched_phrase}'. "
                    "Explicit human operator authorization via ask_user_question is non-waivable regardless of confidence spread."
                ),
            )

    # Priority A: Destructive Verbs Check
    destructive_res = _check_destructive_triggers(
        full_text, words, action_words, is_operational=is_operational
    )
    if destructive_res is not None:
        return destructive_res

    # Priority B: Governance Files Check
    gov_res = _check_path_triggers(
        files_to_check,
        GOVERNANCE_PATH_TRIGGERS,
        DecisionCategory.GOVERNANCE,
        "GOVERNANCE_SURFACE_LOCK",
        "Governance surface",
    )
    if gov_res is not None:
        return gov_res

    # Priority C: Architectural Scope Check
    arch_res = _check_path_triggers(
        files_to_check,
        ARCHITECTURAL_PATH_TRIGGERS,
        DecisionCategory.ARCHITECTURAL,
        "ARCHITECTURAL_SCOPE_LOCK",
        "Architectural component",
    )
    if arch_res is not None:
        return arch_res

    # Priority D: Policy Modifications Check
    policy_res = _check_policy_triggers(
        words, action_words, full_text, is_operational=is_operational
    )
    if policy_res is not None:
        return policy_res

    # Priority E: Default Tactical / Routine
    is_tactical = "tactical" in full_text or "routine" in full_text
    chosen_cat = DecisionCategory.TACTICAL if is_tactical else DecisionCategory.ROUTINE
    return DecisionClassificationResult(
        category=chosen_cat,
        is_critical=False,
        requires_mandatory_hitl=False,
        matched_triggers=[],
        rationale="ROUTINE_TACTICAL_SCOPE: Non-critical, reversible implementation action within bounded module scope.",
    )
