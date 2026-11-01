"""
tools/ci_gates_core/runner.py

Execution runner, process tree management, and receipt generation for CI gate profiles.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from dual_pipeline.core import (
    canonical_bytes,
    sha256_bytes,
    sha256_file,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]


_RESOLVED_CI_PYTHON_INTERPRETER: str | None = None


def resolve_python_interpreter() -> str:
    """Find the preferred Python interpreter with all virtualenv dependencies."""
    global _RESOLVED_CI_PYTHON_INTERPRETER
    if _RESOLVED_CI_PYTHON_INTERPRETER is not None:
        return _RESOLVED_CI_PYTHON_INTERPRETER

    # If already running inside virtualenv, reuse sys.executable directly
    if sys.prefix != sys.base_prefix and Path(sys.executable).is_file():
        _RESOLVED_CI_PYTHON_INTERPRETER = sys.executable
        return sys.executable

    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and os.access(venv_py, os.X_OK):
        _RESOLVED_CI_PYTHON_INTERPRETER = str(venv_py)
        return _RESOLVED_CI_PYTHON_INTERPRETER
    win_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if win_py.exists():
        _RESOLVED_CI_PYTHON_INTERPRETER = str(win_py)
        return _RESOLVED_CI_PYTHON_INTERPRETER
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            c_dir = Path(res.stdout.strip())
            c_root = (
                (ROOT / c_dir).resolve().parent
                if not c_dir.is_absolute()
                else c_dir.resolve().parent
            )
            c_py = c_root / ".venv" / "bin" / "python"
            if c_py.exists() and os.access(c_py, os.X_OK):
                _RESOLVED_CI_PYTHON_INTERPRETER = str(c_py)
                return _RESOLVED_CI_PYTHON_INTERPRETER
    except Exception:
        pass
    _RESOLVED_CI_PYTHON_INTERPRETER = sys.executable
    return _RESOLVED_CI_PYTHON_INTERPRETER


def _claim_profile_output_root(output_root: Path) -> Path:
    """Reserve one immutable profile output root for this execution."""
    if output_root.exists() and output_root.resolve() == (ROOT / ".local/ci").resolve():
        import shutil

        shutil.rmtree(output_root, ignore_errors=True)

    try:
        output_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise FileExistsError(
            f"CI profile output root must be a new directory: {output_root}"
        ) from exc
    return output_root


def verify_ci_test_list_integrity(
    repository_root: Path,
    all_tests: Sequence[str],
) -> list[str]:
    """Verify that all tests exist on disk and contain no slashes or .py extensions."""
    errors = []
    for test_name in all_tests:
        if "/" in test_name or "\\" in test_name:
            errors.append(f"Test specifier contains slash: {test_name}")
            continue
        if test_name.endswith(".py"):
            errors.append(f"Test specifier contains .py suffix: {test_name}")
            continue
        parts = test_name.split(".")
        found = False
        for i in range(len(parts), 0, -1):
            cand = repository_root / ("/".join(parts[:i]) + ".py")
            if cand.is_file():
                found = True
                break
        if not found:
            errors.append(f"Test module not found on disk: {test_name}")
    return errors


def bge_environment(
    *,
    base_environment: Mapping[str, str],
    model_path: Path | None,
    manifest_path: Path | None,
) -> dict[str, str]:
    """Optionally bind the real-model profile to one explicit pinned snapshot."""
    if (model_path is None) != (manifest_path is None):
        raise ValueError("--bge-model-path and --bge-model-manifest must be supplied together")
    environment = dict(base_environment)
    if model_path is None:
        return environment
    resolved_model_path = model_path.expanduser().resolve()
    resolved_manifest_path = manifest_path.expanduser().resolve()
    if not resolved_model_path.is_dir():
        raise ValueError(f"BGE-M3 model path is not a directory: {resolved_model_path}")
    if not resolved_manifest_path.is_file():
        raise ValueError(f"BGE-M3 model manifest is not a file: {resolved_manifest_path}")
    environment["NEE_BGE_M3_MODEL_PATH"] = str(resolved_model_path)
    environment["NEE_BGE_M3_MODEL_MANIFEST"] = str(resolved_manifest_path)
    return environment


def _process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
    return {"start_new_session": True}


def _terminate_process_tree(process: subprocess.Popen[object]) -> None:
    """Terminate a timed-out gate and any model-worker descendants."""
    import tools.run_ci_gates as rc

    subproc = getattr(rc, "subprocess", subprocess)
    os_mod = getattr(rc, "os", os)

    if os_mod.name == "nt":
        subproc.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
    try:
        process.wait(timeout=5)
    except subproc.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except Exception:
            pass


def _init_progress_bar(total: int) -> Any | None:
    if tqdm is not None and total > 0:
        return tqdm(
            total=total,
            desc=f"CI Gate Stages (0/{total})",
            unit="gate",
            file=sys.stdout,
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
        )
    return None


def _record_gate_result(
    gate_label: str,
    command_result: dict[str, object],
    command: Sequence[str],
    repository_root: Path,
) -> None:
    try:
        from tools.gate_run_ledger import record_gate_run

        record_gate_run(
            gate_name=gate_label,
            tier="ci_gates",
            wall_clock_seconds=float(command_result.get("elapsed_seconds", 0.0)),
            status=str(command_result.get("status", "UNKNOWN")),
            metadata={"command": " ".join(command)[:200]},
            repo_root=repository_root,
        )
    except Exception:
        pass


def _parse_log_test_counts(path: Path) -> dict[str, int] | None:
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
        res: dict[str, int] = {}
        for m in re.finditer(r"(\d+)\s+(passed|skipped|failed|error[s]?)", txt, re.IGNORECASE):
            key = m.group(2).lower().rstrip("s")
            res[key] = int(m.group(1))
        for m in re.finditer(r"(passed|failed|errors?|skipped)\s*[:=]\s*(\d+)", txt, re.IGNORECASE):
            key = m.group(1).lower().rstrip("s")
            res[key] = int(m.group(2))
        coll = re.search(r"(?:collected|Total)\s*[:=]?\s*(\d+)", txt, re.IGNORECASE)
        if coll:
            res["collected"] = int(coll.group(1))
        return res if res else None
    except Exception:
        return None


def _format_gate_row(idx: int, item: Mapping[str, object]) -> tuple[str, int, int, int]:
    status = str(item.get("status", "UNKNOWN"))
    elapsed = float(item.get("elapsed_seconds", 0.0))
    cmd = item.get("command") or []
    target = cmd[2] if len(cmd) > 2 else f"gate_{idx}"
    target_name = Path(target).name
    counts = item.get("test_counts") or {}
    p = counts.get("passed", 0) if isinstance(counts, dict) else 0
    f = (counts.get("failed", 0) + counts.get("error", 0)) if isinstance(counts, dict) else 0
    s = counts.get("skipped", 0) if isinstance(counts, dict) else 0
    counts_str = f"Passed: {p}, Failed: {f}, Skipped: {s}" if counts else "No test count"
    line = f"  Gate {idx:02d} [{status}]: {target_name:<32} in {elapsed:.2f}s | {counts_str}"
    return line, p, f, s


def _print_profile_summary(
    command_results: Sequence[Mapping[str, object]],
    total_elapsed_seconds: float,
) -> None:
    """Surface per-suite runtime distributions and test assertions in stdout."""
    print("\n" + "=" * 80)
    print("📊 CI Gate Profile Execution & Runtime Distribution Summary")
    print("=" * 80)
    total_p, total_f, total_s = 0, 0, 0
    any_failed = False
    for idx, item in enumerate(command_results, start=1):
        line, p, f, s = _format_gate_row(idx, item)
        total_p += p
        total_f += f
        total_s += s
        if item.get("status") in ("FAIL", "TIMEOUT") or f > 0:
            any_failed = True
        print(line)
    print("-" * 80)
    overall_status = "FAIL" if any_failed else "PASS"
    print(
        f"Total Wall-Clock: {total_elapsed_seconds:.2f}s | "
        f"Gates: {len(command_results)} | Tests: {total_p} passed, {total_f} failed, {total_s} skipped | "
        f"Overall: [{overall_status}]"
    )
    print("=" * 80 + "\n", flush=True)


def _evaluate_command_outcome(return_code: int, counts: dict[str, int] | None) -> str:
    if return_code != 0:
        return "FAIL"
    if counts and counts.get("collected", 0) > 0 and counts.get("passed", 0) == 0 and counts.get("skipped", 0) > 0:
        return "FAIL"
    return "PASS"


def _read_test_counts(log_root: Path | None, command_index: int) -> dict[str, int] | None:
    if log_root is None:
        return None
    lp = log_root / f"{command_index:02d}-gate.log"
    return _parse_log_test_counts(lp) if lp.is_file() else None


def _execute_single_command(
    command: Sequence[str],
    command_index: int,
    repository_root: Path,
    environment: Mapping[str, str] | None,
    command_timeout: float,
    log_root: Path | None,
) -> tuple[int, dict[str, object], str]:
    started = time.monotonic()
    gate_label = "gate"
    if len(command) > 2:
        target = command[2]
        gate_label = (
            Path(target).name if "." in target or "/" in target or "\\" in target else target
        )

    command_result: dict[str, object] = {
        "command_index": command_index,
        "command": list(command),
        "timeout_seconds": command_timeout,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    log_handle = None
    if log_root is not None:
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / f"{command_index:02d}-gate.log"
        log_handle = log_path.open("w", encoding="utf-8")
        command_result["log_path"] = str(log_path)
    try:
        import tools.run_ci_gates as rc

        subproc = getattr(rc, "subprocess", subprocess)
        term_fn = getattr(rc, "_terminate_process_tree", _terminate_process_tree)
        popen_options = _process_group_options()
        if log_handle is not None:
            popen_options.update({"stdout": log_handle, "stderr": subproc.STDOUT})
        process = subproc.Popen(
            command,
            cwd=repository_root,
            env=environment,
            **popen_options,
        )
        try:
            return_code = process.wait(timeout=command_timeout)
        except subproc.TimeoutExpired:
            elapsed = time.monotonic() - started
            term_fn(process)
            command_result.update(
                {
                    "status": "TIMEOUT",
                    "return_code": 124,
                    "elapsed_seconds": elapsed,
                }
            )
            print(
                f"TIMEOUT: command exceeded {command_timeout:g}s after {elapsed:.1f}s",
                flush=True,
            )
            return 124, command_result, gate_label

        if log_handle is not None:
            log_handle.close()
            log_handle = None

        counts = _read_test_counts(log_root, command_index)
        if counts:
            command_result["test_counts"] = counts

        command_result.update(
            {
                "status": _evaluate_command_outcome(return_code, counts),
                "return_code": return_code,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return return_code, command_result, gate_label
    finally:
        if log_handle is not None:
            log_handle.close()


def run_commands(
    *,
    commands: Sequence[Sequence[str]],
    repository_root: Path,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float,
    total_timeout_seconds: float,
    command_results: list[dict[str, object]] | None = None,
    log_root: Path | None = None,
) -> int:
    profile_started = time.monotonic()
    pbar = _init_progress_bar(len(commands))

    try:
        for command_index, command in enumerate(commands, start=1):
            remaining_seconds = total_timeout_seconds - (time.monotonic() - profile_started)
            if remaining_seconds <= 0:
                print(
                    f"TIMEOUT: gate profile exceeded {total_timeout_seconds:g}s",
                    flush=True,
                )
                if command_results is not None:
                    command_results.append(
                        {
                            "status": "TIMEOUT",
                            "reason": "PROFILE_TIMEOUT",
                            "elapsed_seconds": time.monotonic() - profile_started,
                        }
                    )
                return 124
            command_timeout = min(timeout_seconds, remaining_seconds)
            print(
                "+",
                " ".join(command),
                f"[timeout={command_timeout:g}s, profile-limit={total_timeout_seconds:g}s]",
                flush=True,
            )

            return_code, command_result, gate_label = _execute_single_command(
                command, command_index, repository_root, environment, command_timeout, log_root
            )
            if command_results is not None:
                command_results.append(command_result)
            _record_gate_result(gate_label, command_result, command, repository_root)
            print(
                f"result: {command_result['status']} "
                f"after {float(command_result['elapsed_seconds']):.1f}s",
                flush=True,
            )
            if pbar is not None:
                pbar.set_description(f"Gate [{command_index}/{len(commands)}]: {gate_label}")
                pbar.update(1)
                pbar.set_postfix(status=command_result["status"])
            if return_code:
                if command_results:
                    _print_profile_summary(command_results, time.monotonic() - profile_started)
                return return_code
        if command_results:
            _print_profile_summary(command_results, time.monotonic() - profile_started)
        return 0
    finally:
        if pbar is not None:
            pbar.close()


def _aggregate_test_counts(command_results: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
    aggregated: dict[str, int] = {}
    for item in command_results:
        tc = item.get("test_counts")
        if isinstance(tc, dict):
            for k, v in tc.items():
                if isinstance(v, int):
                    aggregated[k] = aggregated.get(k, 0) + v
    return aggregated or None


def _resolve_profile_status(status_code: int, command_results: Sequence[Mapping[str, object]]) -> str:
    if status_code != 0 or any(item.get("status") in ("FAIL", "TIMEOUT") for item in command_results):
        return "FAIL"
    return "PASS"


def _resolve_terminal_reason(receipt_status: str, command_results: Sequence[Mapping[str, object]]) -> str:
    if receipt_status == "PASS":
        return "COMPLETED"
    if any(item.get("status") == "TIMEOUT" for item in command_results):
        return "TIMEOUT"
    return "GATE_FAILURE"


def _write_profile_receipt(
    *,
    path: Path,
    profile: str,
    repository_root: Path,
    output_root: Path,
    command_timeout_seconds: float,
    total_timeout_seconds: float,
    command_results: list[dict[str, object]],
    status_code: int,
    started_at: str,
) -> Path:
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    status_result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    receipt_status = _resolve_profile_status(status_code, command_results)
    graph_path = repository_root / "graph" / "nee_graph.sqlite"
    receipt = {
        "schema_version": "1.0",
        "artifact_type": "ci_gate_profile_receipt",
        "profile": profile,
        "repository_root": str(repository_root),
        "repository_revision": (
            revision_result.stdout.strip() if revision_result.returncode == 0 else None
        ),
        "worktree_dirty": bool(status_result.stdout.strip()),
        "graph_path": str(graph_path) if graph_path.is_file() else None,
        "graph_sha256": sha256_file(graph_path) if graph_path.is_file() else None,
        "output_root": str(output_root),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "command_timeout_seconds": command_timeout_seconds,
        "total_timeout_seconds": total_timeout_seconds,
        "status": receipt_status,
        "terminal_reason": _resolve_terminal_reason(receipt_status, command_results),
        "return_code": status_code,
        "provider_calls": None,
        "graph_mutations": None,
        "operations_measured": False,
        "test_counts": _aggregate_test_counts(command_results),
        "production_qualified": False,
        "commands": command_results,
    }
    receipt["canonical_digest"] = {
        "algorithm": "sha256",
        "value": sha256_bytes(canonical_bytes(receipt)),
    }
    write_json(path, receipt)
    return path
