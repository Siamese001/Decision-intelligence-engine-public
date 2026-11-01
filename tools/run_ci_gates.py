#!/usr/bin/env python3
"""Run the versioned CI profiles for the governed NEE Knowledge Graph.

The deterministic profile is safe for hosted CI: it never initializes a
provider, opens the BGE-M3 model, mutates a graph, or activates a release.
The bge profile is deliberately opt-in for a self-hosted runner that has the
pinned local model and manifest; it covers real-model release evaluation and
the conditional-L2 acceptance suite. Passing either profile is not a
production qualification claim.

Wave 5 AI-DLC Testing: Automated pattern and marker discovery replaces
hand-maintained test lists while preserving strict enrollment invariants.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci_gates_core.base_tests import (  # noqa: E402
    BASE_BGE_TESTS,
    BASE_DETERMINISTIC_TESTS,
    BASE_E2E_TESTS,
    FAST_RECORDING_TESTS,
    ROUTE_EVALUATION_SUITES,
)
from tools.ci_gates_core.commands import (  # noqa: E402
    _current_branch,
    bge_commands as _bge_commands_impl,
    deterministic_commands as _det_commands_impl,
    e2e_commands as _e2e_commands_impl,
    fast_recording_commands as _fast_commands_impl,
    copilot_quality_commands as _copilot_quality_commands_impl,
)
from tools.ci_gates_core.runner import (  # noqa: E402
    _claim_profile_output_root,
    _process_group_options,
    _terminate_process_tree,
    _write_profile_receipt,
    bge_environment,
    resolve_python_interpreter,
    run_commands,
    verify_ci_test_list_integrity as _verify_integrity_core,
)
from tools.test_discovery_core import (  # noqa: E402
    generate_resolution_report,
    resolve_all_tests,
)

DEFAULT_COMMAND_TIMEOUT_SECONDS = 360
BGE_COMMAND_TIMEOUT_SECONDS = 600
E2E_COMMAND_TIMEOUT_SECONDS = 240
DEFAULT_PROFILE_TIMEOUT_SECONDS = 600
BGE_PROFILE_TIMEOUT_SECONDS = 720
E2E_PROFILE_TIMEOUT_SECONDS = 900

_RESOLVED_TESTS_CACHE: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None = None


def _get_resolved_tests(repo_root: Path = ROOT) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Lazily resolve all test suites on first access instead of import time."""
    global _RESOLVED_TESTS_CACHE
    if repo_root.resolve() == ROOT.resolve() and _RESOLVED_TESTS_CACHE is not None:
        return _RESOLVED_TESTS_CACHE
    resolved = resolve_all_tests(
        base_deterministic=BASE_DETERMINISTIC_TESTS,
        base_e2e=BASE_E2E_TESTS,
        base_bge=BASE_BGE_TESTS,
        repo_root=repo_root,
    )
    if repo_root.resolve() == ROOT.resolve():
        _RESOLVED_TESTS_CACHE = resolved
    return resolved


def __getattr__(name: str) -> object:
    if name in ("DETERMINISTIC_TESTS", "E2E_TESTS", "BGE_TESTS"):
        det, e2e, bge = _get_resolved_tests(ROOT)
        mapping = {
            "DETERMINISTIC_TESTS": det,
            "E2E_TESTS": e2e,
            "BGE_TESTS": bge,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def deterministic_commands(
    *,
    python: str,
    repository_root: Path,
    output_root: Path,
    tests: Sequence[str] | None = None,
    cache_modules: bool = True,
) -> list[list[str]]:
    """Return the hosted-CI-safe command set without executing it."""
    det_tests = tests if tests is not None else _get_resolved_tests(repository_root)[0]
    return _det_commands_impl(
        python=python,
        repository_root=repository_root,
        output_root=output_root,
        tests=det_tests,
        cache_modules=cache_modules,
    )


def bge_commands(*, python: str, tests: Sequence[str] | None = None) -> list[list[str]]:
    """Return the bounded real-model release and conditional-L2 commands."""
    bge_tests = tests if tests is not None else _get_resolved_tests(ROOT)[2]
    return _bge_commands_impl(
        python=python,
        tests=bge_tests,
    )


def e2e_commands(
    *,
    python: str,
    tests: Sequence[str] | None = None,
    cache_modules: bool = False,
    require_live_runtime: bool = True,
) -> list[list[str]]:
    """Return the provider-free E2E and adversarial test commands."""
    e2e_tests = tests if tests is not None else _get_resolved_tests(ROOT)[1]
    return _e2e_commands_impl(
        python=python,
        tests=e2e_tests,
        cache_modules=cache_modules,
        require_live_runtime=require_live_runtime,
    )


def fast_recording_commands(*, python: str, tests: Sequence[str] | None = None) -> list[list[str]]:
    """Return the focused fast-recording initialization test commands."""
    return _fast_commands_impl(
        python=python,
        tests=tests if tests is not None else FAST_RECORDING_TESTS,
    )


def copilot_quality_commands(*, python: str, repository_root: Path) -> list[list[str]]:
    """Return the Wave 6 copilot-quality test and evaluation commands."""
    return _copilot_quality_commands_impl(
        python=python,
        repository_root=repository_root,
    )


def verify_ci_test_list_integrity(repository_root: Path) -> list[str]:
    """Verify that all resolved tests exist on disk and contain no slashes or .py."""
    det, e2e, bge = _get_resolved_tests(repository_root)
    all_tests = list(det) + list(e2e) + list(bge)
    return _verify_integrity_core(repository_root, all_tests)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("deterministic", "e2e", "bge", "fast-recording", "copilot_quality"),
        default="deterministic",
        help=(
            "deterministic: fast governance gates, safe for hosted CI. "
            "e2e: full suite of provider-free end-to-end and adversarial tests (~15 min). "
            "bge: real-model BGE-M3 release evaluation on self-hosted runner. "
            "fast-recording: focused fast-recording initialization test suite (< 30s). "
            "copilot_quality: Wave 6 local-model composer holdout qualification and ablation."
        ),
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=Path(".local/ci"))
    parser.add_argument("--python", default=resolve_python_interpreter())
    parser.add_argument(
        "--bge-model-path",
        type=Path,
        help="Pinned local BGE-M3 snapshot for the optional real-model profile",
    )
    parser.add_argument(
        "--bge-model-manifest",
        type=Path,
        help="Manifest that binds --bge-model-path for the optional real-model profile",
    )
    parser.add_argument(
        "--max-command-seconds",
        type=float,
        default=None,
        help=(
            "Hard timeout for each gate command. Defaults to 120s for deterministic, "
            "180s for e2e, and 300s for bge."
        ),
    )
    parser.add_argument(
        "--max-total-seconds",
        type=float,
        default=None,
        help=(
            "Hard timeout for the complete gate profile. Defaults to 180s for deterministic, "
            "900s for e2e, and 360s for bge."
        ),
    )
    parser.add_argument(
        "--emit-resolution-report",
        type=Path,
        default=None,
        help="Emit Wave 5 test discovery and parity resolution report to given path",
    )
    parser.add_argument(
        "--shard",
        type=int,
        default=1,
        help="1-indexed shard number for parallel E2E execution (default: 1)",
    )
    parser.add_argument(
        "--total-shards",
        type=int,
        default=1,
        help="Total number of parallel shards for E2E execution (default: 1)",
    )
    parser.add_argument(
        "--sla-budget",
        type=float,
        default=None,
        help="SLA budget ceiling in seconds (e.g. 240.0 for <=4-minute SLA). Sets max_total_seconds when provided.",
    )
    parser.add_argument(
        "--no-module-cache",
        action="store_true",
        default=False,
        help="Bypass content-addressable per-module pass caching in test runners",
    )
    return parser


def _resolve_timeouts(args: argparse.Namespace) -> tuple[float, float]:
    timeout_seconds = args.max_command_seconds
    if timeout_seconds is None:
        if args.profile == "bge":
            timeout_seconds = BGE_COMMAND_TIMEOUT_SECONDS
        elif args.profile == "e2e":
            timeout_seconds = E2E_COMMAND_TIMEOUT_SECONDS
        elif args.profile in ("fast-recording", "copilot_quality"):
            timeout_seconds = 120.0
        else:
            timeout_seconds = DEFAULT_COMMAND_TIMEOUT_SECONDS
    if timeout_seconds <= 0:
        raise SystemExit("--max-command-seconds must be greater than zero")

    total_timeout_seconds = args.max_total_seconds
    if getattr(args, "sla_budget", None) is not None:
        total_timeout_seconds = args.sla_budget
    elif total_timeout_seconds is None:
        if args.profile == "bge":
            total_timeout_seconds = BGE_PROFILE_TIMEOUT_SECONDS
        elif args.profile == "e2e":
            total_timeout_seconds = E2E_PROFILE_TIMEOUT_SECONDS
        elif args.profile in ("fast-recording", "copilot_quality"):
            total_timeout_seconds = 240.0
        else:
            total_timeout_seconds = DEFAULT_PROFILE_TIMEOUT_SECONDS
    if total_timeout_seconds <= 0:
        raise SystemExit("--max-total-seconds must be greater than zero")
    return timeout_seconds, total_timeout_seconds


def _resolve_profile_execution(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    repository_root: Path,
    output_root: Path,
) -> tuple[Mapping[str, str], list[Sequence[str]]]:
    environment: dict[str, str] = dict(os.environ)
    cache_modules = not getattr(args, "no_module_cache", False)
    det_tests, e2e_tests, bge_tests = _get_resolved_tests(repository_root)

    if args.profile == "bge":
        environment["NEE_RUN_BGE_TESTS"] = "1"
        environment["NEE_RUN_BGE_RELEASE_TESTS"] = "1"
        try:
            environment = dict(
                bge_environment(
                    base_environment=environment,
                    model_path=args.bge_model_path,
                    manifest_path=args.bge_model_manifest,
                )
            )
        except ValueError as exc:
            parser.error(str(exc))
        commands = bge_commands(python=args.python, tests=bge_tests)
    elif args.profile == "e2e":
        environment["NEE_RUN_E2E_TESTS"] = "1"
        if args.total_shards and args.total_shards > 1:
            if args.shard < 1 or args.shard > args.total_shards:
                parser.error(
                    f"--shard must be between 1 and {args.total_shards} (got {args.shard})"
                )
            shard_idx = args.shard - 1
            sharded_tests = [
                t for i, t in enumerate(e2e_tests) if i % args.total_shards == shard_idx
            ]
            commands = e2e_commands(
                python=args.python,
                tests=sharded_tests,
                cache_modules=False if getattr(args, "no_module_cache", False) else cache_modules,
                require_live_runtime=True,
            )
        else:
            commands = e2e_commands(
                python=args.python,
                tests=e2e_tests,
                cache_modules=False if getattr(args, "no_module_cache", False) else cache_modules,
                require_live_runtime=True,
            )
    elif args.profile == "fast-recording":
        commands = fast_recording_commands(python=args.python)
    elif args.profile == "copilot_quality":
        commands = copilot_quality_commands(
            python=args.python,
            repository_root=repository_root,
        )
    else:
        commands = deterministic_commands(
            python=args.python,
            repository_root=repository_root,
            output_root=output_root,
            tests=det_tests,
            cache_modules=cache_modules,
        )
    return environment, list(commands)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    repository_root = args.repository_root.expanduser().resolve()
    if not (repository_root / "pyproject.toml").is_file():
        raise SystemExit(f"repository root is invalid: {repository_root}")

    if args.emit_resolution_report:
        generate_resolution_report(
            base_deterministic=BASE_DETERMINISTIC_TESTS,
            base_e2e=BASE_E2E_TESTS,
            base_bge=BASE_BGE_TESTS,
            out_path=args.emit_resolution_report,
            repo_root=repository_root,
        )
        print(f"✅ Wave 5 resolution report emitted to: {args.emit_resolution_report}")

    integrity_errors = verify_ci_test_list_integrity(repository_root)
    if integrity_errors:
        print(
            f"FATAL: CI test list integrity check failed with {len(integrity_errors)} error(s):",
            file=sys.stderr,
        )
        for err in integrity_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    timeout_seconds, total_timeout_seconds = _resolve_timeouts(args)

    output_root = args.output_root.expanduser()
    if not output_root.is_absolute():
        output_root = repository_root / output_root
    output_root = output_root.resolve()

    environment, commands = _resolve_profile_execution(args, parser, repository_root, output_root)

    _claim_profile_output_root(output_root)
    started_at = datetime.now(timezone.utc).isoformat()
    command_results: list[dict[str, object]] = []
    status_code = run_commands(
        commands=commands,
        repository_root=repository_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
        command_results=command_results,
        log_root=output_root / "command-logs",
    )
    if getattr(args, "total_shards", 1) and args.total_shards > 1:
        receipt_filename = f"{args.profile}-gate-shard-{args.shard}-of-{args.total_shards}-receipt.json"
    else:
        receipt_filename = f"{args.profile}-gate-receipt.json"
    receipt_path = _write_profile_receipt(
        path=output_root / receipt_filename,
        profile=args.profile,
        repository_root=repository_root,
        output_root=output_root,
        command_timeout_seconds=timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
        command_results=command_results,
        status_code=status_code,
        started_at=started_at,
    )
    print(f"gate receipt: {receipt_path}", flush=True)
    return status_code


if __name__ == "__main__":
    raise SystemExit(main())
