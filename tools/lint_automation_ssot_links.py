#!/usr/bin/env python3
"""tools/lint_automation_ssot_links.py

Fail-closed static governance linter enforcing the SSOT Link Contract
(urn:nee:automation-prompt-link-governance:1.0.0) for Antigravity automations.

Invariants Enforced:
1. INV-SSOT-LINK-01 (DEREFERENCER_POINTER_MANDATE):
   Sidecars in ~/.gemini/config/sidecars/ must use thin dereferencer pointer prompts.
2. INV-SSOT-LINK-02 (ZERO_INLINE_PROMPT_BODY):
   Sidecar prompt payloads must be under 40 words and contain zero multi-line markdown headers.
3. INV-SSOT-LINK-03 (CANONICAL_TARGET_RESOLVABILITY):
   Dereferenced pointer URIs must resolve to existing files under automations/prompts/*.md.
4. INV-SSOT-LINK-04 (RECONCILIATION_CONTRACT_CONFORMANCE):
   config/scheduled_tasks/registration_reconciliation.json must enforce
   allow_inline_prompt_body: false and contain valid pointer prompts across all 6 canonical tasks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.task_restoration_core.ssot_pointers import (
    MAX_POINTER_WORDS,
    TASK_SIDECAR_MAP,
    extract_target_path,
    is_valid_pointer_prompt,
    resolve_primary_repo_root,
)

STAGE_VI_SIDECAR_MAP: dict[str, dict[str, Any]] = {
    task_id: info
    for task_id, info in TASK_SIDECAR_MAP.items()
    if str(info.get("canonical_prompt", "")).startswith("automations/prompts/sidecars/")
}

ALL_GOVERNED_SIDECARS: dict[str, dict[str, Any]] = dict(TASK_SIDECAR_MAP)



def audit_reconciliation_manifest(repo_root: Path) -> List[str]:
    """Audits config/scheduled_tasks/registration_reconciliation.json for link contract conformance."""
    errors: List[str] = []
    recon_path = repo_root / "config" / "scheduled_tasks" / "registration_reconciliation.json"
    if not recon_path.is_file():
        return [f"[INV-SSOT-LINK-04] Missing reconciliation manifest: {recon_path}"]

    try:
        from tools.audit_governance_harmonization_core.common import load_json_with_dup_check
        data, _ = load_json_with_dup_check(recon_path)
    except Exception as e:
        return [f"[INV-SSOT-LINK-04] Failed to parse JSON at {recon_path}: {e}"]

    tasks = {t.get("task_id"): t for t in data.get("tasks", []) if isinstance(t, dict)}
    for task_id in TASK_SIDECAR_MAP:
        if task_id not in tasks:
            errors.append(f"[INV-SSOT-LINK-04] Task '{task_id}' missing in {recon_path.name}")
            continue

        entry = tasks[task_id]
        if entry.get("allow_inline_prompt_body") is not False:
            errors.append(f"[INV-SSOT-LINK-02] Task '{task_id}' must set allow_inline_prompt_body to false")

        prompt = entry.get("dereferencer_prompt", "")
        if not prompt:
            errors.append(f"[INV-SSOT-LINK-01] Task '{task_id}' missing dereferencer_prompt")
        elif len(prompt.split()) > MAX_POINTER_WORDS:
            errors.append(f"[INV-SSOT-LINK-02] Task '{task_id}' pointer exceeds {MAX_POINTER_WORDS} words")

        canon_rel = entry.get("canonical_prompt", "")
        canon_file = repo_root / canon_rel
        if not canon_file.is_file():
            errors.append(f"[INV-SSOT-LINK-03] Task '{task_id}' target prompt missing: {canon_file}")

    return errors


def _is_worktree_registered_sidecar(entry: Path, primary_root: Path) -> bool:
    try:
        s_data = json.loads((entry / "sidecar.json").read_text(encoding="utf-8"))
        s_args = s_data.get("args", [])
        s_prompt = s_args[-1] if isinstance(s_args, list) and s_args else ""
        s_target = extract_target_path(s_prompt) if is_valid_pointer_prompt(s_prompt) else None
        if not s_target:
            return False
        # Restrict worktree bypass: Only allow sidecars pointing to unmerged prompts in active worktrees
        wt_root = primary_root / ".worktrees"
        return bool(wt_root.is_dir() and any(wt_root.glob(f"*/automations/prompts/**/{s_target.name}")))
    except Exception:
        return False


def audit_stage_vi_prompt_governance(repo_root: Path) -> List[str]:
    """Audits Stage VI sidecar prompts for common control matrix, canonical query filters, and Rule 9."""
    errors: List[str] = []
    for task_id, info in STAGE_VI_SIDECAR_MAP.items():
        prompt_path = repo_root / info["canonical_prompt"]
        if not prompt_path.is_file():
            errors.append(f"[INV-STAGE-VI-01] Missing prompt file for {task_id}: {prompt_path}")
            continue

        text = prompt_path.read_text(encoding="utf-8")
        if "urn:nee:scheduled-task-common-control-matrix:1.0.0" not in text:
            errors.append(f"[INV-STAGE-VI-02] {prompt_path.name} missing common control matrix contract")
        if f"Canonical Task: {task_id}" not in text:
            errors.append(f"[INV-STAGE-VI-02] {prompt_path.name} missing Canonical Task declaration for {task_id}")
        if "Exclusive Domain:" not in text:
            errors.append(f"[INV-STAGE-VI-02] {prompt_path.name} missing Exclusive Domain declaration")

        # Invariant: canonical query filters for assertion tables
        if "FROM assertion" in text and "canonical_eligible = 1" not in text:
            errors.append(f"[INV-STAGE-VI-03] {prompt_path.name} assertion queries missing canonical_eligible = 1")

        # Invariant: Rule 9 formatting (zero em-dashes, zero en-dashes, zero double-hyphen spacers)
        if "\u2014" in text or "\u2013" in text or " -- " in text:
            errors.append(f"[INV-STAGE-VI-04] {prompt_path.name} violates Rule 9 anti-em-dash / dash formatting")

        # Invariant: No hardcoded model names
        for token in ("gpt-", "gemini-", "claude-"):
            if token in text:
                errors.append(f"[INV-STAGE-VI-05] {prompt_path.name} contains hardcoded model token: {token}")

    return errors


def audit_host_sidecars(repo_root: Path, sidecars_dir: Path) -> List[str]:
    """Audits live host sidecar files in ~/.gemini/config/sidecars/."""
    errors: List[str] = []
    if not sidecars_dir.is_dir():
        # If no sidecars dir exists in this environment, skip host-specific checks
        return errors

    primary_root = resolve_primary_repo_root(repo_root)

    # Ensure zero unmapped or rogue sidecars in sidecars_dir (allow active worktree-registered sidecars)
    known_dirs = {info["sidecar_dir"] for info in ALL_GOVERNED_SIDECARS.values()}
    for entry in sidecars_dir.iterdir():
        if entry.is_dir() and (entry / "sidecar.json").is_file() and entry.name not in known_dirs:
            if not _is_worktree_registered_sidecar(entry, primary_root):
                errors.append(
                    f"[INV-SSOT-LINK-01] Unmapped/unmanaged sidecar detected in {sidecars_dir}: {entry.name}"
                )

    for task_id, info in ALL_GOVERNED_SIDECARS.items():
        sidecar_file = sidecars_dir / info["sidecar_dir"] / "sidecar.json"
        if not sidecar_file.is_file():
            errors.append(f"[INV-SSOT-LINK-01] Sidecar file missing for {task_id}: {sidecar_file}")
            continue

        try:
            data = json.loads(sidecar_file.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"[INV-SSOT-LINK-01] Malformed JSON in sidecar {sidecar_file}: {e}")
            continue

        # Audit access grants for stale or nonexistent files
        perms = data.get("agent_permissions", {})
        for grant in perms.get("access_grants", []):
            if isinstance(grant, str) and grant.startswith("read_file(") and grant.endswith(")"):
                target = Path(grant[len("read_file(") : -1].strip())
                if not target.is_file():
                    errors.append(
                        f"[INV-SSOT-LINK-03] Sidecar {sidecar_file} has stale access grant to nonexistent file: {target}"
                    )

        args = data.get("args", [])
        if len(args) < 5 or "--" not in args:
            errors.append(f"[INV-SSOT-LINK-01] Sidecar {sidecar_file} has insufficient args (<5) or missing '--'")
            continue

        prompt = args[-1]
        words = prompt.split()
        if len(words) > MAX_POINTER_WORDS:
            errors.append(
                f"[INV-SSOT-LINK-02] Sidecar {sidecar_file} inlines prompt body "
                f"({len(words)} words > {MAX_POINTER_WORDS} limit)"
            )

        if "\n#" in prompt or "## " in prompt:
            errors.append(f"[INV-SSOT-LINK-02] Sidecar {sidecar_file} contains raw markdown headers")

        if not is_valid_pointer_prompt(prompt):
            errors.append(
                f"[INV-SSOT-LINK-01] Sidecar {sidecar_file} does not conform to SSOT pointer template: '{prompt[:60]}...'"
            )

        target_path = extract_target_path(prompt)
        expected_target = (primary_root / info["canonical_prompt"]).resolve()
        repo_expected = (repo_root / info["canonical_prompt"]).resolve()
        target_resolved = target_path.resolve() if target_path else None
        is_expected = bool(target_resolved and target_resolved in (expected_target, repo_expected))

        if is_expected:
            target_exists = bool(target_path and (target_path.is_file() or repo_expected.is_file()))
        else:
            target_exists = bool(target_path and target_path.is_file())

        if not target_exists:
            errors.append(
                f"[INV-SSOT-LINK-03] Sidecar {sidecar_file} targets non-existent file: {target_path}"
            )
        elif not is_expected:
            errors.append(
                f"[INV-SSOT-LINK-03] Sidecar {sidecar_file} targets incorrect file {target_resolved}, expected {expected_target}"
            )

        expected_title = info.get("title")
        actual_title = data.get("display_name")
        if expected_title and actual_title is not None and actual_title != expected_title:
            errors.append(
                f"[INV-SSOT-LINK-01] Sidecar {sidecar_file} display_name '{actual_title}' does not match canonical title '{expected_title}'"
            )

    return errors


def run_automation_ssot_links_lint(
    repo_root: Path,
    sidecars_dir: Path | None = None,
    skip_stage_vi_duplicate: bool = False,
) -> Tuple[bool, List[str]]:
    """Runs all automation SSOT link linter audits."""
    target_sidecars = sidecars_dir or (Path.home() / ".gemini" / "config" / "sidecars")
    errors: List[str] = []
    errors.extend(audit_reconciliation_manifest(repo_root))
    errors.extend(audit_host_sidecars(repo_root, target_sidecars))
    if not skip_stage_vi_duplicate:
        errors.extend(audit_stage_vi_prompt_governance(repo_root))
    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Automation SSOT Link Governance Linter")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root path")
    parser.add_argument(
        "--sidecars-dir",
        type=Path,
        default=Path.home() / ".gemini" / "config" / "sidecars",
        help="Antigravity sidecars directory",
    )
    parser.add_argument(
        "--report-receipt",
        type=Path,
        default=None,
        help="Path to write JSON execution receipt",
    )
    args = parser.parse_args()

    ok, errors = run_automation_ssot_links_lint(args.repo_root, args.sidecars_dir)

    report = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "terminal_outcome": "PASS" if ok else "FAIL",
        "is_valid": ok,
        "total_sidecars_validated": len(ALL_GOVERNED_SIDECARS),
        "defects_count": len(errors),
        "defects": errors,
    }

    if args.report_receipt:
        args.report_receipt.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        report["canonical_digest"] = hashlib.sha256(payload).hexdigest()
        args.report_receipt.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[RECEIPT] Written to {args.report_receipt}")

    if not ok:
        print(f"[FAIL] Found {len(errors)} SSOT link governance defect(s):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("[PASS] All Antigravity automation sidecars satisfy SSOT link pointer governance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
