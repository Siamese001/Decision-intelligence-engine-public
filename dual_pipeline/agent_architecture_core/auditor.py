"""Static repository governance auditor for unified agent architecture.

Statically verifies 100% repository conformance to the Unified Agent Architecture:
- AGENTS.md required sections and phrases.
- .agents/rules/unified-agent-architecture.md core pillars, foundations, and lifecycle.
- .agents/rules/conformance-map.md mapping traceability.
- .agents/hooks.json guard hook registrations and severity.
- .agents/skills/ registered skill catalog.
- .githooks/ pre-commit and pre-push test wiring.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.agent_architecture_core.contracts import REQUIRED_UNIFIED_ARCHITECTURE_PHRASES


def _check_agents_md(repo_root: Path) -> list[str]:
    """Verifies AGENTS.md presence and required architecture phrases."""
    errors: list[str] = []
    agents_md = repo_root / "AGENTS.md"
    if not agents_md.is_file():
        return ["AGENTS_MD_MISSING: AGENTS.md does not exist at repo root."]
    content = agents_md.read_text(encoding="utf-8")
    for phrase in REQUIRED_UNIFIED_ARCHITECTURE_PHRASES:
        if phrase not in content:
            errors.append(f"AGENTS_MD_MISSING_PHRASE: AGENTS.md is missing required phrase: '{phrase}'.")
    return errors


def _check_rule_file(repo_root: Path) -> list[str]:
    """Verifies unified-agent-architecture.md presence and required sections."""
    errors: list[str] = []
    rule_file = repo_root / ".agents" / "rules" / "unified-agent-architecture.md"
    if not rule_file.is_file():
        return ["RULE_FILE_MISSING: .agents/rules/unified-agent-architecture.md does not exist."]
    rule_content = rule_file.read_text(encoding="utf-8")
    required_rule_phrases = (
        "Skills (\"What work gets done\")",
        "Agents (\"Who decides & orchestrates\")",
        "Sub-Agents (\"Specialized executors\")",
        "Hooks (\"When to extend\")",
        "Rules (\"What must be true\")",
        "SHARED FOUNDATIONS",
        "Delegation Contract Protocol",
        "7-Step Lifecycle",
        "End-to-End Execution Flow",
    )
    for phrase in required_rule_phrases:
        if phrase not in rule_content:
            errors.append(f"RULE_FILE_MISSING_PHRASE: unified-agent-architecture.md is missing: '{phrase}'.")
    return errors


def _check_conformance_map(repo_root: Path) -> list[str]:
    """Verifies conformance-map.md unified architecture tracing."""
    errors: list[str] = []
    conformance_file = repo_root / ".agents" / "rules" / "conformance-map.md"
    if not conformance_file.is_file():
        return ["CONFORMANCE_MAP_MISSING: .agents/rules/conformance-map.md does not exist."]
    conf_content = conformance_file.read_text(encoding="utf-8")
    if "§\"Skills, agents, sub-agents, hooks, and rules unified architecture\"" not in conf_content:
        errors.append("CONFORMANCE_MAP_SECTION_MISSING: Conformance map is missing unified architecture control mapping.")
    if "test_unified_agent_architecture.py" not in conf_content:
        errors.append("CONFORMANCE_MAP_TEST_MISSING: Conformance map must reference test_unified_agent_architecture.py.")
    if "| Skills, agents, sub-agents, hooks & rules unified architecture | ✅ | ✅ | ✅ | ✅ | ✅ |" not in conf_content:
        errors.append("CONFORMANCE_MAP_SUMMARY_INCOMPLETE: Summary table missing complete unified architecture row.")
    return errors


def _check_hooks_json(repo_root: Path) -> list[str]:
    """Verifies hooks.json architecture guards and severity."""
    errors: list[str] = []
    hooks_file = repo_root / ".agents" / "hooks.json"
    if not hooks_file.is_file():
        return ["HOOKS_JSON_MISSING: .agents/hooks.json does not exist."]
    try:
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks = {h.get("id"): h for h in hooks_data.get("hooks", [])}
        required_hooks = (
            "nee-architecture-lifecycle-guard",
            "nee-subagent-delegation-guard",
            "nee-hook-extension-guard",
        )
        for h_id in required_hooks:
            if h_id not in hooks:
                errors.append(f"HOOKS_JSON_GUARD_MISSING: .agents/hooks.json must declare '{h_id}'.")
            elif hooks[h_id].get("severity") != "blocking":
                errors.append(f"HOOK_SEVERITY_NOT_BLOCKING: Guard '{h_id}' must have severity 'blocking'.")
    except Exception as e:
        errors.append(f"HOOKS_JSON_PARSE_ERROR: Failed to parse .agents/hooks.json: {e}")
    return errors


def _check_skills_catalog(repo_root: Path) -> list[str]:
    """Verifies .agents/skills directory structure and health."""
    errors: list[str] = []
    skills_dir = repo_root / ".agents" / "skills"
    if not skills_dir.is_dir():
        return ["SKILLS_DIR_MISSING: .agents/skills directory does not exist."]
    skill_subdirs = [p for p in skills_dir.iterdir() if p.is_dir()]
    if len(skill_subdirs) < 10:
        errors.append(f"SKILLS_CATALOG_DEPLETED: Expected >= 10 skills in .agents/skills, found {len(skill_subdirs)}.")
    for sdir in skill_subdirs:
        if not (sdir / "SKILL.md").is_file():
            errors.append(f"SKILL_MD_MISSING: Skill '{sdir.name}' is missing SKILL.md.")
    return errors


def _check_git_hooks(repo_root: Path) -> list[str]:
    """Verifies git hook scripts enforce unified agent architecture tests."""
    errors: list[str] = []
    pre_commit = repo_root / ".githooks" / "pre-commit"
    if not pre_commit.is_file():
        errors.append("PRE_COMMIT_HOOK_MISSING: .githooks/pre-commit does not exist.")
    elif "tests.test_unified_agent_architecture" not in pre_commit.read_text(encoding="utf-8"):
        errors.append("PRE_COMMIT_TEST_MISSING: .githooks/pre-commit must run tests.test_unified_agent_architecture.")

    pre_push = repo_root / ".githooks" / "pre-push"
    if not pre_push.is_file():
        errors.append("PRE_PUSH_HOOK_MISSING: .githooks/pre-push does not exist.")
    elif "tests.test_unified_agent_architecture" not in pre_push.read_text(encoding="utf-8"):
        errors.append("PRE_PUSH_TEST_MISSING: .githooks/pre-push must run tests.test_unified_agent_architecture.")
    return errors


def validate_unified_architecture_governance(repo_root: Path) -> list[str]:
    """Statically verifies 100% repository conformance to the Unified Agent Architecture."""
    errors: list[str] = []
    errors.extend(_check_agents_md(repo_root))
    errors.extend(_check_rule_file(repo_root))
    errors.extend(_check_conformance_map(repo_root))
    errors.extend(_check_hooks_json(repo_root))
    errors.extend(_check_skills_catalog(repo_root))
    errors.extend(_check_git_hooks(repo_root))
    return errors


class StaticAgenticGovernanceAuditor:
    """Static auditor utility for unified agent architecture governance."""

    @staticmethod
    def audit(repo_root: Path) -> list[str]:
        return validate_unified_architecture_governance(repo_root)
