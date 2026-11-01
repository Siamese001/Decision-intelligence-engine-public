#!/usr/bin/env python3
"""
PR Priority Engine (Wave 5).
Classifies branches based on first-principles diff evidence, machine-readable rubric v2,
honors time-boxed registry overrides with strict expiration gating, and produces verified rank receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import pr_priority_facts as facts  # noqa: E402
from tools.pr_priority_facts import match_path_globs  # noqa: E402,F401  (re-export)

CATEGORIES = (*facts.CATEGORY_ORDER, facts.UNCLASSIFIED)


def load_rubric(rubric_path: Optional[Path] = None) -> Dict[str, Any]:
    target = rubric_path or (ROOT / "config" / "pr_priority_rubric.json")
    if not target.exists():
        return {}
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def load_registry(registry_path: Optional[Path] = None) -> Dict[str, Any]:
    target = registry_path or (ROOT / "config" / "pr_priority_registry.json")
    if not target.exists():
        return {}
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def classify_path(file_path: str, rules: Optional[Dict[str, List[str]]] = None) -> str:
    if not rules:
        rules = load_rubric().get("classification_rules", {})
    return facts.classify_path(file_path, rules)

def is_valid_override(override_data: Any, today_str: Optional[str] = None) -> Tuple[bool, str]:
    if isinstance(override_data, str):
        if override_data.upper() in ("P0", "P1", "P2", "P3", "TOMBSTONE", "D"):
            return True, "valid legacy string override"
        return False, "invalid tier string"
    if not isinstance(override_data, dict):
        return False, "not a dictionary"
    req_keys = ("tier", "owner", "expires_at") if override_data.get("tier") == "TOMBSTONE" else ("tier", "owner", "scope", "expires_at")
    for k in req_keys:
        if not override_data.get(k):
            return False, f"missing {k}"
    if not (override_data.get("reason") or override_data.get("rationale")):
        return False, "missing reason"
    try:
        exp_date = datetime.strptime(override_data["expires_at"], "%Y-%m-%d").date()
    except Exception:
        return False, "invalid expires_at format"
    cur_date = datetime.strptime(today_str, "%Y-%m-%d").date() if today_str else datetime.now(timezone.utc).date()
    if exp_date < cur_date:
        return False, f"override expired on {override_data['expires_at']}"
    return True, "valid"


def _git_sync_status(branch: str, repo_root: Path, base_branch: str = "main") -> Tuple[int, int, bool]:
    """Ahead/behind/clean for direct callers that do not supply them (``-1`` = git failure, fail closed)."""
    try:
        from tools.pr_governance_core.common import compute_first_parent_sync
        behind, ahead = compute_first_parent_sync(base_branch, branch, cwd=repo_root)
        clean = subprocess.run(["git", "merge-tree", "--write-tree", base_branch, branch], cwd=repo_root, capture_output=True, text=True, check=False).returncode == 0
        return (ahead, behind, clean) if ahead >= 0 and behind >= 0 else (-1, -1, False)
    except Exception:
        return -1, -1, False


def _calculate_value_score(in_set_prod_loc: int, test_files: List[str], commits: List[Dict[str, str]]) -> float:
    if in_set_prod_loc <= 0:
        return 0.0
    has_e2e = any("e2e" in f or "end_to_end" in f for f in test_files)
    has_adv = any("adversarial" in f or "edge_case" in f for f in test_files)
    has_fix = any(facts.is_conventional_fix(c.get("subject", "")) for c in commits)
    target_re = re.compile(r"\d+(\.\d+)?\s*(%|ms|s|tokens|\$)")
    has_target = any(target_re.search(c.get("body", "") + " " + c.get("subject", "")) for c in commits)
    return math.log2(1 + in_set_prod_loc) + 2.0 * float(has_e2e) + 2.0 * float(has_adv) + 1.0 * float(has_fix) + 1.0 * float(has_target)


def _calculate_risk_score(behind: int, clean_merge: bool, file_count: int, churn: int, prod_loc: int, test_count: int, tier_count: int, test_files: List[str], rubric: Optional[Dict[str, Any]] = None) -> int:
    p = (rubric or load_rubric()).get("scoring", {}).get("risk_penalties", {})
    risk = (p.get("behind_penalty", 3) if behind >= p.get("behind_threshold", 20) else 0) + (p.get("conflict_penalty", 2) if not clean_merge else 0)
    if file_count > p.get("xl_files_threshold", 150) or churn > p.get("xl_churn_threshold", 3000):
        risk += p.get("xl_penalty", 2)
    if prod_loc > 0 and test_count == 0:
        risk += p.get("untested_prod_penalty", 2)
    if tier_count > 1:
        risk += p.get("multi_tier_split_penalty", 1)
    if any("e2e" in f or "adversarial" in f for f in test_files):
        risk += p.get("comprehensive_tests_bonus", -1)
    return max(0, risk)


def _format_explanation(base_tier: str, rule: str, top_dirs: List[str], prod_loc: int, test_count: int, flags: List[str], override_info: Optional[str] = None) -> str:
    if override_info:
        res = f"{base_tier}* override ({override_info})"
        return res[:90]
    dirs_str = ", ".join(top_dirs[:2]) if top_dirs else "general"
    flags_str = f" · {', '.join(flags)}" if flags else ""
    loc_str = f"{prod_loc / 1000:.1f}k" if prod_loc >= 1000 else str(prod_loc)
    res = f"{base_tier} {rule}: {dirs_str} · {loc_str} prod LOC, {test_count}T{flags_str}"
    return res[:90]


def _handle_r0_gate(ahead: int, worktree: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Only ahead==0 + clean worktree is decommissioned here; dirty rows keep a domain tier (rule §0.6)."""
    if ahead != 0 or (worktree and worktree.get("dirty")):
        return None
    return {
        "tier": "D.1", "base_tier": "D", "rule_id": "R0.prune", "rationale": "Zero unique commits ahead of main with clean worktree",
        "scope": "decommission", "explanation": "D (P3) R0.prune: ahead=0, clean", "value": 0.0, "risk": 0, "qualifying_tiers": [],
        "in_set_prod_loc": 0, "total_prod_loc": 0, "override_active": False,
    }


def _fail_closed(rule_id: str, reason: str) -> Dict[str, Any]:
    return {
        "tier": "P3.1", "base_tier": "P3", "evidence_tier": "P3", "display_tier": "P3", "rule_id": rule_id,
        "rationale": reason, "scope": "error", "explanation": f"P3 {rule_id}: {reason}"[:90], "value": 0.0, "risk": 5,
        "qualifying_tiers": [], "in_set_prod_loc": 0, "total_prod_loc": 0, "override_active": False,
    }


def _tombstone_record() -> Dict[str, Any]:
    return {
        "tier": "D.1", "base_tier": "TOMBSTONE", "evidence_tier": "D", "display_tier": "TOMBSTONE",
        "rule_id": "R0.tombstone", "rationale": "Flagged for tombstone decommissioning in registry",
        "scope": "decommission", "explanation": "D.1 R0.tombstone: flagged in registry",
        "value": 0.0, "risk": 0, "qualifying_tiers": [], "in_set_prod_loc": 0, "total_prod_loc": 0, "override_active": True,
    }


def _summarize_dirs(matched: List[str]) -> List[str]:
    dirs: Set[str] = set()
    for f in matched:
        parts = Path(f).parts
        dirs.add(parts[1] if len(parts) > 2 else parts[-1].replace(".py", ""))
    return sorted(dirs)


def _evaluate_tier_qualification(
    prod_files: List[str],
    numstat: Dict[str, Tuple[int, int]],
    tiers_cfg: Dict[str, Any],
    prod_loc: int,
    commits: List[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, int], Dict[str, List[str]]]:
    qualifying: List[str] = []
    in_set_loc_map: Dict[str, int] = {}
    in_set_dirs_map: Dict[str, List[str]] = {}

    for t in ("P0", "P1", "P2"):
        t_cfg = tiers_cfg.get(t, {})
        matched = [f for f in prod_files if match_path_globs(f, t_cfg.get("path_globs", []))]
        loc = sum(sum(numstat.get(f, (0, 0))) for f in matched)
        in_set_loc_map[t] = loc
        in_set_dirs_map[t] = _summarize_dirs(matched)
        meets_size = loc >= t_cfg.get("min_loc", 20) and loc >= t_cfg.get("min_ratio", 0.10) * max(1, prod_loc)
        # Hotfix exception (rubric `hotfix_exception`): a conventional fix commit must itself touch the tier.
        hotfix = bool(t_cfg.get("hotfix_exception")) and loc > 0 and facts.has_scoped_fix(commits, matched)
        if meets_size or hotfix:
            qualifying.append(t)

    return qualifying, in_set_loc_map, in_set_dirs_map


def _select_winning_tier(
    qualifying: List[str],
    artifact_only: bool,
    files_by_cat: Dict[str, List[str]],
    prod_loc: int,
    tool_loc: int,
    commits: List[Dict[str, str]],
    total_adds: int,
    total_dels: int,
    total_churn: int,
    tiers_cfg: Dict[str, Any],
    in_set_loc_map: Dict[str, int],
    in_set_dirs_map: Dict[str, List[str]],
) -> Tuple[str, str, str, str, int, List[str]]:
    if qualifying:
        winning = qualifying[0]
        rule_suffix = "cost" if winning == "P0" else ("graph" if winning == "P1" else "deliverable")
        scope = "core" if winning == "P0" else ("graph" if winning == "P1" else "live")
        return (
            winning,
            f"R3.{rule_suffix}",
            scope,
            tiers_cfg.get(winning, {}).get("name", winning),
            in_set_loc_map.get(winning, 0),
            in_set_dirs_map.get(winning, []),
        )
    if artifact_only:
        return "P2", "R4.deliverable", "live", "Deliverable output artifacts", 0, ["output"]

    if prod_loc == 0 and tool_loc == 0:
        if len(files_by_cat["test"]) > 0:
            return "P3", "R5.test_only", "test", "Test code only", 0, []
        return "P3", "R5.research", "research", "Plan, docs, or research only", 0, []

    is_cosmetic_prefix = commits and all(re.split(r"[(:!]", c.get("subject", ""), maxsplit=1)[0] in ("docs", "chore", "style") for c in commits)
    if is_cosmetic_prefix and total_churn > 0 and abs(total_adds - total_dels) <= 0.05 * total_churn:
        return "P3", "R5.cosmetic", "style", "Cosmetic style/whitespace churn", 0, []

    if tool_loc > 0 and prod_loc == 0:
        return "P3", "R5.governance", "governance", "Tooling or governance scripts only", tool_loc, ["tools"]

    top = list(dict.fromkeys(Path(f).parts[0] for f in files_by_cat["prod"]))[:2]
    return "P3", "R6.misc", "misc", "Governance / Housekeeping", prod_loc, top


def _resolve_initial_override(
    overrides: Dict[str, Any], branch: str, wip: bool, today_str: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], bool, str, Any]:
    override_val = overrides.get(branch)
    override_ok, override_msg = is_valid_override(override_val, today_str=today_str)
    is_tomb = override_val == "TOMBSTONE" or (isinstance(override_val, dict) and override_val.get("tier") == "TOMBSTONE")
    if override_ok and wip and is_tomb and isinstance(override_val, str):
        override_ok, override_msg = False, "cannot apply legacy string TOMBSTONE to WIP (ahead==0, dirty) row; commit first"
    if override_ok and is_tomb:
        return _tombstone_record(), override_ok, override_msg, override_val
    if override_ok and isinstance(override_val, str):
        ov_tier = override_val.upper()
        return {
            "tier": f"{ov_tier}.1", "base_tier": ov_tier, "evidence_tier": ov_tier, "display_tier": ov_tier,
            "rule_id": "R9.override", "rationale": f"Explicit priority tier {ov_tier} from registry override",
            "scope": "override", "explanation": f"{ov_tier} override: explicit priority {ov_tier}",
            "value": 0.0, "risk": 0, "qualifying_tiers": [ov_tier], "in_set_prod_loc": 0, "total_prod_loc": 0, "override_active": True,
        }, override_ok, override_msg, override_val
    return None, override_ok, override_msg, override_val


def _fetch_or_fallback_facts(
    branch: str, root: Path, has_git: bool, wip: bool,
    numstat: Optional[Dict[str, Tuple[int, int]]],
    commits: Optional[List[Dict[str, Any]]],
    changed_files: List[str],
    allow_synthetic: bool,
) -> Tuple[Optional[Dict[str, Tuple[int, int]]], Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    if wip:
        return numstat or {}, [], None
    if numstat is not None and commits is not None:
        return numstat, commits, None
    f = facts.fetch_branch_facts(branch, root) if has_git else {"ok": False, "error": "no git repository"}
    if f["ok"]:
        return (f["numstat"] if numstat is None else numstat), (f["commits"] if commits is None else commits), None
    if numstat is not None:
        return numstat, [], None
    if allow_synthetic:
        return {p: (10, 0) for p in changed_files}, commits or [], None
    return None, None, _fail_closed("R0.facts_unavailable", f["error"])


def _apply_dict_override(
    winning: str, rule_id: str, override_ok: bool, override_val: Any, override_msg: str, flags: List[str]
) -> Tuple[str, str, str, str, bool, Optional[str], str]:
    if override_ok and isinstance(override_val, dict):
        ov_tier = override_val["tier"]
        return ov_tier, f"{ov_tier}.1*", f"{ov_tier}*", "R9.override", True, f"evidence {winning}, exp {override_val['expires_at']}, owner {override_val['owner']}", ""
    override_warning = ""
    if override_val:
        pin = override_val.get("tier") if isinstance(override_val, dict) else override_val
        override_warning = f"registry pin {pin} ignored: {override_msg}"
        if "expired" in override_msg:
            flags.append("override expired")
    return winning, f"{winning}.1", winning, rule_id, False, None, override_warning


def derive_priority(
    branch: str, changed_files: List[str], overrides: Optional[Dict[str, Any]] = None,
    rubric: Optional[Dict[str, Any]] = None, *, ahead: Optional[int] = None, behind: Optional[int] = None,
    clean_merge: Optional[bool] = None, worktree: Optional[Dict[str, Any]] = None,
    numstat: Optional[Dict[str, Tuple[int, int]]] = None, commits: Optional[List[Dict[str, Any]]] = None,
    repo_root: Optional[Path] = None, today_str: Optional[str] = None, allow_synthetic: bool = False,
) -> Dict[str, Any]:
    """Tier from real diff evidence. Placeholder LOC is only permitted with ``allow_synthetic=True`` (tests)."""
    rubric = rubric or load_rubric()
    overrides = overrides or {}
    root = repo_root or ROOT
    has_git = bool(branch) and (root / ".git").exists()

    if ahead is None:
        ahead, behind, clean_merge = _git_sync_status(branch, root) if has_git else (1, 0, True)
    behind, clean_merge = (0 if behind is None else behind), (True if clean_merge is None else clean_merge)
    if ahead < 0:
        return _fail_closed("R0.git_error", "ahead/behind unavailable (git failure)")

    ov = overrides.get(branch)
    if isinstance(ov, dict) and ov.get("tier") == "TOMBSTONE" and is_valid_override(ov, today_str=today_str)[0]:
        return _tombstone_record()

    r0_res = _handle_r0_gate(ahead, worktree)  # R0 precedes every override path: pins never lift ahead==0
    if r0_res is not None:
        return r0_res
    wip = ahead == 0  # dirty worktree, no commits: caller supplies uncommitted-path numstat (or none -> P3)

    rec, override_ok, override_msg, override_val = _resolve_initial_override(overrides, branch, wip, today_str)
    if rec is not None:
        return rec

    numstat, commits, err = _fetch_or_fallback_facts(branch, root, has_git, wip, numstat, commits, changed_files, allow_synthetic)
    if err is not None:
        return err
    numstat = numstat or {}
    commits = commits or []

    rules = rubric.get("classification_rules", {})
    files_by_cat: Dict[str, List[str]] = {c: [] for c in CATEGORIES}
    prod_loc, tool_loc, total_adds, total_dels = 0, 0, 0, 0

    for path, (a, d) in numstat.items():
        cat = classify_path(path, rules)
        files_by_cat[cat].append(path)
        total_adds += a
        total_dels += d
        if cat == "prod":
            prod_loc += (a + d)
        elif cat == "tool":
            tool_loc += (a + d)

    total_churn = total_adds + total_dels
    tiers_cfg = rubric.get("tiers", {})

    qualifying, in_set_loc_map, in_set_dirs_map = _evaluate_tier_qualification(
        files_by_cat["prod"], numstat, tiers_cfg, prod_loc, commits
    )

    artifact_only = len(files_by_cat["artifact"]) > 0 and prod_loc == 0 and tool_loc == 0 and len(files_by_cat["test"]) == 0

    winning, rule_id, scope, rationale, in_set_loc, top_dirs = _select_winning_tier(
        qualifying, artifact_only, files_by_cat, prod_loc, tool_loc, commits, total_adds, total_dels, total_churn, tiers_cfg, in_set_loc_map, in_set_dirs_map
    )

    flags: List[str] = ["WIP"] if wip else []
    if any("e2e" in f or "end_to_end" in f for f in files_by_cat["test"]):
        flags.append("+e2e")
    if any("adversarial" in f for f in files_by_cat["test"]):
        flags.append("+adv")

    value = _calculate_value_score(in_set_loc, files_by_cat["test"], commits)
    risk = _calculate_risk_score(behind, clean_merge, len(numstat), total_churn, prod_loc, len(files_by_cat["test"]), len(qualifying), files_by_cat["test"])

    effective_base, tier, display_tier, rule_id, override_active, override_info, override_warning = _apply_dict_override(
        winning, rule_id, override_ok, override_val, override_msg, flags
    )

    explanation = _format_explanation(display_tier, rule_id, top_dirs, in_set_loc if in_set_loc > 0 else prod_loc, len(files_by_cat["test"]), flags, override_info)

    return {
        "tier": tier, "base_tier": effective_base, "evidence_tier": winning, "display_tier": display_tier,
        "rule_id": rule_id, "rationale": rationale, "scope": scope, "explanation": explanation,
        "value": value, "risk": risk, "qualifying_tiers": qualifying, "in_set_prod_loc": in_set_loc,
        "total_prod_loc": prod_loc, "override_active": override_active, "override_warning": override_warning,
        "wip": wip, "unclassified_loc": sum(sum(numstat.get(p, (0, 0))) for p in files_by_cat[facts.UNCLASSIFIED]),
    }


def cmd_validate_rubric(args: argparse.Namespace) -> int:
    rubric = load_rubric()
    if not rubric or rubric.get("version") not in ("2.0", "2.1"):
        print("Error: Invalid or missing rubric v2.0/v2.1")
        return 1

    tiers = rubric.get("tiers", {})
    for t in ("P0", "P1", "P2", "P3"):
        if t not in tiers:
            print(f"Error: Missing tier {t}")
            return 1
        if t in ("P0", "P1", "P2") and not isinstance(tiers[t].get("path_globs"), list):
            print(f"Error: Tier {t} missing path_globs list")
            return 1

    registry = load_registry()
    warnings = []
    now = datetime.now(timezone.utc).date()

    for branch, data in registry.get("overrides", {}).items():
        if isinstance(data, dict) and "expires_at" in data:
            try:
                exp = datetime.strptime(data["expires_at"], "%Y-%m-%d").date()
                if exp > now + timedelta(days=30):
                    print(f"Error: Override for {branch} expires more than 30 days out ({data['expires_at']})")
                    return 1
            except Exception as e:
                print(f"Error: Invalid date format in override {branch}: {e}")
                return 1

    if args.receipt:
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "terminal_outcome": "PASS", "rubric_version": rubric.get("version"),
            "timestamp": datetime.now(timezone.utc).isoformat(), "tiers_validated": list(tiers.keys()),
            "warnings": warnings,
        }
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        print(f"Receipt written to {args.receipt}")

    print("Rubric v2 validated successfully.")
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    from tools.pr_governance_core.decision_api import evaluate_queue_decisions
    from tools.pr_governance_core.queue_snapshot import collect_queue_snapshot

    snapshot = collect_queue_snapshot(ROOT)
    decisions = evaluate_queue_decisions(snapshot=snapshot, repo_root=ROOT)
    items = decisions.branches

    rules_fired: Dict[str, int] = {}
    inputs_hasher = hashlib.sha256()

    for item in items:
        r_id = item.get("rule_id", "R6.misc")
        rules_fired[r_id] = rules_fired.get(r_id, 0) + 1
        inputs_hasher.update(f"{item.get('branch')}:{item.get('tier')}:{item.get('value_score')}:{item.get('risk_score')}".encode("utf-8"))

    receipt = {
        "terminal_outcome": "PASS",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_branches": len(items),
        "rules_fired": rules_fired,
        "inputs_digest": inputs_hasher.hexdigest(),
        "execution_sequence": decisions.execution_sequence,
        "blocked_branches": decisions.blocked_branches,
        "cycles": decisions.cycles,
        "ranked_queue": items,
    }

    if args.receipt:
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        print(f"Receipt written to {args.receipt}")
    else:
        print(json.dumps(receipt, indent=2))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PR Priority Engine v2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    val_parser = subparsers.add_parser("validate-rubric", help="Validate rubric schema")
    val_parser.add_argument("--receipt", help="Path to write the receipt JSON")

    rank_parser = subparsers.add_parser("rank", help="Rank PR queue")
    rank_parser.add_argument("--json", dest="receipt", help="Path to write the receipt JSON")

    args = parser.parse_args()
    if args.command == "validate-rubric":
        return cmd_validate_rubric(args)
    elif args.command == "rank":
        return cmd_rank(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
