#!/usr/bin/env python3
"""Synchronize HITL interactive tool names across hook configurations with the SSOT.

Validates and synchronizes `.agents/hooks.json` (and related lifecycle hook files)
so that all interactive question matching patterns target the canonical SSOT name
`ask_user_question` defined in `config/hitl_tool_name.py`.

Usage:
    python tools/sync_hitl_tool_name.py --check
    python tools/sync_hitl_tool_name.py --fix
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.hitl_tool_name import (  # noqa: E402
    ASK_USER_QUESTION,
    DEPRECATED_NAMES,
    HOST_RUNTIME_TOOL_NAME,
)

HOOKS_PATH = REPO_ROOT / ".agents" / "hooks.json"


def _sync_match_block(
    match_block: Dict[str, Any], hook_id: str, fix: bool
) -> Tuple[bool, List[str]]:
    changed = False
    issues = []
    tool = match_block.get("tool")
    if tool in DEPRECATED_NAMES:
        issues.append(f"[{hook_id}] match.tool '{tool}' is deprecated -> '{ASK_USER_QUESTION}'")
        if fix:
            match_block["tool"] = ASK_USER_QUESTION
            changed = True

    also_match = match_block.get("also_match", [])
    if isinstance(also_match, list):
        for entry in also_match:
            if isinstance(entry, dict) and entry.get("tool") in DEPRECATED_NAMES:
                old_t = entry["tool"]
                if old_t == HOST_RUNTIME_TOOL_NAME:
                    continue  # Host runtime execution primitive is permitted in also_match
                issues.append(f"[{hook_id}] also_match.tool '{old_t}' is deprecated -> '{ASK_USER_QUESTION}'")
                if fix:
                    entry["tool"] = ASK_USER_QUESTION
                    changed = True

    cmd_patterns = match_block.get("command_patterns", [])
    if isinstance(cmd_patterns, list):
        new_patterns = []
        for p in cmd_patterns:
            if p in DEPRECATED_NAMES:
                issues.append(f"[{hook_id}] command_patterns entry '{p}' is deprecated -> '{ASK_USER_QUESTION}'")
                if fix:
                    new_patterns.append(ASK_USER_QUESTION)
                    changed = True
                else:
                    new_patterns.append(p)
            else:
                new_patterns.append(p)
        if fix:
            deduped = []
            for p in new_patterns:
                if p not in deduped:
                    deduped.append(p)
            match_block["command_patterns"] = deduped

    return changed, issues


def _sync_text_fields(
    hook: Dict[str, Any], hook_id: str, fix: bool
) -> Tuple[bool, List[str]]:
    import re
    changed = False
    issues = []
    for field in ("description", "instructions"):
        val = hook.get(field)
        if isinstance(val, str):
            for dep in DEPRECATED_NAMES:
                pattern = r"\b" + re.escape(dep) + r"\b"
                if re.search(pattern, val):
                    issues.append(f"[{hook_id}] {field} mentions deprecated '{dep}'")
                    if fix:
                        hook[field] = re.sub(pattern, ASK_USER_QUESTION, val)
                        changed = True
    return changed, issues


def sync_hook_dict(hook: Dict[str, Any], fix: bool = False) -> Tuple[bool, List[str]]:
    """Inspect and optionally fix a single hook dictionary.

    Returns (was_changed_or_needs_change, list_of_issues).
    """
    issues = []
    hook_id = hook.get("id", "unknown_hook")
    changed = False

    match_block = hook.get("match")
    if isinstance(match_block, dict):
        m_changed, m_issues = _sync_match_block(match_block, hook_id, fix)
        changed = changed or m_changed
        issues.extend(m_issues)

    t_changed, t_issues = _sync_text_fields(hook, hook_id, fix)
    changed = changed or t_changed
    issues.extend(t_issues)

    return changed, issues


def synchronize_hooks(
    hooks_file: Path = HOOKS_PATH, fix: bool = False
) -> Tuple[int, List[str]]:
    """Synchronize hooks file with SSOT tool name.

    Returns (exit_code, report_messages).
    """
    if not hooks_file.exists():
        return 1, [f"Hooks file not found: {hooks_file}"]

    try:
        content = hooks_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return 1, [f"Failed to read/parse {hooks_file}: {e}"]

    hooks = data.get("hooks", [])
    all_issues = []
    file_modified = False

    for hook in hooks:
        if isinstance(hook, dict):
            changed, issues = sync_hook_dict(hook, fix=fix)
            if changed:
                file_modified = True
            all_issues.extend(issues)

    if fix and file_modified:
        try:
            hooks_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            all_issues.append(f"Successfully synchronized {hooks_file} to SSOT '{ASK_USER_QUESTION}'.")
        except Exception as e:
            return 1, [f"Failed to write updated hooks file: {e}"]

    exit_code = 0 if (fix or not all_issues) else 1
    return exit_code, all_issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize hooks configuration with SSOT interactive tool name."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether hooks file matches SSOT without making changes.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Modify hooks file in place to synchronize with SSOT.",
    )
    parser.add_argument(
        "--hooks-path",
        type=Path,
        default=HOOKS_PATH,
        help="Path to hooks.json file (defaults to .agents/hooks.json).",
    )

    args = parser.parse_args()

    if not args.check and not args.fix:
        parser.print_help()
        print("\nError: Must specify either --check or --fix.")
        return 2

    rc, messages = synchronize_hooks(hooks_file=args.hooks_path, fix=args.fix)

    for msg in messages:
        print(msg)

    if rc == 0:
        if args.check:
            print(f"✅ All hook configurations are in sync with SSOT tool name '{ASK_USER_QUESTION}'.")
        else:
            print("✅ Synchronization complete.")
    else:
        print(f"❌ Hook configurations are out of sync with SSOT tool name '{ASK_USER_QUESTION}'.")

    return rc


if __name__ == "__main__":
    sys.exit(main())
