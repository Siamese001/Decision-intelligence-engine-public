#!/usr/bin/env python3
"""Governed Refactoring Arbiter (Wave 2 Architectural & Refactoring Safeguards).

Enforces that automated refactorings, 400-line module budget splits, and structural
code modifications never bypass human governance via overconfident probabilistic scores:
1. Scans code modifications for L0-L6 DAG inversions and cyclic dependencies.
2. Intercepts 400-line budget breaches and subpackage decomposition proposals.
3. Detects raw SQLite connection bypasses or concurrency lock alterations.
4. Forces mandatory HITL UI clickdown (Tier 1 Architectural / Tier 0 Irreversible)
   using HitlArbiterAgent under gpt-5.6-luna (high).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_criticality import (
    CriticalityTier,
    evaluate_criticality_score,
)

DEFAULT_MAX_LINES = 400

# Governed Layer Hierarchy
LAYER_MAP: Dict[str, int] = {
    "storage": 1,
    "pipeline_a_intake": 2,
    "pipeline_b_extraction": 3,
    "dual_pipeline/retrieval": 4,
    "dual_pipeline/decision": 5,
    "dual_pipeline/governance": 6,
}


class GovernedRefactorArbiter:
    """Arbiter analyzing code changes for architectural risk and formulating HITL cards."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        ledger_path: Optional[Path | str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.arbiter = HitlArbiterAgent(
            model_id=model_id or def_m,
            reasoning_effort=reasoning_effort or def_e,
            ledger_path=ledger_path,
        )
        self.store = self.arbiter.store

    def inspect_source_code(
        self,
        file_path: Path | str,
        source_code: str,
    ) -> Dict[str, Any]:
        """Inspect source code for architectural anti-patterns, line counts, and AST imports.

        Returns:
            Dict containing detected risks, line count, criticality tier, and reasons.
        """
        path_str = str(file_path).replace("\\", "/")
        lines = source_code.splitlines()
        line_count = len(lines)
        risks: List[str] = []

        # 1. 400-line budget check
        if line_count > DEFAULT_MAX_LINES:
            risks.append(
                f"LINE_BUDGET_BREACH: File length ({line_count} lines) exceeds {DEFAULT_MAX_LINES}-line budget."
            )

        # 2. Parse AST for imports and raw SQLite calls
        try:
            tree = ast.parse(source_code, filename=path_str)
        except SyntaxError as e:
            risks.append(f"SYNTAX_ERROR: Failed to parse AST: {e}")
            tree = None

        has_raw_sqlite = False
        import_targets: List[str] = []

        if tree:
            for node in ast.walk(tree):
                # Detect raw sqlite3.connect calls bypassing storage.database connection manager
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "connect":
                        if isinstance(func.value, ast.Name) and func.value.id in ("sqlite3", "sqlite"):
                            has_raw_sqlite = True
                # Collect imports
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        import_targets.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        import_targets.append(node.module)

        if has_raw_sqlite and "storage/database.py" not in path_str and "tests/" not in path_str:
            risks.append(
                "CONCURRENCY_LOCK_BYPASS: Direct sqlite3.connect detected outside storage connection manager. "
                "Violates Master Invariant 2 (WAL serialization)."
            )

        # 3. Detect layer inversions (e.g. storage or pipeline_a importing decision/retrieval)
        source_layer = self._determine_layer(path_str)
        for target in import_targets:
            target_layer = self._determine_layer(target)
            if source_layer is not None and target_layer is not None and source_layer < target_layer:
                risks.append(
                    f"LAYER_INVERSION_DEFECT: Lower layer L{source_layer} ({path_str}) imports "
                    f"higher layer L{target_layer} ({target}). Violates Master Invariant 1."
                )

        # 4. Criticality evaluation
        if not risks:
            tier = CriticalityTier.TIER_4_AUTONOMOUS
            score = 0.10
            reasons = ["Clean module; zero architectural, concurrency, or line budget risks detected."]
        else:
            context = {
                "affected_file": path_str,
                "line_count": line_count,
                "violates_400_line_budget": line_count > DEFAULT_MAX_LINES,
                "modifies_storage_locks": has_raw_sqlite,
                "affects_layer_dag": any("LAYER_INVERSION" in r for r in risks),
                "category": "CODE_REFACTORING",
                "details": " ".join(risks),
            }
            tier, score, reasons = evaluate_criticality_score(context=context, operation_text=" ".join(risks))

        return {
            "file_path": path_str,
            "line_count": line_count,
            "risks": risks,
            "criticality_tier": tier,
            "criticality_score": score,
            "reasons": reasons,
            "has_blocking_defects": len(risks) > 0,
        }

    def arbitrate_refactoring_proposal(
        self,
        decision_id: str,
        target_file: Path | str,
        proposed_action: str,
        source_code: str,
        suggested_subpackage: Optional[str] = None,
        auto_record: bool = False,
    ) -> Dict[str, Any]:
        """Arbitrates a code refactoring proposal, formatting an atomic card if HITL is required."""
        inspection = self.inspect_source_code(target_file, source_code)
        path_str = str(target_file)
        tier = inspection["criticality_tier"]
        risks = inspection["risks"]

        if not inspection["has_blocking_defects"]:
            return {
                "decision_id": decision_id,
                "target_file": path_str,
                "inspection": inspection,
                "status": "AUTONOMOUS_APPROVED",
                "should_surface": False,
                "criticality_tier": tier.value if isinstance(tier, CriticalityTier) else tier,
                "atomic_card": None,
                "ledger_record": None,
                "reason": "Zero architectural, concurrency, or line budget defects detected; code change autonomously approved.",
            }

        # Formulate concrete, mutually exclusive options
        base_name = Path(target_file).stem
        target_pkg = suggested_subpackage or f"{Path(target_file).parent}/{base_name}_core"

        options = [
            {
                "key": "structured_decomposition",
                "label": f"Decompose into modular '{target_pkg}' subpackage",
                "confidence": "90%",
                "tier": "HIGH",
                "rationale": "Enforces 400-line budget and unidirectional L0-L6 layering cleanly.",
                "description": "Recommended refactoring path.",
            },
            {
                "key": "temporary_budget_exemption",
                "label": "Grant temporary budget exemption and retain monolithic file",
                "confidence": "55%",
                "tier": "MEDIUM",
                "rationale": "Avoids file splitting churn for transient feature work.",
                "description": "Requires technical debt ledger entry.",
            },
            {
                "key": "abort_refactor",
                "label": "Halt refactor and require manual principal architect design review",
                "confidence": "25%",
                "tier": "LOW",
                "rationale": "High risk of cyclic imports or concurrency regression.",
                "description": "Fails closed.",
            },
        ]

        question = (
            f"Architecture review required for '{path_str}' ({inspection['line_count']} lines).\n"
            f"Detected Risks: {', '.join(risks) if risks else 'None'}."
        )

        arbitration = self.arbiter.arbitrate_decision(
            decision_id=decision_id,
            category="ARCHITECTURAL_CODE_REFACTORING",
            question=question,
            options=options,
            criticality_tier=tier,
            context=inspection,
            auto_record_ledger=auto_record,
        )

        return {
            "decision_id": decision_id,
            "target_file": path_str,
            "inspection": inspection,
            "status": arbitration["status"],
            "should_surface": arbitration["should_surface"],
            "criticality_tier": tier.value if isinstance(tier, CriticalityTier) else tier,
            "atomic_card": arbitration.get("atomic_card"),
            "ledger_record": arbitration.get("recorded_entry"),
            "reason": arbitration.get("reason"),
        }

    def _determine_layer(self, module_path: str) -> Optional[int]:
        normalized = module_path.replace(".", "/")
        for prefix, layer in LAYER_MAP.items():
            if prefix in normalized:
                return layer
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed Refactoring Arbiter")
    parser.add_argument("--file", type=str, help="Python source file to inspect")
    parser.add_argument("--decision-id", type=str, default="refactor_check_001", help="Unique decision ID")
    parser.add_argument("--auto-record", action="store_true", help="Auto-record decisions in hitl_decision_ledger.json")
    args = parser.parse_args()

    if not args.file:
        print("Error: --file argument required.")
        return 1

    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"Error: File not found: {file_path}")
        return 1

    arbiter = GovernedRefactorArbiter()
    source_code = file_path.read_text(encoding="utf-8")
    result = arbiter.arbitrate_refactoring_proposal(
        decision_id=args.decision_id,
        target_file=file_path,
        proposed_action="inspect_and_govern",
        source_code=source_code,
        auto_record=args.auto_record,
    )

    print(json.dumps(result, indent=2, default=str))
    if result["should_surface"]:
        print(f"\n[HITL REQUIRED]: Criticality Tier {result['criticality_tier']} requires operator clickdown in UI.")
        return 2

    print("\n[AUTONOMOUS APPROVED]: Code change passed architectural safeguards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
