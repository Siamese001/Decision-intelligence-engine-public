#!/usr/bin/env python3
"""tools/generate_agents_index.py

Generates and validates the compact AGENTS.md index (<= 12 KB) from canonical
governance sources (governance/policy_registry.yaml and .agents/rules/*.md).
Guarantees byte-for-byte preservation of the NEE-RUNTIME-EVIDENCE-GUARD block
while eliminating bloated narrative duplication that causes context window truncation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

RUNTIME_EVIDENCE_GUARD_BLOCK = """## Runtime and retrieval claims are evidence-bound
<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->

Before stating that an embedding backend is active or inactive, or that retrieval testing/evidence is present or absent, run `python -m dual_pipeline.runtime_evidence inventory` and inspect runtime, model, generation, query-run, and evidence bindings.
- Every `active_pair.json` is scoped to its explicit runtime root; never treat a default-looking directory as the single global runtime.
- Follow every existing `runtime_root` and `evidence_root` pointer in governed receipts and include deployment-owned roots.
- An empty filename or pattern search is never proof that runtime or retrieval evidence is absent. Report it only as “not found in the explicitly searched scope.”
- `production_qualified: false` is an authorization boundary; it does not mean that a model is inactive, unexecuted, or untested.
- Sealed retrieval release evaluations are retrieval evidence. Inventory `<runtime_root>/retrieval_evaluations/` and bind exact status, pair, model, receipt, gates, digest, and path before making presence/absence claims.
- Bind positive claims with `python -m dual_pipeline.runtime_evidence claim`.
- Negative claims require explicit `--scope-root` values plus `--complete-scope`; if claim fails, do not make the claim.
- Report exact runtime root, active generation, embedding backend, model ID/revision, relevant run IDs, terminal outcomes, and evidence paths.

The tracked pre-commit and pre-push hooks enforce the continued presence of this control and its focused regression tests.

<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->"""

UNIFIED_ARCHITECTURE_BLOCK = """## Skills, agents, sub-agents, hooks, and rules unified architecture
See [.agents/rules/unified-agent-architecture.md](.agents/rules/unified-agent-architecture.md).

### Five Core Architectural Pillars
- **Skills (What work gets done)**: Skills are the building blocks.
- **Agents (Who decides & orchestrates)**: Agents orchestrate and decide.
- **Sub-Agents (Specialized executors)**: Sub-Agents specialize and scale.
- **Hooks (When to extend)**: Hooks extend behavior.
- **Rules (What must be true)**: Rules govern.

### Shared Foundations (Common to All)
Memory, ledgers, graphs, telemetry, learning.

### Structural Interactions & Delegation Contracts
Agents use skills, delegate via mandatory contracts.

### End-to-End Execution Flow (7-Step Lifecycle)
1. Verify -> 2. Plan -> 3. Delegate -> 4. Execute -> 5. Validate -> 6. Seal -> 7. Teardown."""

CANONICAL_CORE_INVARIANTS = frozenset(
    {
        "INV-BUDGET-FILE-LINES",
        "INV-COMPLEXITY-MAX",
        "INV-HITL-AMBIGUITY-MARGIN",
        "INV-HITL-UNRESOLVED-ENTITY-MARGIN",
        "INV-HITL-DIVERGENCE-MARGIN",
        "INV-AGENTS-MD-MAX-SIZE",
        "INV-TEST-TIMEOUT-HARD",
        "INV-WAVE-PLAN-MAX-WORDS",
        "INV-TEST-TIMEOUT-CLASS-B",
        "INV-MIN-E2E-COVERAGE-SCORE",
        "INV-HITL-BAYESIAN-PRIOR-STRENGTH",
        "INV-HITL-MIN-CONFIDENCE-FLOOR",
        "INV-HITL-ENTROPY-CEILING",
        "INV-GROUNDING-MANDATORY-ASSERTION",
        "INV-GROUNDING-STRICT-METRICS",
        "INV-GROUNDING-DELIVERY-IMMUTABILITY",
        "INV-GROUNDING-SAFE-FAILURE-RESPONSE",
        "INV-INGESTION-ACTIVE-COMPLETION",
        "INV-INGESTION-ENGINEERING-ISOLATION",
        "INV-OUTPUT-MAX-WORDS",
        "INV-OUTPUT-MAX-CHARS",
        "INV-OUTPUT-PROGRESS-MAX-WORDS",
        "INV-GRAPH-MUTATION-PHYSICAL-PROOF",
    }
)




def load_policy_registry(repo_root: Path) -> Dict[str, Any]:
    registry_path = repo_root / "governance" / "policy_registry.yaml"
    if not registry_path.exists():
        raise FileNotFoundError(f"Policy registry not found at {registry_path}")
    with open(registry_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def scan_rule_metadata(repo_root: Path) -> List[Dict[str, str]]:
    rules_dir = repo_root / ".agents" / "rules"
    if not rules_dir.exists():
        return []

    rules: List[Dict[str, str]] = []
    for p in sorted(rules_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        trigger = "always_on"
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    fm = yaml.safe_load(parts[1])
                    if isinstance(fm, dict) and "trigger" in fm:
                        trig_val = fm["trigger"]
                        trigger = trig_val if isinstance(trig_val, str) else "glob"
                except Exception:
                    pass
        # Get first heading or description
        title = p.stem.replace("-", " ").title()
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        rules.append(
            {
                "filename": p.name,
                "title": title,
                "trigger": trigger,
                "path": f".agents/rules/{p.name}",
            }
        )
    return rules


def build_agents_index_content(repo_root: Path) -> str:
    registry = load_policy_registry(repo_root)
    all_policies = registry.get("policies", [])
    policies = [p for p in all_policies if p.get("id") in CANONICAL_CORE_INVARIANTS]
    rules = scan_rule_metadata(repo_root)

    # Build markdown index
    lines = [
        "# NEE Knowledge Graph & Decision Intelligence Agent Controls",
        "",
        "> [!IMPORTANT]",
        "> **Canonical Invariant Index:** Generated from `governance/policy_registry.yaml`.",
        "",
        "## Core Operational Invariants",
        "",
        "| ID | Invariant Title | SSOT Threshold | Enforcer / Verification | Governed Rule |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for pol in policies:
        pid = pol.get("id", "")
        title = pol.get("title", "")
        thresh = pol.get("threshold", "")
        unit = pol.get("unit", "")
        enforcer = pol.get("enforcer", "")
        short_enforcer = Path(enforcer).name if "/" in enforcer else enforcer
        rule = pol.get("rule_reference", "")
        lines.append(
            f"| `{pid}` | {title} | `{thresh} {unit}` | `{short_enforcer}` | [{Path(rule).name}]({rule}) |"
        )

    lines.extend(
        [
            "",
            "## Primary Execution Principles",
            "",
            "1. **Wave Gating:** Advance only after observed behavior passes. See [wave-progression.md](.agents/rules/wave-progression.md).",
            "2. **Human Decisions:** Confirm via `ask_user_question` when margin $\\Delta \\le 30\\%$. See [hitl-decisions.md](.agents/rules/hitl-decisions.md).",
            "3. **Plain Language:** Plain English overrides code citations. Translate behavior into business meaning.",
            "4. **Ingestion & Isolation:** Ingestion requires approval; tests use sandboxes.",
            "5. **Worktree Isolation:** Branch into `.worktrees/<slug>` via `pr_governance.py start`. See [git-worktree-governance.md](.agents/rules/git-worktree-governance.md).",
            "6. **Progress Tracking:** Operations (>3s) use progress bars. See [progress-indicators.md](.agents/rules/progress-indicators.md).",
            "7. **Fast Verification:** Receipt verification requires `--timeout=60`. See [e2e-and-edge-case-coverage.md](.agents/rules/e2e-and-edge-case-coverage.md).",
            "8. **Concise Output:** Target ~120 words (max 300). See [engineering-output.md](.agents/rules/engineering-output.md).",
            "",
            RUNTIME_EVIDENCE_GUARD_BLOCK,
            "",
            UNIFIED_ARCHITECTURE_BLOCK,
            "",
            "## Governed Rule Registry Index",
            "",
            f"The repository defines **{len(rules)} modular rules** in `.agents/rules/`, activated deterministically by trigger configuration:",
            "",
            "| Rule Document | Trigger Mode | Scope & Core Responsibility |",
            "| :--- | :--- | :--- |",
        ]
    )

    for r in rules:
        fname = r["filename"]
        rpath = r["path"]
        trig = r["trigger"]
        title = r["title"].split(":")[0].strip()
        for suffix in (
            " Single Source of Truth (SSOT) Rules",
            " Single Source of Truth (SSOT)",
            " Single Source of Truth Rules",
            " Single Source of Truth",
            " Continuous Feedback Governance Rules",
            " & Continuous Feedback Governance Rules",
            " Firewall Rules",
            " Governance Rules",
            " Governance Rule",
            " Governance",
            " Rules",
            " Rule",
            " & Pre-Egress Sanitization",
            " & Local Main Promotion",
            " & Branch Summary Rendering",
            " & Synthetic Fixture Quarantine",
            " & Telemetry Transparency",
            " & Persistence Resilience",
            " & Continuous Feedback",
            " & Batch Sizing",
            " & Healing Mesh",
            " & Artifact Independence",
            " & Anti-Overfitting",
            " & Storage Safety",
            " & Module Budget",
            " & LLM Neutrality",
            " & Concise Reporting",
            " & Edge-Case Coverage",
            " Anti-Pollution & Synthetic Fixture Quarantine",
            " Decision Governance",
            " Architecture",
        ):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        title = title[:20].strip()
        lines.append(f"| [{fname}]({rpath}) | `{trig}` | {title} |")

    lines.extend(
        [
            "",
            "---",
            "*Generated by `tools/generate_agents_index.py`. Enforces `INV-AGENTS-MD-MAX-SIZE` (<= 12 KB).* ",
            "",
        ]
    )

    return "\n".join(lines)


def check_agents_index(repo_root: Path, agents_path: Path) -> List[str]:
    if not agents_path.exists():
        return [f"{agents_path} does not exist"]
    content = agents_path.read_text(encoding="utf-8")
    size = len(content.encode("utf-8"))
    max_size = 12288  # 12 KB

    errors = []
    if size > max_size:
        errors.append(
            f"AGENTS.md exceeds size ceiling: {size} bytes > {max_size} bytes (12 KB)"
        )
    if "<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->" not in content:
        errors.append("AGENTS.md is missing NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 sentinel")
    if "<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->" not in content:
        errors.append("AGENTS.md is missing NEE-RUNTIME-EVIDENCE-GUARD:END v1 sentinel")

    generated = build_agents_index_content(repo_root)
    if content != generated:
        errors.append(
            "AGENTS.md is out of sync with generator (generator drift detected). "
            "Run 'python tools/generate_agents_index.py' to regenerate."
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate compact AGENTS.md index.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if AGENTS.md complies with <=12KB size, contains runtime guard, and has zero generator drift.",
    )
    parser.add_argument("--repo-root", default=".", help="Path to repository root.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    agents_path = repo_root / "AGENTS.md"

    if args.check:
        errors = check_agents_index(repo_root, agents_path)
        if errors:
            for e in errors:
                print(f"[FAIL] {e}", file=sys.stderr)
            return 1

        size = len(agents_path.read_bytes())
        print(
            f"[PASS] AGENTS.md satisfies governance index contract ({size} bytes <= 12288 bytes, zero drift)."
        )
        return 0


    # Generate mode
    new_content = build_agents_index_content(repo_root)
    size = len(new_content.encode("utf-8"))
    max_size = 12288
    if size > max_size:
        print(
            f"[ERROR] Generated index exceeds 12 KB limit ({size} bytes > {max_size} bytes). Pruning required.",
            file=sys.stderr,
        )
        return 1

    agents_path.write_text(new_content, encoding="utf-8")
    print(f"[SUCCESS] Wrote compact AGENTS.md index ({size} bytes, limit: {max_size} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
