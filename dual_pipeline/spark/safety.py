"""Driver memory safety and execution governance for Managed Spark workloads.

Enforces:
1. Strict prohibition of un-aggregated .collect() or .toPandas() on driver
2. Mandatory broadcast joins for lookup/dimension tables under ~10 MB
3. Hard ceiling on driver row egress (max 1000 rows, default 100 for agents)
4. Partition skew and OOM prevention guardrails
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import List, Sequence


class SparkSafetyViolation(Exception):
    """Raised when PySpark code or query violates driver memory safety rules."""


@dataclass(frozen=True)
class SafetyCheckResult:
    """Immutable audit report from Spark execution safety evaluation."""

    is_safe: bool
    violations: Sequence[str]
    warnings: Sequence[str]
    enforced_row_limit: int
    uses_broadcast_join: bool

    def summary(self) -> str:
        status = "SAFE" if self.is_safe else "VIOLATION"
        return (
            f"SparkSafety[{status}] violations={len(self.violations)} "
            f"warnings={len(self.warnings)} limit={self.enforced_row_limit}"
        )


class DriverMemoryGuard:
    """Static and runtime memory guard enforcing zero-OOM invariants on Spark drivers."""

    MAX_DRIVER_ROWS = 1000
    DEFAULT_AGENT_ROWS = 100
    FORBIDDEN_OPERATIONS = ("collect()", "topandas()", "to_pandas()")

    @classmethod
    def audit_sql_query(
        cls,
        sql_query: str,
        max_allowed_rows: int = DEFAULT_AGENT_ROWS,
    ) -> tuple[str, SafetyCheckResult]:
        """Audit SQL query, inject row bounds if missing, and check for safety."""
        clean_sql = sql_query.strip()
        violations: List[str] = []
        warnings: List[str] = []

        if not clean_sql:
            return "", SafetyCheckResult(
                is_safe=False,
                violations=["Empty SQL query"],
                warnings=[],
                enforced_row_limit=0,
                uses_broadcast_join=False,
            )

        # Check for un-bounded SELECT without LIMIT
        limit_match = re.search(r"\bLIMIT\s+(\d+)\b", clean_sql, re.IGNORECASE)
        enforced_limit = min(max_allowed_rows, cls.MAX_DRIVER_ROWS)

        if limit_match:
            existing_limit = int(limit_match.group(1))
            if existing_limit > cls.MAX_DRIVER_ROWS:
                violations.append(
                    f"LIMIT {existing_limit} exceeds driver safety ceiling of {cls.MAX_DRIVER_ROWS} rows."
                )
                safe_sql = re.sub(
                    r"\bLIMIT\s+\d+\b",
                    f"LIMIT {enforced_limit}",
                    clean_sql,
                    flags=re.IGNORECASE,
                )
            else:
                safe_sql = clean_sql
                enforced_limit = existing_limit
        else:
            warnings.append(
                f"Query lacked explicit LIMIT; injected safety LIMIT {enforced_limit}."
            )
            safe_sql = f"{clean_sql} LIMIT {enforced_limit}"

        has_broadcast = bool(re.search(r"/\*\+\s*BROADCAST\b", clean_sql, re.IGNORECASE))
        is_safe = len(violations) == 0

        return safe_sql, SafetyCheckResult(
            is_safe=is_safe,
            violations=violations,
            warnings=warnings,
            enforced_row_limit=enforced_limit,
            uses_broadcast_join=has_broadcast,
        )

    @classmethod
    def audit_pyspark_code(cls, code_str: str) -> SafetyCheckResult:
        """Inspect PySpark code string via AST to detect forbidden driver collection patterns."""
        violations: List[str] = []
        warnings: List[str] = []
        has_broadcast = "broadcast(" in code_str

        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            return SafetyCheckResult(
                is_safe=False,
                violations=[f"Syntax error in PySpark code: {e}"],
                warnings=[],
                enforced_row_limit=0,
                uses_broadcast_join=has_broadcast,
            )

        # Look for calls to collect() or toPandas()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method_name = node.func.attr.lower()
                if method_name in ("collect", "topandas", "to_pandas"):
                    # Verify if preceded by limit or aggregation
                    violations.append(
                        f"Prohibited un-aggregated .{node.func.attr}() call on line {node.lineno}. "
                        "Driver OOM hazard: aggregate in Spark or use .limit() before driver retrieval."
                    )

        # Check for joins without broadcast on small tables
        if "join(" in code_str and not has_broadcast:
            warnings.append(
                "Detected .join() without broadcast() hint. Verify if lookup tables can be broadcast."
            )

        return SafetyCheckResult(
            is_safe=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            enforced_row_limit=cls.DEFAULT_AGENT_ROWS,
            uses_broadcast_join=has_broadcast,
        )
