"""Representative candidate diff generators across documentation, governance, and application."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class CandidateDiff:
    workload: str
    is_defect: bool
    rel_path: str
    content: str
    commit_message: str


def get_candidate(workload: str, is_defect: bool = False, repair: bool = False) -> CandidateDiff:
    tag = "repaired" if repair else ("defect" if is_defect else "clean")

    if workload == "documentation":
        rel_path = f"docs/candidate_{tag}.md"
        if is_defect and not repair:
            content = ""  # Defect: empty doc
            commit_msg = "bad commit message without conventional format"
        else:
            content = (
                f"# Candidate Documentation ({tag.title()})\n\n"
                "## Overview\n"
                f"Verified architectural documentation for {tag} scenario.\n"
            )
            commit_msg = f"docs(benchmark): add {tag} documentation candidate"

    elif workload == "governance":
        rel_path = f"config/governance/candidate_{tag}_policy.json"
        if is_defect and not repair:
            content = "{\n  \"policy\": \"invalid_json\",,\n}"  # Syntax error
            commit_msg = f"chore(governance): add defective {tag} policy"
        else:
            content = json.dumps(
                {
                    "policy_name": f"candidate_{tag}_policy",
                    "version": "1.0.0",
                    "mode": tag,
                    "strict_enforcement": True,
                },
                indent=2,
            )
            commit_msg = f"chore(governance): add valid {tag} policy"

    else:  # application
        rel_path = f"dual_pipeline/candidate_{tag}_runtime.py"
        if is_defect and not repair:
            content = "def execute_task(\n    broken syntax here\n"  # Syntax error
            commit_msg = f"feat(runtime): add defective {tag} module"
        else:
            content = (
                f'"""Candidate runtime module for {tag}."""\n\n'
                "def execute_task() -> bool:\n"
                "    return True\n"
            )
            commit_msg = f"feat(runtime): add valid {tag} runtime module"

    return CandidateDiff(
        workload=workload,
        is_defect=is_defect and not repair,
        rel_path=rel_path,
        content=content,
        commit_message=commit_msg,
    )
