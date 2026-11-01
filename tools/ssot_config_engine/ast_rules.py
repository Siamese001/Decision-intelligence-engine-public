"""
tools/ssot_config_engine/ast_rules.py

Unified AST rule definitions for hardcoded vendor/local model strings,
governed SSOT parameter default/fallback bypasses, and `# noqa: SSOT_EXEMPT`
enforcement.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set

PROHIBITED_MODEL_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^gpt-[0-9]+(?:\.[0-9]+)?(?:-[a-z0-9\-]+)?$", re.IGNORECASE),
    re.compile(r"^gpt-4o(?:-[a-z0-9\-]+)?$", re.IGNORECASE),
    re.compile(r"^o[13](?:-[a-z0-9\-]+)?$", re.IGNORECASE),
    re.compile(r"^claude-3-[0-9a-z\-]+$", re.IGNORECASE),
    re.compile(r"^gemini-[0-9\.]+(?:-[a-z0-9\-]+)?$", re.IGNORECASE),
    re.compile(r"^BAAI/bge-m3$", re.IGNORECASE),
    re.compile(r"^pyannote/speaker-diarization-[0-9\.]+$", re.IGNORECASE),
    re.compile(r"^mlx-community/whisper-[a-z0-9\-]+$", re.IGNORECASE),
    re.compile(r"^mlx-community/[a-z0-9._\-]+$", re.IGNORECASE),
]

PROHIBITED_HITL_BADGE_PATTERN: re.Pattern[str] = re.compile(
    r"\[\d{1,3}%\s*[—\-]\s*(?:HIGH|MEDIUM|LOW)\]",
    re.IGNORECASE,
)

PROHIBITED_DOMAIN_ENTITY_PATTERN: re.Pattern[str] = re.compile(
    r"(?:Dan\\s\+Okray|Project\\s\+Lens.*Project\\s\+Nexus|Project\\s\+Nexus.*Project\\s\+Jarvis)",
    re.IGNORECASE,
)

UNAMBIGUOUS_LEAF_FALLBACK_KEYS: Set[str] = {
    "ambiguity_margin_threshold",
    "consensus_threshold",
    "max_payload_retries",
    "copilot_request_timeout",
    "divergence_margin_threshold",
    "min_autonomous_confidence_floor",
    "entropy_ambiguity_threshold",
    "unresolved_entity_margin_threshold",
    "sqlite_wal_busy_timeout_ms",
    "retrieval_steady_state_latency_ms",
    "output1_production_pass_threshold",
    "output2_production_pass_threshold",
    "output3_production_pass_threshold",
    "delivery_max_words",
    "max_plan_words",
    "max_scaled_plan_words",
    "progress_max_words",
    "speaker_summary_max_words",
    "supporting_details_max_words",
    "total_content_max_words",
    "max_sentence_words",
    "pr_queue_max_words",
    "ssot_pointer_max_words",
}


def collect_declared_keys(data: Any, prefix: str = "") -> Set[str]:
    keys: Set[str] = set()
    if isinstance(data, dict):
        for k, v in data.items():
            full = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                keys.update(collect_declared_keys(v, full))
            else:
                keys.add(full)
    return keys


def _collect_dotted_keys(data: Any, prefix: str = "") -> Set[str]:
    return {k.lower() for k in collect_declared_keys(data, prefix)}


def load_declared_parameter_keys(param_path: Optional[Path] = None) -> Set[str]:
    target = param_path or (Path(__file__).resolve().parents[2] / "config" / "runtime" / "parameters.json")
    if not target.is_file():
        return set()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        params = raw.get("parameters", raw) if isinstance(raw, dict) else {}
        return collect_declared_keys(params)
    except Exception:
        return set()


DECLARED_PARAMETER_KEYS: Set[str] = load_declared_parameter_keys()


def _load_governed_parameter_fallback_keys() -> Set[str]:
    keys = set(UNAMBIGUOUS_LEAF_FALLBACK_KEYS)
    keys.update(k.lower() for k in DECLARED_PARAMETER_KEYS)
    return keys


GOVERNED_PARAMETER_FALLBACK_KEYS: Set[str] = _load_governed_parameter_fallback_keys()

TARGET_PARAMETER_FUNCTIONS: Set[str] = {
    "get_parameter",
    "get_int_parameter",
    "get_float_parameter",
    "get_str_parameter",
    "get_bool_parameter",
}

FORBIDDEN_STATIC_FLOAT_VARS: Set[str] = {"base_brier", "sim_brier"}

FORBIDDEN_LOCAL_WORD_LIMIT_FUNCS: Set[str] = {
    "_get_tier1_word_limits",
    "_get_tier3_word_limits",
    "_get_default_word_limits",
    "_get_word_count_limits",
}


class SSOTASTViolation(NamedTuple):
    file_path: str
    line_number: int
    rule: str
    detail: str


def is_ssot_exempt_comment(line_text: str) -> bool:
    """Only explicit `# noqa: SSOT_EXEMPT`, `# noqa: model-string-allowed`, or `# allow-retired-model` bypass SSOT AST checks."""
    return bool(
        re.search(r"#\s*noqa:\s*(?:SSOT_EXEMPT|model-string-allowed)\b", line_text, re.IGNORECASE)
        or "# allow-retired-model" in line_text
    )


def _is_numeric_or_str_literal(node: Optional[ast.AST]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)) and not isinstance(node.value, bool):
        return True
    return False


class UnifiedSSOTASTVisitor(ast.NodeVisitor):
    """AST visitor enforcing zero hardcoded models, thresholds, or parameter fallbacks."""

    def __init__(
        self,
        rel_path: str,
        lines: Sequence[str],
        ssot_confined_assignments: Optional[Dict[str, Set[str]]] = None,
        declared_keys: Optional[Set[str]] = None,
        check_undeclared_keys: bool = False,
    ) -> None:
        self.rel_path = rel_path
        self.lines = list(lines)
        self.ssot_confined_assignments = ssot_confined_assignments or {}
        self.declared_keys: Set[str] = declared_keys if declared_keys is not None else DECLARED_PARAMETER_KEYS
        self.check_undeclared_keys = check_undeclared_keys
        self.referenced_keys: Set[str] = set()
        self.is_test = rel_path.startswith("tests/") or "test_" in rel_path
        self.violations: List[SSOTASTViolation] = []
        self.docstrings: Set[int] = set()

    def _line_is_exempt(self, lineno: int) -> bool:
        if 0 <= lineno - 1 < len(self.lines):
            return is_ssot_exempt_comment(self.lines[lineno - 1])
        return False

    def _record_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            for ln in range( getattr(body[0], "lineno", 1), getattr(body[0], "end_lineno", getattr(body[0], "lineno", 1)) + 1):
                self.docstrings.add(ln)

    def visit_Module(self, node: ast.Module) -> None:
        self._record_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_docstring(node)
        self._check_function_node(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_docstring(node)
        self._check_function_node(node)
        self.generic_visit(node)

    def _check_function_node(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name in FORBIDDEN_LOCAL_WORD_LIMIT_FUNCS and self.rel_path not in {
            "dual_pipeline/parameters.py",
            "dual_pipeline/parameters_ops.py",
        }:
            self.violations.append(
                SSOTASTViolation(
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    rule="LOCAL_WORD_LIMIT_FUNCTION",
                    detail=(
                        f"Local word-limit function '{node.name}' defined outside SSOT. "
                        "Consume get_output2_*_word_limits from dual_pipeline.parameters."
                    ),
                )
            )
        self._check_callable_defaults(node)

    def _check_callable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        num_defaults = len(node.args.defaults)
        pairs: List[tuple[ast.arg, Optional[ast.expr]]] = []
        if num_defaults > 0:
            pairs.extend(zip(node.args.args[-num_defaults:], node.args.defaults, strict=False))
        pairs.extend(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False))
        for arg, default_expr in pairs:
            if arg.arg.lower() in GOVERNED_PARAMETER_FALLBACK_KEYS and _is_numeric_or_str_literal(default_expr):
                lineno = getattr(default_expr, "lineno", node.lineno)
                if not self._line_is_exempt(lineno):
                    self.violations.append(
                        SSOTASTViolation(
                            file_path=self.rel_path,
                            line_number=lineno,
                            rule="HARDCODED_PARAMETER_DEFAULT",
                            detail=(
                                f"Governed parameter '{arg.arg}' in '{node.name}' has hardcoded default "
                                f"literal {getattr(default_expr, 'value', None)!r}. Default to None and resolve via ParameterRegistry."
                            ),
                        )
                    )

    def _check_parameter_reference_call(self, node: ast.Call) -> None:
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            receiver = ""
            if isinstance(node.func.value, ast.Name):
                receiver = node.func.value.id.lower()
            elif isinstance(node.func.value, ast.Attribute):
                receiver = node.func.value.attr.lower()
            if receiver in ("parameter_registry", "param_registry", "parameters_registry"):
                func_name = "get_parameter"

        if func_name in TARGET_PARAMETER_FUNCTIONS and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                key = first_arg.value
                self.referenced_keys.add(key)
                if self.check_undeclared_keys and not self.is_test and key not in self.declared_keys:
                    self.violations.append(
                        SSOTASTViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            rule="UNDECLARED_PARAMETER_KEY",
                            detail=f"Key '{key}' is not declared in config/runtime/parameters.json",
                        )
                    )

    def visit_Call(self, node: ast.Call) -> None:
        self._check_parameter_reference_call(node)
        if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            key_str = node.args[0].value.strip().lower()
            key_tail = key_str.split(".")[-1]
            if (key_str in GOVERNED_PARAMETER_FALLBACK_KEYS or key_tail in GOVERNED_PARAMETER_FALLBACK_KEYS) and _is_numeric_or_str_literal(node.args[1]):
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "getenv"):
                    is_param_reg = isinstance(node.func.value, ast.Name) and "param" in node.func.value.id.lower()
                    if not is_param_reg and not self._line_is_exempt(node.lineno):
                        self.violations.append(
                            SSOTASTViolation(
                                file_path=self.rel_path,
                                line_number=node.lineno,
                                rule="HARDCODED_PARAMETER_FALLBACK",
                                detail=(
                                    f"Fallback literal {getattr(node.args[1], 'value', None)!r} supplied for governed parameter "
                                    f"'{node.args[0].value}'. Resolve via ParameterRegistry."
                                ),
                            )
                        )
        self.generic_visit(node)

    def _check_target_name(self, name: str, lineno: int) -> None:
        allowed_files = self.ssot_confined_assignments.get(name)
        if allowed_files is not None and self.rel_path not in allowed_files and not self._line_is_exempt(lineno):
            self.violations.append(
                SSOTASTViolation(
                    file_path=self.rel_path,
                    line_number=lineno,
                    rule="DUPLICATE_SSOT_CONSTANT",
                    detail=(
                        f"Constant or pattern table '{name}' is defined outside SSOT "
                        f"({sorted(allowed_files)}). Import from SSOT instead."
                    ),
                )
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        is_num_const = isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float))
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._check_target_name(target.id, node.lineno)
                if is_num_const and target.id in FORBIDDEN_STATIC_FLOAT_VARS and not self._line_is_exempt(node.lineno):
                    self.violations.append(
                        SSOTASTViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            rule="HARDCODED_BRIER_CONSTANT",
                            detail=f"Hardcoded numeric literal assigned to '{target.id}'. Compute Brier scores empirically from store records.",
                        )
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._check_target_name(node.target.id, node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        line_no = getattr(node, "lineno", 1)
        if line_no in self.docstrings or self._line_is_exempt(line_no):
            return
        if isinstance(node.value, str):
            val = node.value.strip()
            for pattern in PROHIBITED_MODEL_PATTERNS:
                if pattern.match(val):
                    self.violations.append(
                        SSOTASTViolation(
                            file_path=self.rel_path,
                            line_number=line_no,
                            rule="HARDCODED_MODEL_STRING",
                            detail=f"Hardcoded model identifier '{val}' found. Bind to ModelTier and resolve via ModelRegistry.",
                        )
                    )
                    break
            if PROHIBITED_HITL_BADGE_PATTERN.search(val):
                self.violations.append(
                    SSOTASTViolation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        rule="HARDCODED_HITL_CONFIDENCE_BADGE",
                        detail=f"Hardcoded legacy HITL confidence badge in '{val[:60]}'. Use QuestionBuilder and format_option_text.",
                    )
                )
            if PROHIBITED_DOMAIN_ENTITY_PATTERN.search(val) and self.rel_path != "tools/ssot_config_engine/ast_rules.py":
                self.violations.append(
                    SSOTASTViolation(
                        file_path=self.rel_path,
                        line_number=line_no,
                        rule="HARDCODED_DOMAIN_ENTITY_PATTERN",
                        detail=(
                            f"Hardcoded domain project/stakeholder pattern in '{val[:60]}'. "
                            "Resolve dynamically from config/governance/project_registry.json."
                        ),
                    )
                )
        self.generic_visit(node)
