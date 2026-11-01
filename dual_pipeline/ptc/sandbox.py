"""
dual_pipeline/ptc/sandbox.py

Governed in-process sandbox with AST static inspection and timeout enforcement.
Prevents unauthorized file/network I/O, secret leakages, and raw sqlite3 access.
Layer: L2 (Execution Isolation & Sanitization).
"""
from __future__ import annotations

import ast
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Dict, Optional, Set

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCExecutionError,
    PTCResultSummary,
    PTCSecurityViolationError,
    PTCStatus,
)
from dual_pipeline.ptc.spooler import PTCOutputSpooler

FORBIDDEN_MODULES: Set[str] = {
    "os", "sys", "subprocess", "shutil", "socket", "requests", "urllib",
    "sqlite3", "psycopg2", "ctypes", "multiprocessing", "threading",
}

FORBIDDEN_CALLS: Set[str] = {
    "eval", "exec", "compile", "__import__", "globals", "locals",
    "breakpoint", "help", "input",
}

SAFE_BUILTINS: Dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "filter": filter, "float": float, "int": int,
    "isinstance": isinstance, "issubclass": issubclass, "len": len,
    "list": list, "map": map, "max": max, "min": min, "range": range,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "getattr": getattr, "hasattr": hasattr, "repr": repr,
    "iter": iter, "next": next, "hash": hash, "slice": slice,
    "True": True, "False": False, "None": None,
    "Exception": Exception, "ValueError": ValueError, "KeyError": KeyError,
    "TypeError": TypeError, "RuntimeError": RuntimeError, "IndexError": IndexError,
    "AttributeError": AttributeError, "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError, "ImportError": ImportError,
}


class PTCAstValidator(ast.NodeVisitor):
    """Static AST inspector enforcing repository security invariants on PTC scripts."""

    def __init__(self, allowed_modules: Optional[Set[str]] = None) -> None:
        self.allowed_modules = allowed_modules or {
            "math", "json", "re", "datetime", "collections", "itertools", "dataclasses", "typing"
        }

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_mod = alias.name.split(".")[0]
            if base_mod in FORBIDDEN_MODULES or base_mod not in self.allowed_modules:
                raise PTCSecurityViolationError(
                    f"FORBIDDEN_IMPORT: Import of module '{alias.name}' is prohibited in PTC sandbox."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base_mod = node.module.split(".")[0]
            if base_mod in FORBIDDEN_MODULES or base_mod not in self.allowed_modules:
                raise PTCSecurityViolationError(
                    f"FORBIDDEN_IMPORT_FROM: Import from module '{node.module}' is prohibited in PTC sandbox."
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            raise PTCSecurityViolationError(
                f"FORBIDDEN_CALL: Direct call to function '{node.func.id}' is prohibited in PTC sandbox."
            )
        self.generic_visit(node)


class PTCSandbox:
    """Governed execution sandbox executing programmatic tool scripts."""

    def __init__(self, spooler: Optional[PTCOutputSpooler] = None) -> None:
        self.spooler = spooler or PTCOutputSpooler()

    def validate_script(self, code_str: str, allowed_modules: Optional[Set[str]] = None) -> None:
        """Parses and runs AST security checks on the provided code string."""
        try:
            tree = ast.parse(code_str)
        except SyntaxError as syntax_err:
            raise PTCExecutionError(f"PTC_SYNTAX_ERROR: {syntax_err}") from syntax_err

        validator = PTCAstValidator(allowed_modules=allowed_modules)
        validator.visit(tree)

    def execute(
        self,
        code_str: str,
        context: PTCExecutionContext,
        entrypoint: str = "run",
    ) -> PTCResultSummary:
        """Executes the programmatic script within the governed sandbox with timeout."""
        start_time = time.perf_counter()

        # 1. AST Validation
        try:
            self.validate_script(code_str, allowed_modules=set(context.allowed_modules))
        except PTCSecurityViolationError as sec_err:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return PTCResultSummary(
                status=PTCStatus.BLOCKED,
                task_id=context.task_id,
                execution_time_ms=elapsed,
                payload_bytes=0,
                error_message=str(sec_err),
            )
        except PTCExecutionError as exec_err:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return PTCResultSummary(
                status=PTCStatus.ERROR,
                task_id=context.task_id,
                execution_time_ms=elapsed,
                payload_bytes=0,
                error_message=str(exec_err),
            )

        # 2. Prepare Sandbox Global Scope
        orig_import = __import__
        allowed_set = set(context.allowed_modules)

        def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
            base_mod = name.split(".")[0]
            if base_mod in FORBIDDEN_MODULES or base_mod not in allowed_set:
                raise PTCSecurityViolationError(f"FORBIDDEN_IMPORT: Import of '{name}' is prohibited in PTC sandbox.")
            return orig_import(name, globals, locals, fromlist, level)

        builtins_dict = SAFE_BUILTINS.copy()
        builtins_dict["__import__"] = _safe_import
        sandbox_globals: Dict[str, Any] = {"__builtins__": builtins_dict}
        for mod_name in context.allowed_modules:
            try:
                sandbox_globals[mod_name] = importlib.import_module(mod_name)
            except ImportError:
                pass

        # Inject proxies (e.g. env, graph, retrieval)
        for proxy_name, proxy_obj in context.proxies.items():
            sandbox_globals[proxy_name] = proxy_obj

        # 3. Compile and Execute with Timeout
        def _target_runner() -> Any:
            local_scope: Dict[str, Any] = {}
            compiled = compile(code_str, f"<ptc_sandbox_{context.task_id}>", "exec")
            exec(compiled, sandbox_globals, local_scope)

            # Locate return value or entrypoint function
            if entrypoint in local_scope and callable(local_scope[entrypoint]):
                return local_scope[entrypoint](**context.proxies)
            elif "result" in local_scope:
                return local_scope["result"]
            return local_scope

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_target_runner)
            try:
                raw_result = future.result(timeout=context.timeout_seconds)
            except FuturesTimeoutError:
                elapsed = (time.perf_counter() - start_time) * 1000.0
                return PTCResultSummary(
                    status=PTCStatus.TIMEOUT,
                    task_id=context.task_id,
                    execution_time_ms=elapsed,
                    payload_bytes=0,
                    error_message=f"EXECUTION_TIMEOUT: Exceeded {context.timeout_seconds}s limit.",
                )
            except Exception as run_err:
                elapsed = (time.perf_counter() - start_time) * 1000.0
                return PTCResultSummary(
                    status=PTCStatus.ERROR,
                    task_id=context.task_id,
                    execution_time_ms=elapsed,
                    payload_bytes=0,
                    error_message=f"RUNTIME_ERROR: {type(run_err).__name__}: {run_err}",
                )

        elapsed = (time.perf_counter() - start_time) * 1000.0

        # 4. Marshal Output through Spooler
        return self.spooler.marshal(
            task_id=context.task_id,
            data=raw_result,
            token_budget=context.token_budget,
            runtime_root=context.runtime_root,
            execution_time_ms=elapsed,
        )
