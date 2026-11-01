#!/usr/bin/env python3
"""
tools/remediate_script_credentials.py

Automated AST & regex refactoring tool to migrate scripts from raw
`load_dotenv(ROOT / "env_di")` and bare `OpenAI()` to governed `CredentialManager`.

Enforces AGENTS.md Invariant 4 (SSOT Credential Isolation).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]


def remediate_script(path: Path) -> Tuple[bool, List[str]]:
    """Inspects and refactors a script to use CredentialManager."""
    content = path.read_text(encoding="utf-8")
    changes: List[str] = []

    # 1. Replace load_dotenv(ROOT / "env_di") or load_dotenv(WORKSPACE_ROOT / "env_di")
    if "load_dotenv" in content and "env_di" in content:
        # Check if CredentialManager is imported
        if "from config.credentials import CredentialManager" not in content and "from storage.credentials import CredentialManager" not in content:
            # Insert import
            content = "from config.credentials import CredentialManager\n" + content
            changes.append("Added CredentialManager import")

        content = re.sub(
            r"load_dotenv\([^)]*env_di[^)]*\)",
            "CredentialManager.ensure_ssot_env_loaded()",
            content,
        )
        changes.append("Replaced raw load_dotenv with CredentialManager.ensure_ssot_env_loaded()")

    # 2. Replace client = OpenAI() with CredentialManager.get_openai_client()
    if re.search(r"client\s*=\s*OpenAI\(\s*\)", content):
        if "from config.credentials import CredentialManager" not in content and "from storage.credentials import CredentialManager" not in content:
            content = "from config.credentials import CredentialManager\n" + content
            changes.append("Added CredentialManager import")

        content = re.sub(
            r"client\s*=\s*OpenAI\(\s*\)",
            "client, _ = CredentialManager.get_openai_client(allow_mock=False)",
            content,
        )
        changes.append("Replaced bare OpenAI() with CredentialManager.get_openai_client(allow_mock=False)")

    if changes:
        path.write_text(content, encoding="utf-8")
        return True, changes
    return False, []


def main() -> int:
    parser = argparse.ArgumentParser(description="Remediate scripts to use CredentialManager.")
    args = parser.parse_args()

    scripts_dir = ROOT / "scripts"
    modified = 0

    print("Scanning scripts/ for unmanaged credential initialization...")
    for p in sorted(scripts_dir.glob("*.py")):
        changed, notes = remediate_script(p)
        if changed:
            modified += 1
            print(f"[REMEDIATED] {p.name}: {', '.join(notes)}")

    print(f"\nRemediation complete. Updated {modified} script(s).")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
