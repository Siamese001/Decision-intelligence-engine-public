#!/usr/bin/env python3
"""
tools/generate_ssot_inventory.py

Automated static AST/regex inventory generator for SSOT violations, hardcoded
configuration constants, function default bounds, step-tables, and duplicate
pattern lists across governed directories.
Outputs reports/ssot_inventory.json.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

GOVERNED_DIRS: Tuple[str, ...] = (
    "dual_pipeline",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "storage",
    "config",
    "tools",
)

CONFIG_CONSTANT_RE = re.compile(
    r"(?:MIN_WORDS|MAX_WORDS|WORD_COUNT|THRESHOLD|TIMEOUT|MAX_RETRIES|MAX_TOKENS|"
    r"FLOOR|CEILING|LIMIT|BUDGET|COLLAPSE|DENSITY|_C21_FLOORS|SPEAKER_CEILING)",
    re.IGNORECASE,
)

BOUND_PARAM_NAMES = frozenset({
    "min_words",
    "max_words",
    "min_words_2a",
    "min_words_2c",
    "max_words_per_utterance",
    "min_words_per_theme",
    "max_tokens",
    "max_input_tokens",
    "max_completion_tokens",
    "timeout",
    "deadline_seconds",
    "max_retries",
})

STEP_TABLE_FUNC_RE = re.compile(
    r"(?:_get_.*word_limits|_get_word_count_limits|_check_word_count_bounds|"
    r"_check_deep_section_density|check_word_limit_envelopes|check_section_density_sum|"
    r"validate_word_floor)",
    re.IGNORECASE,
)

PATTERN_SYMBOL_RE = re.compile(
    r"^(?:WORD_SALAD_PATTERNS|FORBIDDEN_APPENDIX_DISCLAIMERS|PASSIVE_HEDGING_PATTERNS|"
    r"_PASSIVE_HEDGING_THRESHOLD|ROLE_ANONYMIZATION_PATTERNS|C19_LEAK_MARKERS|C29_EXPLICIT_MARKERS)$"
)


def infer_target_ssot_key(rel_path: str, symbol: str) -> str:
    """Map a discovered symbol and file path to its canonical SSOT parameter key or contract."""
    sym_up = symbol.upper()
    path_low = rel_path.lower()
    if PATTERN_SYMBOL_RE.match(symbol):
        return f"dual_pipeline.prompts.contracts.output2_contract.{symbol.lstrip('_')}"
    if "C21" in sym_up or "appendix_standard" in path_low:
        return "output2.c21_speaker_tier_floors"
    if "copilot" in path_low or "COPILOT" in sym_up:
        return f"copilot.speech.{symbol.lower().lstrip('_')}"
    if "whisperx" in path_low or "utterance" in path_low or "audio" in path_low:
        return "audio_ingestion.max_words_per_utterance"
    if "grounding" in path_low or "delivery_boundary" in path_low:
        return "grounding.delivery_max_words"
    if "gate1" in path_low or "OUTPUT1" in sym_up:
        return f"output1.{symbol.lower().lstrip('_')}"
    if "OUTPUT3" in sym_up or "gate3" in path_low:
        return f"output3.{symbol.lower().lstrip('_')}"
    if "judge" in path_low or "JUDGE" in sym_up:
        return f"judge.{symbol.lower().lstrip('_')}"
    if any(k in path_low for k in ("gate2", "recap", "output2", "constraint_bounds")):
        return f"output2.word_limits.{symbol.lower().lstrip('_')}"
    return f"runtime.{symbol.lower().lstrip('_')}"


def _extract_literal_value(node: Optional[ast.AST]) -> Tuple[bool, Any]:
    """Extract primitive numeric or collection literal from an AST node."""
    if node is None:
        return False, None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        if isinstance(node.operand.value, (int, float)):
            return True, -node.operand.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Dict)):
        try:
            val = ast.literal_eval(node)
            return True, val
        except Exception:
            return True, "<complex_literal>"
    if isinstance(node, ast.Call):
        func_name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if func_name == "compile" and node.args:
            return True, "<compiled_regex>"
    return False, None


def _collect_numeric_literals(node: ast.AST) -> List[Any]:
    """Collect all numeric constants inside a function AST subtree."""
    nums: List[Any] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)) and not isinstance(child.value, bool):
            if child.value not in (0, 1, -1):
                nums.append(child.value)
    return nums


class ConfigASTInventoryVisitor(ast.NodeVisitor):
    """AST visitor extracting hardcoded constants, default bounds, step-tables, and patterns."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.constants: List[Dict[str, Any]] = []
        self.default_bounds: List[Dict[str, Any]] = []
        self.step_tables: List[Dict[str, Any]] = []
        self.pattern_defs: List[Dict[str, Any]] = []
        self._class_stack: List[str] = []
        self._func_depth: int = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _record_assignment(self, target_name: str, value_node: Optional[ast.AST], lineno: int) -> None:
        if PATTERN_SYMBOL_RE.match(target_name):
            ok, val = _extract_literal_value(value_node)
            self.pattern_defs.append({
                "file": self.rel_path,
                "line_number": lineno,
                "symbol": target_name,
                "value_preview": str(val)[:120] if ok else "<expression>",
                "target_ssot_key": infer_target_ssot_key(self.rel_path, target_name),
            })
            return

        if self._func_depth > 0:
            return

        qualified = f"{self._class_stack[-1]}.{target_name}" if self._class_stack else target_name
        if not (CONFIG_CONSTANT_RE.search(qualified) or target_name in BOUND_PARAM_NAMES):
            return
        ok, val = _extract_literal_value(value_node)
        if not ok or val in ("<compiled_regex>", "<complex_literal>") and not isinstance(val, (int, float, dict, list, tuple)):
            if target_name != "_C21_FLOORS":
                return
        self.constants.append({
            "file": self.rel_path,
            "line_number": lineno,
            "symbol": qualified,
            "value": val if isinstance(val, (int, float, str, list)) else str(val),
            "target_ssot_key": infer_target_ssot_key(self.rel_path, qualified),
        })

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._record_assignment(target.id, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._record_assignment(node.target.id, node.value, node.lineno)
        self.generic_visit(node)

    def _visit_callable(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args.args
        defaults = node.args.defaults
        offset = len(args) - len(defaults)
        for idx, default_node in enumerate(defaults):
            arg_name = args[offset + idx].arg
            if arg_name in BOUND_PARAM_NAMES:
                ok, val = _extract_literal_value(default_node)
                if ok and isinstance(val, (int, float)):
                    self.default_bounds.append({
                        "file": self.rel_path,
                        "line_number": node.lineno,
                        "function": node.name,
                        "parameter": arg_name,
                        "default_value": val,
                        "target_ssot_key": infer_target_ssot_key(self.rel_path, arg_name),
                    })

        if STEP_TABLE_FUNC_RE.match(node.name):
            nums = _collect_numeric_literals(node)
            if nums:
                self.step_tables.append({
                    "file": self.rel_path,
                    "line_number": node.lineno,
                    "function": node.name,
                    "numeric_literals": nums,
                    "target_ssot_key": infer_target_ssot_key(self.rel_path, node.name),
                })

        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_callable(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_callable(node)


def scan_source_ast(source: str, rel_path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parse Python source and extract AST configuration inventory items."""
    tree = ast.parse(source, filename=rel_path)
    visitor = ConfigASTInventoryVisitor(rel_path)
    visitor.visit(tree)
    return {
        "constants": visitor.constants,
        "default_bounds": visitor.default_bounds,
        "step_tables": visitor.step_tables,
        "pattern_defs": visitor.pattern_defs,
    }


def scan_governed_ast_inventory(
    repo_root: Path = ROOT,
    governed_dirs: Sequence[str] = GOVERNED_DIRS,
) -> Dict[str, Any]:
    """Walk governed directories and aggregate AST configuration findings."""
    constants: List[Dict[str, Any]] = []
    default_bounds: List[Dict[str, Any]] = []
    step_tables: List[Dict[str, Any]] = []
    pattern_defs: List[Dict[str, Any]] = []

    for d_name in governed_dirs:
        d_path = repo_root / d_name
        if not d_path.exists():
            continue
        for py_file in sorted(d_path.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            rel_path = str(py_file.relative_to(repo_root)).replace("\\", "/")
            if rel_path == "tools/generate_ssot_inventory.py":
                continue
            try:
                src = py_file.read_text(encoding="utf-8")
                res = scan_source_ast(src, rel_path)
            except Exception:
                continue
            constants.extend(res["constants"])
            default_bounds.extend(res["default_bounds"])
            step_tables.extend(res["step_tables"])
            pattern_defs.extend(res["pattern_defs"])

    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for p in pattern_defs:
        by_symbol.setdefault(p["symbol"], []).append(p)
    duplicate_clusters = [
        {
            "symbol": sym,
            "occurrence_count": len(items),
            "locations": [{"file": i["file"], "line_number": i["line_number"]} for i in items],
            "target_ssot_key": items[0]["target_ssot_key"],
        }
        for sym, items in sorted(by_symbol.items())
    ]

    return {
        "hardcoded_config_constants": constants,
        "default_parameter_bounds": default_bounds,
        "step_table_functions": step_tables,
        "duplicate_pattern_clusters": duplicate_clusters,
    }


def scan_ingestion_scripts(repo_root: Path = ROOT) -> List[Dict[str, Any]]:
    scripts_dir = repo_root / "scripts"
    results: List[Dict[str, Any]] = []
    if not scripts_dir.exists():
        return results
    for p in sorted(scripts_dir.glob("build_and_ingest_*.py")):
        content = p.read_text(encoding="utf-8")
        results.append({
            "script_name": p.name,
            "path": str(p.relative_to(repo_root)),
            "line_count": len(content.splitlines()),
            "has_hardcoded_f3_data": bool(re.search(r"f3_data\w*\s*=\s*\{", content)),
            "has_bespoke_mutation_table": (
                "def print_mutation_breakdown" in content or "Granular Graph DB Delta Table" in content
            ),
            "has_bespoke_pinning": "pinned_evals" in content,
        })
    return results


def scan_threshold_literals(repo_root: Path = ROOT) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(r"\b(s[123]|score|composite_score|out[123]_eval)\s*(?:>=|>|==|<|<=)\s*(80|90|95)\b")
    for py_file in sorted(repo_root.glob("**/*.py")):
        rel_parts = py_file.relative_to(repo_root).parts
        if any(skip in rel_parts for skip in (".venv", ".worktrees", "scratch", "__pycache__")):
            continue
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
            for idx, line in enumerate(lines, 1):
                m = pattern.search(line)
                if m:
                    findings.append({
                        "file": str(py_file.relative_to(repo_root)),
                        "line_number": idx,
                        "match": m.group(0),
                        "snippet": line.strip(),
                    })
        except Exception:
            pass
    return findings


def scan_prompt_sources(repo_root: Path = ROOT) -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = []
    gov_dir = repo_root / "governance" / "decision_intelligence"
    if gov_dir.exists():
        for p in sorted(gov_dir.glob("*PROMPT*.md")):
            prompts.append({"type": "governance_canonical", "path": str(p.relative_to(repo_root)), "name": p.name})
    cfg_dir = repo_root / "config" / "prompts"
    if cfg_dir.exists():
        for p in sorted(cfg_dir.glob("**/*.md")):
            prompts.append({"type": "config_prompt_stage", "path": str(p.relative_to(repo_root)), "name": p.name})
    judge_dir = repo_root / "dual_pipeline" / "llm_judge_core"
    if judge_dir.exists():
        for py_file in sorted(judge_dir.glob("*.py")):
            content = py_file.read_text(encoding="utf-8")
            if any(k in content for k in ("system_prompt =", "You are a Knowledge Graph", "You are an elite")):
                prompts.append({"type": "inline_judge_prompt", "path": str(py_file.relative_to(repo_root)), "name": py_file.name})
    return prompts


def build_inventory(repo_root: Path = ROOT) -> Dict[str, Any]:
    """Build complete SSOT configuration inventory dictionary."""
    ingestion_scripts = scan_ingestion_scripts(repo_root)
    thresholds = scan_threshold_literals(repo_root)
    prompt_sources = scan_prompt_sources(repo_root)
    ast_inv = scan_governed_ast_inventory(repo_root)

    return {
        "schema_version": "2.0.0",
        "summary": {
            "total_ingestion_scripts": len(ingestion_scripts),
            "threshold_comparison_locations": len(thresholds),
            "prompt_sources_and_consumers": len(prompt_sources),
            "hardcoded_config_constants_count": len(ast_inv["hardcoded_config_constants"]),
            "default_parameter_bounds_count": len(ast_inv["default_parameter_bounds"]),
            "step_table_functions_count": len(ast_inv["step_table_functions"]),
            "duplicate_pattern_clusters_count": len(ast_inv["duplicate_pattern_clusters"]),
        },
        "ingestion_scripts": ingestion_scripts,
        "threshold_comparison_locations": thresholds,
        "prompt_sources": prompt_sources,
        **ast_inv,
    }


def main() -> int:
    inventory = build_inventory(ROOT)
    out_path = ROOT / "reports" / "ssot_inventory.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(inventory, indent=2)
    out_path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    s = inventory["summary"]
    print(
        f"Wrote inventory to {out_path} (sha256={digest[:12]}, "
        f"constants={s['hardcoded_config_constants_count']}, "
        f"defaults={s['default_parameter_bounds_count']}, "
        f"step_tables={s['step_table_functions_count']}, "
        f"pattern_clusters={s['duplicate_pattern_clusters_count']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
