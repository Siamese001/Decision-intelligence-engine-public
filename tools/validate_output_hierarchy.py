#!/usr/bin/env python3
"""tools/validate_output_hierarchy.py

Enforces Governed Output Directory Hierarchy: output/<project_name>/<meeting_date>/
or output/<project_name>/<meeting_date>/<meeting_name>/ for multi-meeting dates.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_output_hierarchy_core import (  # noqa: E402
    ALLOWED_ROOT_AUX_DIRS,
    ALLOWED_ROOT_FILES,
    BOILERPLATE_SUBFOLDER_PREFIXES,
    CONTROLLED_SUPPORTING_DIRS,
    CONTROLLED_SUPPORTING_FILES,
    DATE_FOLDER_REGEX,
    KNOWN_PROJECTS,
    LEGACY_UNSCOPED_MULTI_MEETING_DATES,
    PROHIBITED_CACHE_RECEIPTS,
    PROHIBITED_EXTENSIONS,
    PROJECT_AFFINITY_MAP,
    PROJECT_DIR_REGEX,
    _extract_date_token,
    _extract_stem,
    _is_override_authorized,
    _validate_meeting_container,
    _validate_project_affinity,
    detect_meeting_duplicates,
    validate_transcript_cache,
)

__all__ = [
    "ALLOWED_ROOT_AUX_DIRS",
    "ALLOWED_ROOT_FILES",
    "BOILERPLATE_SUBFOLDER_PREFIXES",
    "CONTROLLED_SUPPORTING_DIRS",
    "CONTROLLED_SUPPORTING_FILES",
    "DATE_FOLDER_REGEX",
    "KNOWN_PROJECTS",
    "LEGACY_UNSCOPED_MULTI_MEETING_DATES",
    "PROHIBITED_CACHE_RECEIPTS",
    "PROHIBITED_EXTENSIONS",
    "PROJECT_AFFINITY_MAP",
    "PROJECT_DIR_REGEX",
    "_extract_date_token",
    "_extract_stem",
    "_is_override_authorized",
    "_validate_meeting_container",
    "_validate_project_affinity",
    "detect_meeting_duplicates",
    "validate_meeting_directory",
    "validate_output_hierarchy",
    "validate_staged_output_hierarchy",
    "validate_transcript_cache",
]


def validate_meeting_directory(
    date_dir: Path,
    project_name: Optional[str] = None,
    allow_active_recording: bool = False,
    target_slug: Optional[str] = None,
) -> List[str]:
    """Hermetically validates a meeting date folder or its multi-meeting subfolders."""
    violations: List[str] = []
    if not date_dir.exists():
        return [f"[MISSING_DIRECTORY] Meeting directory does not exist: {date_dir}"]
    if not date_dir.is_dir():
        return [f"[NOT_A_DIRECTORY] Meeting path is not a directory: {date_dir}"]

    proj_name = project_name or date_dir.parent.name
    meeting_date = date_dir.name

    if not DATE_FOLDER_REGEX.match(meeting_date):
        if DATE_FOLDER_REGEX.match(date_dir.parent.name):
            return validate_meeting_directory(
                date_dir.parent,
                project_name=project_name or date_dir.parent.parent.name,
                allow_active_recording=allow_active_recording,
                target_slug=target_slug or meeting_date,
            )
        return [f"[INVALID_DATE_FORMAT] Subfolder '{meeting_date}' in project '{proj_name}' does not match MM-DD-YYYY."]

    if not [p for p in date_dir.iterdir() if p.name != ".DS_Store"]:
        with contextlib.suppress(Exception):
            for p in date_dir.iterdir():
                p.unlink()
            date_dir.rmdir()
        return []

    if proj_name == "project_nexus" and meeting_date == "09-12-2026":
        return [f"[SYNTHETIC_FIXTURE_POLLUTION] Hallucinated fixture directory '{proj_name}/{meeting_date}' prohibited."]

    meeting_subfolders = [
        s for s in sorted(date_dir.iterdir())
        if s.is_dir() and not s.name.startswith(".") and s.name not in CONTROLLED_SUPPORTING_DIRS
    ]
    root_arts = {
        k: [p for p in date_dir.glob(f"{k}_*") if p.name.endswith(ext)]
        for k, ext in (("01", ".md"), ("02", ".md"), ("03", ".json"))
    }
    is_cat = (date_dir.parent.name != proj_name)
    date_key = f"{proj_name}/{date_dir.parent.name}/{meeting_date}" if is_cat else f"{proj_name}/{meeting_date}"
    is_legacy = (not is_cat and date_key in LEGACY_UNSCOPED_MULTI_MEETING_DATES)

    for p in date_dir.iterdir():
        if p.name == ".cache":
            violations.append(f"[PROHIBITED_CACHE_DIRECTORY] Prohibited hidden .cache directory in '{date_key}'.")
    for sub in meeting_subfolders:
        if (sub / ".cache").exists():
            violations.append(f"[PROHIBITED_CACHE_DIRECTORY] Prohibited hidden .cache directory in '{date_key}/{sub.name}'.")

    if meeting_subfolders and not is_legacy:
        if not target_slug and any(root_arts.values()):
            violations.append(
                f"[MIXED_DATE_CONTAINER] Date directory '{date_key}' contains both meeting subfolders "
                f"{[s.name for s in meeting_subfolders]} and loose deliverable files at root."
            )
        # Duplicate meeting collision detection
        violations.extend(detect_meeting_duplicates(meeting_subfolders, proj_name, meeting_date, date_dir, target_slug=target_slug))

        if target_slug:
            matching_sub = [s for s in meeting_subfolders if s.name == target_slug or s.name.lower() == target_slug.lower()]
            if not matching_sub:
                violations.append(f"[MISSING_TARGET_SUBFOLDER] Target meeting subfolder '{target_slug}' not found in '{date_key}'.")
                return violations

        for sub in meeting_subfolders:
            if target_slug and sub.name != target_slug and sub.name.lower() != target_slug.lower():
                continue
            if sub.name.lower().endswith(("_draft", " draft", "_temp", " temp")) or sub.name.lower() in ("draft", "temp", "tmp", "scratch"):
                violations.append(f"[PROHIBITED_DRAFT_DIRECTORY] Prohibited draft/temp subfolder '{sub.name}' in '{date_key}'.")
                continue
            if len(sub.name) > 40:
                violations.append(f"[OVERSIZED_MEETING_NAME] Subfolder '{sub.name}' in '{date_key}' exceeds 40 chars ({len(sub.name)} chars).")
            if any(sub.name.lower().startswith(bp) for bp in BOILERPLATE_SUBFOLDER_PREFIXES):
                violations.append(f"[BOILERPLATE_MEETING_NAME] Subfolder '{sub.name}' in '{date_key}' contains generic boilerplate prefix.")
            sub_aff_err = _validate_project_affinity(sub.name, proj_name, f"{date_key}/{sub.name}")
            if sub_aff_err:
                violations.append(sub_aff_err)
            violations.extend(
                _validate_meeting_container(
                    sub,
                    proj_name=proj_name,
                    meeting_date=meeting_date,
                    container_label=f"{date_key}/{sub.name}",
                    allow_active_recording=allow_active_recording,
                )
            )
        return violations

    distinct_stems = {_extract_stem(f.name) for f in root_arts["01"]}
    if len(distinct_stems) > 1 and not is_legacy:
        violations.append(
            f"[MULTI_MEETING_ROOT_COLLISION] Multiple meeting sessions ({len(distinct_stems)}) detected loose at date root '{date_key}': {sorted(distinct_stems)}. Meetings on same date must reside in dedicated subfolders."
        )

    all_non_hidden = [p for p in date_dir.iterdir() if not p.name.startswith(".") and p.name != ".DS_Store"]
    if all_non_hidden and all(p.is_dir() and p.name in CONTROLLED_SUPPORTING_DIRS for p in all_non_hidden):
        return violations

    violations.extend(
        _validate_meeting_container(
            date_dir,
            proj_name=proj_name,
            meeting_date=meeting_date,
            container_label=date_key,
            allow_active_recording=allow_active_recording,
        )
    )
    return violations


def validate_output_hierarchy(
    output_dir: Path,
    project: Optional[str] = None,
    allow_active_recording: bool = True,
) -> List[str]:
    """Validates the entire output/ directory hierarchy across all canonical projects."""
    violations: List[str] = []
    if not output_dir.exists() or not output_dir.is_dir():
        return [f"Output path does not exist or is not a directory: {output_dir}"]

    for entry in sorted(output_dir.iterdir()):
        if entry.is_symlink():
            violations.append(f"[ROOT_SYMLINK] Disallowed symlink at output root: {entry.name} -> {os.readlink(entry)}")
            continue
        if entry.is_file() and entry.name not in ALLOWED_ROOT_FILES:
            violations.append(f"[ROOT_FILE_DUMP] Disallowed file at output root: {entry.name}")
            continue
        if entry.is_dir():
            if "draft" in entry.name.lower() or "temp" in entry.name.lower():
                violations.append(f"[PROHIBITED_DRAFT_DIRECTORY] Prohibited draft/temp directory '{entry.name}' at output root.")
                continue
            if entry.name == "transcript_cache":
                violations.extend(validate_transcript_cache(entry))
                continue
            if entry.name in ALLOWED_ROOT_AUX_DIRS:
                continue
            if not PROJECT_DIR_REGEX.match(entry.name) or entry.name not in KNOWN_PROJECTS:
                violations.append(f"[INVALID_PROJECT_NAME] Unknown project folder '{entry.name}' at output root.")
                continue

            project_dir = entry
            if project and project_dir.name != project:
                continue

            for sub in sorted(project_dir.iterdir()):
                if sub.name == ".DS_Store":
                    continue
                if sub.is_symlink():
                    violations.append(f"[PROJECT_SYMLINK] Disallowed symlink in project '{project_dir.name}': {sub.name}")
                elif sub.is_file():
                    violations.append(f"[FLAT_PROJECT_FILE] Disallowed file directly under project '{project_dir.name}': {sub.name}.")
                elif sub.is_dir():
                    if sub.name.lower().endswith(("_draft", " draft", "_temp", " temp")) or sub.name.lower() in ("draft", "temp", "tmp", "scratch"):
                        violations.append(f"[PROHIBITED_DRAFT_DIRECTORY] Prohibited draft/temp folder '{sub.name}' in project '{project_dir.name}'.")
                        continue
                    is_cat = not DATE_FOLDER_REGEX.match(sub.name) and any(c.is_dir() and DATE_FOLDER_REGEX.match(c.name) for c in sub.iterdir())
                    targets = [c for c in sorted(sub.iterdir()) if c.name != ".DS_Store" and not c.name.startswith(".") and c.is_dir()] if is_cat else [sub]
                    for t in targets:
                        violations.extend(
                            validate_meeting_directory(
                                t,
                                project_name=project_dir.name,
                                allow_active_recording=allow_active_recording,
                            )
                        )
    return violations


def validate_staged_output_hierarchy(output_dir: Path) -> List[str]:
    """Validates only output directories that contain git-staged changes."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True,
        )
        staged_lines = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("output/")]
    except Exception:
        return []
    if not staged_lines:
        return []

    target_dates: set[Path] = set()
    staged_subfolders: set[Path] = set()
    for rel in staged_lines:
        file_path = output_dir.parent / rel
        curr = file_path.parent
        while curr != output_dir and curr != curr.parent:
            if DATE_FOLDER_REGEX.match(curr.name):
                target_dates.add(curr)
                if file_path.parent != curr:
                    sub = file_path.parent
                    while sub.parent != curr and sub != curr:
                        sub = sub.parent
                    if sub.parent == curr:
                        staged_subfolders.add(sub)
                break
            curr = curr.parent

    violations: List[str] = []
    for d in sorted(target_dates):
        with contextlib.suppress(Exception):
            proj_name = d.relative_to(output_dir).parts[0]
            subs = [p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != ".DS_Store" and p.name not in CONTROLLED_SUPPORTING_DIRS]
            if subs and staged_subfolders:
                violations.extend(detect_meeting_duplicates(subs, proj_name, d.name, d))
                for sub in sorted(subs):
                    if sub in staged_subfolders:
                        violations.extend(_validate_meeting_container(sub, proj_name=proj_name, meeting_date=d.name, container_label=f"{proj_name}/{d.name}/{sub.name}"))
            else:
                violations.extend(validate_meeting_directory(d, project_name=proj_name))
    return violations


def validate_target_files_output_hierarchy(output_dir: Path, target_files: Sequence[str | Path]) -> List[str]:
    """Validates only output directories that contain specified target files."""
    output_lines = [
        str(f).replace("\\", "/")
        for f in target_files
        if str(f).replace("\\", "/").startswith("output/")
        or (output_dir in Path(f).resolve().parents)
    ]
    if not output_lines:
        return []

    target_dates: set[Path] = set()
    staged_subfolders: set[Path] = set()
    for rel in output_lines:
        file_path = output_dir.parent / rel if not Path(rel).is_absolute() else Path(rel)
        curr = file_path.parent
        date_cand: Optional[Path] = None
        while curr != output_dir and curr != curr.parent:
            if curr.parent == output_dir:
                pass
            elif curr.parent.parent == output_dir:
                date_cand = curr
            if DATE_FOLDER_REGEX.match(curr.name):
                target_dates.add(curr)
                if file_path.parent != curr:
                    sub = file_path.parent
                    while sub.parent != curr and sub != curr:
                        sub = sub.parent
                    if sub.parent == curr:
                        staged_subfolders.add(sub)
                break
            curr = curr.parent
        if date_cand is not None and not DATE_FOLDER_REGEX.match(date_cand.name):
            target_dates.add(date_cand)

    violations: List[str] = []
    for d in sorted(target_dates):
        with contextlib.suppress(Exception):
            proj_name = d.relative_to(output_dir).parts[0]
            subs = [
                p
                for p in d.iterdir()
                if p.is_dir()
                and not p.name.startswith(".")
                and p.name != ".DS_Store"
                and p.name not in CONTROLLED_SUPPORTING_DIRS
            ]
            if subs and staged_subfolders:
                violations.extend(detect_meeting_duplicates(subs, proj_name, d.name, d))
                for sub in sorted(subs):
                    if sub in staged_subfolders:
                        violations.extend(
                            _validate_meeting_container(
                                sub,
                                proj_name=proj_name,
                                meeting_date=d.name,
                                container_label=f"{proj_name}/{d.name}/{sub.name}",
                            )
                        )
            else:
                violations.extend(validate_meeting_directory(d, project_name=proj_name))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate governed output folder hierarchy")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent.parent / "output")
    parser.add_argument("--project", type=str, default=None)
    parser.add_argument("--staged", action="store_true", help="Only validate output folders containing staged git changes")
    parser.add_argument("--check", action="store_true", default=True)
    parser.add_argument("files", nargs="*", help="Optional target files to restrict validation scope")
    args = parser.parse_args()

    if args.files:
        violations = validate_target_files_output_hierarchy(args.output_dir, args.files)
    elif args.staged:
        violations = validate_staged_output_hierarchy(args.output_dir)
    else:
        violations = validate_output_hierarchy(args.output_dir, project=args.project)

    if violations:
        print(
            f"❌ Output directory hierarchy validation FAILED with {len(violations)} violation(s):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
        return 1
    print(f"✅ Output directory hierarchy validation PASSED ({'target files mode' if args.files else ('staged mode' if args.staged else str(args.output_dir))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
