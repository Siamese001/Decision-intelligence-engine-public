"""
Domain-aware container and ephemeral artifact triage helpers for PR governance.
Keeps tools/pr_governance_core/conflict_triage.py within 400 lines and low cognitive complexity.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence, Set

from tools.pr_governance_core.common import run_git


def is_ephemeral_receipt_or_scratch(path: str) -> bool:
    """Returns True if path is a dry-run receipt, test report, or temporary scratch document."""
    p_lower = path.lower()
    return (
        p_lower.startswith(("reports/", "docs/plans/"))
        or "/referenced_artifacts/" in p_lower
        or any(
            part.endswith(("_draft", " draft", "_temp", " temp")) or part in ("draft", "temp", "tmp", "scratch")
            for part in Path(path).parts
        )
    )


def extract_meeting_containers(files: Sequence[str]) -> Set[str]:
    """Extract output/<project>/<date>/<meeting> container directory prefixes."""
    date_regex = re.compile(r"^(?:0?[1-9]|1[0-2])[-.](?:0?[1-9]|[12]\d|3[01])[-.](?:19|20)\d{2}")
    containers: Set[str] = set()
    for f in files:
        if not f.startswith("output/"):
            continue
        parts = Path(f).parts
        for idx, part in enumerate(parts):
            if date_regex.match(part):
                if len(parts) > idx + 1 and not parts[idx + 1].startswith(
                    ("01_", "02_", "03_", "qa_", "llm_", "final_", "pipeline_", "intake_")
                ):
                    containers.add("/".join(parts[: idx + 2]))
                else:
                    containers.add("/".join(parts[: idx + 1]))
                break
    return containers


def _matches_indexed_main_meeting(container: str, root: Path) -> bool:
    """Checks whether container date, project, and tokens match a PACKAGE_READY deliverable on main."""
    from tools.pr_governance_core.deliverable_index import (
        _extract_meeting_tokens,
        _matches_canonical_entry,
        _normalize_date_str,
        build_canonical_main_deliverable_index,
    )

    date_str = _normalize_date_str(container)
    if not date_str:
        return False
    parts = Path(container).parts
    project = parts[1].lower() if len(parts) >= 3 and parts[0] == "output" else ""
    clues = {
        "dates": {date_str},
        "projects": {project} if project else set(),
        "meeting_names": set(),
        "directives": set(),
        "tokens": _extract_meeting_tokens(container),
    }
    return any(_matches_canonical_entry(clues, entry) for entry in build_canonical_main_deliverable_index(root))


def is_meeting_container_sealed_on_main(container: str, root: Path) -> bool:
    """Verifies that main contains the sealed triad (01 transcript, 02 recap, 03 signal projection) or Output-03 document release for a container."""
    main_files = run_git(["ls-tree", "-r", "--name-only", "main", "--", container], cwd=root, check=False).splitlines()
    if any("01_" in f for f in main_files) and any("02_" in f for f in main_files) and any("03_" in f for f in main_files):
        return True
    if any("03_" in f for f in main_files) and any(
        f.endswith(("final_release_decision.json", "pipeline_execution_receipt.json", "publication_coverage_receipt.json"))
        for f in main_files
    ):
        return True
    return _matches_indexed_main_meeting(container, root)


def _is_stash_file_superseded(path: str, root: Path) -> bool:
    """Returns True if a stashed file path is ephemeral or belongs to a sealed meeting container on main."""
    if is_ephemeral_receipt_or_scratch(path):
        return True
    containers = extract_meeting_containers([path])
    return bool(containers and all(is_meeting_container_sealed_on_main(c, root) for c in containers))


def are_branch_stashes_superseded_on_main(branch: str, root: Path) -> bool:
    """Returns True when all git stash entries on branch contain exclusively superseded meeting/ephemeral files."""
    if not branch:
        return False
    stash_lines = run_git(["stash", "list", "--format=%gd::%gs"], cwd=root, check=False).splitlines()
    prefixes = (f"On {branch}:", f"WIP on {branch}:")
    refs = [ln.split("::", 1)[0].strip() for ln in stash_lines if "::" in ln and ln.split("::", 1)[1].strip().startswith(prefixes)]
    if not refs:
        return False
    for ref in refs:
        files = [f.strip() for f in run_git(["stash", "show", "-u", "--name-only", ref], cwd=root, check=False).splitlines() if f.strip()]
        if not files or not all(_is_stash_file_superseded(f, root) for f in files):
            return False
    return True


def filter_topology_absorbed_files(
    branch_files: Sequence[str],
    root: Path,
    absorbed_input: Sequence[str],
    salvageable_input: Sequence[str],
    in_main_pcts: dict[str, float],
) -> tuple[list[str], list[str], dict[str, float]]:
    """Applies container supersession and ephemeral receipt filtering to topology results."""
    branch_containers = extract_meeting_containers(branch_files)
    sealed_containers = {c for c in branch_containers if is_meeting_container_sealed_on_main(c, root)}

    new_absorbed = list(absorbed_input)
    new_salvageable: list[str] = []
    new_pcts = dict(in_main_pcts)

    for f in salvageable_input:
        if any(f.startswith(f"{c}/") or f == c for c in sealed_containers) or is_ephemeral_receipt_or_scratch(f):
            new_pcts[f] = 1.0
            new_absorbed.append(f)
        else:
            new_salvageable.append(f)

    return sorted(set(new_absorbed)), sorted(set(new_salvageable)), new_pcts

