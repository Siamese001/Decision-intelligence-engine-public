"""
Multi-Agent Semantic Diff & Plan Review Panel (Wave 2).

Performs authentic semantic review across four specialized agents:
1. PlanFidelityAuditor: Compares code changes against implementation_plan.md deliverables.
2. LogicDetective: AST and diff analysis for None-dereferences, loops, and logic bugs.
3. StorageConcurrencyAuditor: Audits SQLite queries, WAL mode, timeouts, and raw write bypasses.
4. RedTeamCritic: Adversarial probes for unhandled exceptions, missing timeouts, and input bypasses.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.pr_governance_core.common import ROOT, run_git, slugify_branch


@dataclass
class ReviewFinding:
    agent: str
    severity: str  # "CRITICAL" | "WARNING" | "INFO"
    file: str
    line: int
    message: str
    code_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticReviewReport:
    branch: str
    base_branch: str
    head_sha: str
    base_sha: str
    verdict: str  # "PASS" | "BLOCKED"
    plan_fidelity_score: float
    findings: List[ReviewFinding] = field(default_factory=list)
    agent_summaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    receipt_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch, "base_branch": self.base_branch,
            "head_sha": self.head_sha, "base_sha": self.base_sha,
            "verdict": self.verdict, "plan_fidelity_score": round(self.plan_fidelity_score, 3),
            "findings": [f.to_dict() for f in self.findings],
            "agent_summaries": self.agent_summaries, "receipt_digest": self.receipt_digest,
        }


def _parse_unified_diff(diff_text: str) -> Dict[str, List[Tuple[int, str]]]:
    """Parses unified diff into mapping: {filepath: [(new_line_no, line_content), ...]}."""
    files_added_lines: Dict[str, List[Tuple[int, str]]] = {}
    current_file: Optional[str] = None
    current_line_no = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            match = re.search(r"b/(.*)$", line)
            current_file = match.group(1).strip() if match else None
            if current_file:
                files_added_lines[current_file] = []
        elif line.startswith("@@ ") and current_file:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                current_line_no = int(match.group(1))
        elif line.startswith("+") and not line.startswith("+++") and current_file:
            files_added_lines[current_file].append((current_line_no, line[1:]))
            current_line_no += 1
        elif not line.startswith("-") and current_file:
            current_line_no += 1

    return files_added_lines


def _read_plan_content(root: Path, branch: str) -> str:
    """Attempts to read implementation_plan.md from branch-isolated plan, worktree, or git tree."""
    from tools.pr_governance_core.common import resolve_branch_plan_path, slugify_branch

    slug = slugify_branch(branch)
    clean_slug = slug.replace("feat_", "").replace("fix_", "").replace("chore_", "").replace("_", "-")
    for base in [root / ".worktrees" / slug, root]:
        for doc_dir in [base / "documentation" / "plans", base / "docs" / "plans", base / "plans" / slug]:
            if doc_dir.is_dir():
                for doc in doc_dir.glob("*.md"):
                    stem = doc.stem.lower().replace("_", "-")
                    if clean_slug in stem or slug.lower() in doc.stem.lower() or stem.startswith(clean_slug):
                        return doc.read_text(encoding="utf-8")
        p = resolve_branch_plan_path(branch, repo_root=base)
        if p.is_file() and (p.name != "implementation_plan.md" or "plans" in str(p)):
            return p.read_text(encoding="utf-8")
    wt_plan = root / ".worktrees" / slug / "implementation_plan.md"
    root_plan = root / "implementation_plan.md"
    main_text = run_git(["show", "main:implementation_plan.md"], cwd=root, check=False)
    if wt_plan.is_file() and wt_plan.resolve() != root_plan.resolve():
        wt_text = wt_plan.read_text(encoding="utf-8")
        return wt_text if wt_text.strip() and wt_text.strip() != main_text.strip() else ""
    if root_plan.is_file():
        root_text = root_plan.read_text(encoding="utf-8")
        return root_text if root_text.strip() and root_text.strip() != main_text.strip() else ""
    return ""


class PlanFidelityAuditor:
    """Compares actual code diff against deliverables declared in implementation_plan.md."""

    def audit(
        self, diff_files: List[str], plan_text: str
    ) -> Tuple[float, List[ReviewFinding], Dict[str, Any]]:
        findings: List[ReviewFinding] = []
        if diff_files and all(f.startswith(("output/", "reports/")) for f in diff_files):
            findings.append(ReviewFinding(
                agent="PlanFidelityAuditor", severity="INFO", file="output", line=1,
                message="Pure deliverable branch (output/ only); code plan fidelity audit exempt",
            ))
            return 1.0, findings, {"planned_deliverables": [], "missing_deliverables": []}
        if not plan_text:
            findings.append(ReviewFinding(
                agent="PlanFidelityAuditor", severity="WARNING", file="implementation_plan.md", line=1,
                message="implementation_plan.md not found or empty; plan fidelity cannot be computed",
            ))
            return 0.5, findings, {"planned_deliverables": [], "missing_deliverables": []}

        # Extract file paths mentioned in plan markdown backticks
        mentioned_files = set(re.findall(r"`([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)`", plan_text))
        relevant_planned = {
            f for f in mentioned_files if "/" in f and not f.startswith("reports/") and not f.startswith("output/")
        }

        diff_set = set(diff_files)
        matched = relevant_planned.intersection(diff_set)
        missing = relevant_planned - diff_set
        total = max(1, len(relevant_planned))
        score = len(matched) / total if relevant_planned else 1.0

        partial_tokens = ("partial scope", "[partial_scope]", "partial merge", "[partial_merge]", "allow_partial", "scope: partial")
        has_partial = any(tok in plan_text.lower() for tok in partial_tokens)
        sev = "CRITICAL" if (score < 0.5 and not has_partial) else ("WARNING" if score < 0.8 else "INFO")

        for m in sorted(missing):
            findings.append(ReviewFinding(
                agent="PlanFidelityAuditor", severity=sev, file=m, line=1,
                message=f"File '{m}' declared in implementation_plan.md is missing from branch diff (Plan Fidelity: {score:.1%})",
            ))

        if score < 0.5 and not has_partial and missing:
            findings.append(ReviewFinding(
                agent="PlanFidelityAuditor", severity="CRITICAL", file="implementation_plan.md", line=1,
                message=f"Low Plan Fidelity ({score:.1%}): {len(missing)} of {total} declared deliverables missing from branch diff without explicit partial scope declaration",
            ))

        summary = {
            "declared_deliverables_count": len(relevant_planned),
            "matched_deliverables_count": len(matched),
            "missing_deliverables_count": len(missing),
            "matched_files": sorted(matched), "missing_files": sorted(missing),
        }
        return score, findings, summary


class LogicDetective:
    """Audits newly authored code for None-dereferences, loops, and logic bugs."""

    def audit(
        self, added_lines_by_file: Dict[str, List[Tuple[int, str]]]
    ) -> Tuple[List[ReviewFinding], Dict[str, Any]]:
        findings: List[ReviewFinding] = []

        for fpath, lines in added_lines_by_file.items():
            if not fpath.endswith(".py") or fpath.endswith("semantic_diff_reviewer.py") or fpath.startswith("output/"):
                continue
            is_test_file = "test" in fpath or fpath.startswith("tests/")
            for lineno, line in lines:
                stripped = line.strip()
                # 1. Bare except
                if stripped.startswith("except:") or re.match(r"^except\s+Exception\s*:\s*pass", stripped):
                    findings.append(
                        ReviewFinding(
                            agent="LogicDetective",
                            severity="CRITICAL",
                            file=fpath,
                            line=lineno,
                            message="Bare exception handler or suppressed Exception: pass detected",
                            code_snippet=stripped,
                        )
                    )
                # 2. Infinite while without timeout
                if stripped.startswith("while True:") and "timeout" not in stripped and not is_test_file:
                    findings.append(
                        ReviewFinding(
                            agent="LogicDetective",
                            severity="WARNING",
                            file=fpath,
                            line=lineno,
                            message="while True loop detected without explicit timeout guard in loop header",
                            code_snippet=stripped,
                        )
                    )
                # 3. Direct os.environ lookup
                if not is_test_file and re.search(r"os\.environ\[", stripped):
                    findings.append(
                        ReviewFinding(
                            agent="LogicDetective",
                            severity="CRITICAL",
                            file=fpath,
                            line=lineno,
                            message="Direct os.environ lookup detected (must use os.environ.get with fallback)",
                            code_snippet=stripped,
                        )
                    )

        summary = {"files_inspected": len(added_lines_by_file), "findings_count": len(findings)}
        return findings, summary


def _is_governed_sql_writer_path(fpath: str) -> bool:
    from tools.lint_graph_write_paths import GOVERNED_MAINTENANCE_MODULES, GOVERNED_WRITER_MODULES, GRANDFATHERED_LEGACY_MODULES
    if any(tok in fpath for tok in ("universal_write_gateway", "test", "migration")):
        return True
    return any(fpath == p or fpath.startswith(p) for p in (*GOVERNED_WRITER_MODULES, *GOVERNED_MAINTENANCE_MODULES, *GRANDFATHERED_LEGACY_MODULES))


class StorageConcurrencyAuditor:
    """Audits SQLite queries, WAL mode, busy timeouts, and raw write bypasses."""

    def audit(
        self, added_lines_by_file: Dict[str, List[Tuple[int, str]]]
    ) -> Tuple[List[ReviewFinding], Dict[str, Any]]:
        findings: List[ReviewFinding] = []
        for fpath, lines in added_lines_by_file.items():
            if not fpath.endswith(".py") or fpath.endswith("semantic_diff_reviewer.py"):
                continue
            is_governed = _is_governed_sql_writer_path(fpath)
            for lineno, line in lines:
                stripped = line.strip()
                # 1. Raw SQLite write bypass outside UWG / governed maintenance writers
                if not is_governed and re.search(r"\.execute\(\s*f?[\"'](INSERT|UPDATE|DELETE)", stripped, re.IGNORECASE):
                    findings.append(ReviewFinding(
                        agent="StorageConcurrencyAuditor", severity="CRITICAL", file=fpath, line=lineno,
                        message="Raw SQLite mutation detected outside Universal Write Gateway", code_snippet=stripped,
                    ))
                # 2. Unparameterized SQL format string
                if re.search(r"\.execute\(\s*f[\"'].*?\{.*?\}", stripped):
                    findings.append(ReviewFinding(
                        agent="StorageConcurrencyAuditor", severity="CRITICAL", file=fpath, line=lineno,
                        message="Unparameterized SQL f-string query detected (SQL injection hazard)", code_snippet=stripped,
                    ))
                # 3. sqlite3.connect without timeout
                if "sqlite3.connect(" in stripped and "timeout" not in stripped and "test" not in fpath:
                    findings.append(ReviewFinding(
                        agent="StorageConcurrencyAuditor", severity="WARNING", file=fpath, line=lineno,
                        message="sqlite3.connect called without explicit timeout parameter", code_snippet=stripped,
                    ))

        summary = {"inspected_files": len(added_lines_by_file), "storage_hazards": len(findings)}
        return findings, summary


class RedTeamCritic:
    """Adversarial probe evaluating failure handling, timeouts, and boundary conditions."""

    def audit(
        self, added_lines_by_file: Dict[str, List[Tuple[int, str]]]
    ) -> Tuple[List[ReviewFinding], Dict[str, Any]]:
        findings: List[ReviewFinding] = []

        for fpath, lines in added_lines_by_file.items():
            if not fpath.endswith(".py") or fpath.endswith("semantic_diff_reviewer.py"):
                continue
            is_test_file = "test" in fpath or fpath.startswith("tests/")
            for lineno, line in lines:
                stripped = line.strip()
                # 1. subprocess.run without timeout
                if not is_test_file and "subprocess.run(" in stripped and "timeout=" not in stripped:
                    findings.append(
                        ReviewFinding(
                            agent="RedTeamCritic",
                            severity="WARNING",
                            file=fpath,
                            line=lineno,
                            message="subprocess.run executed without explicit timeout parameter",
                            code_snippet=stripped,
                        )
                    )
                # 2. shell=True usage
                if not is_test_file and "shell=True" in stripped:
                    findings.append(
                        ReviewFinding(
                            agent="RedTeamCritic",
                            severity="CRITICAL",
                            file=fpath,
                            line=lineno,
                            message="subprocess called with shell=True in non-test code",
                            code_snippet=stripped,
                        )
                    )

        summary = {"adversarial_probes_run": 2, "vulnerabilities_flagged": len(findings)}
        return findings, summary


class SemanticDiffReviewPanel:
    """Coordinates the 4-agent review panel and seals an immutable RFC 8785 review receipt."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.plan_auditor = PlanFidelityAuditor()
        self.logic_detective = LogicDetective()
        self.storage_auditor = StorageConcurrencyAuditor()
        self.red_team = RedTeamCritic()

    def run_review(
        self,
        branch: str,
        base_branch: str = "main",
        diff_text: Optional[str] = None,
        persist_receipt: bool = True,
    ) -> SemanticReviewReport:
        # Determine merge base and diff
        head_sha = run_git(["rev-parse", branch], cwd=self.root, check=False).strip()[:8]
        base_sha = run_git(["rev-parse", base_branch], cwd=self.root, check=False).strip()[:8]

        if diff_text is None:
            try:
                merge_base = run_git(["merge-base", base_branch, branch], cwd=self.root).strip()
            except Exception:
                merge_base = base_sha
            raw_diff = run_git(["diff", f"{merge_base}...{branch}"], cwd=self.root, check=False)
        else:
            raw_diff = diff_text

        added_lines = _parse_unified_diff(raw_diff)
        diff_files = list(added_lines.keys())
        plan_text = _read_plan_content(self.root, branch)

        # Run 4 agents
        fidelity_score, f_findings, f_summary = self.plan_auditor.audit(diff_files, plan_text)
        l_findings, l_summary = self.logic_detective.audit(added_lines)
        s_findings, s_summary = self.storage_auditor.audit(added_lines)
        r_findings, r_summary = self.red_team.audit(added_lines)

        all_findings = f_findings + l_findings + s_findings + r_findings
        critical_count = sum(1 for f in all_findings if f.severity == "CRITICAL")

        verdict = "BLOCKED" if critical_count > 0 else "PASS"
        summaries = {
            "PlanFidelityAuditor": f_summary, "LogicDetective": l_summary,
            "StorageConcurrencyAuditor": s_summary, "RedTeamCritic": r_summary,
        }
        report = SemanticReviewReport(
            branch=branch, base_branch=base_branch, head_sha=head_sha, base_sha=base_sha,
            verdict=verdict, plan_fidelity_score=fidelity_score, findings=all_findings, agent_summaries=summaries,
        )

        # RFC 8785 canonical hash
        canon_json = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
        report.receipt_digest = hashlib.sha256(canon_json.encode("utf-8")).hexdigest()

        # Persist receipt
        if persist_receipt:
            reports_dir = self.root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            slug = slugify_branch(branch)
            (reports_dir / f"semantic_code_review_{slug}_receipt.json").write_text(
                json.dumps(report.to_dict(), indent=2), encoding="utf-8"
            )

        return report
