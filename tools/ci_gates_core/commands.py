"""
tools/ci_gates_core/commands.py

Command generators for deterministic, BGE, E2E, and fast-recording CI profiles.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from .base_tests import FAST_RECORDING_TESTS, ROUTE_EVALUATION_SUITES


def _current_branch(repository_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            b = proc.stdout.strip()
            if b not in ("main", "master"):
                return b
    except Exception:
        pass
    return None


def deterministic_commands(
    *,
    python: str,
    repository_root: Path,
    output_root: Path,
    tests: Sequence[str] | None = None,
    cache_modules: bool = True,
) -> list[list[str]]:
    """Return the hosted-CI-safe command set without executing it."""
    curr_b = _current_branch(repository_root)
    commands = [
        [
            python,
            "-B",
            "-m",
            "dual_pipeline.runtime_evidence",
            "enforce-controls",
            "--repository-root",
            str(repository_root),
        ],
    ]
    for route_name, suite_path in ROUTE_EVALUATION_SUITES:
        commands.append(
            [
                python,
                "-B",
                "-m",
                "dual_pipeline.cli",
                "eval-route-run",
                "--suite",
                str(repository_root / suite_path),
                "--output-root",
                str(output_root),
                "--evaluation-id",
                f"ci-route-{route_name}",
            ]
        )
    test_suite = list(tests) if tests is not None else []
    test_cmd = [
        python,
        "-B",
        "tools/run_tests_with_progress.py",
        "-j",
        "auto",
        "--timeout",
        "60",
    ]
    if cache_modules:
        test_cmd.append("--cache-modules")
    test_cmd.extend(test_suite)

    commands.extend(
        [
            [
                python,
                "-B",
                "tools/validate_no_synthetic_embeddings.py",
                "--repository-root",
                str(repository_root),
            ],
            [python, "-B", "tools/validate_dual_pipeline_contracts.py"],
            [
                python,
                "-B",
                "tools/audit_worktree_hygiene.py",
                "--check",
                *(["--branch", curr_b] if curr_b else []),
            ],
            [python, "-B", "tools/lint_no_custom_ingest_scripts.py", "--check"],
            [
                python,
                "-B",
                "tools/run_automatic_evaluation_gates.py",
                "--repository-root",
                str(repository_root),
                "--output-root",
                str(output_root / "automatic-evals"),
                "--evaluation-id",
                "ci-deterministic",
            ],
            [
                python,
                "-B",
                "tools/run_pipeline_assurance_gates.py",
                "--repository-root",
                str(repository_root),
            ],
            [
                python,
                "-B",
                "tools/verify_mac_audio_tap_integrity.py",
                "--repository-root",
                str(repository_root),
            ],
            [python, "-B", "tools/run_governance_linters.py"],
            test_cmd,
            [python, "-B", "tools/audit_graph_signal.py", "--check-active"],
        ]
    )
    return commands


def bge_commands(
    *,
    python: str,
    tests: Sequence[str] | None = None,
) -> list[list[str]]:
    """Return the bounded real-model release and conditional-L2 commands."""
    test_suite = list(tests) if tests is not None else []
    return [
        [
            python,
            "-B",
            "-c",
            (
                "from dual_pipeline.preflight import check_cpu\n"
                "result = check_cpu()\n"
                "if result.get('status') != 'PASS':\n"
                "    raise SystemExit('BGE profile CPU preflight failed: ' + str(result))\n"
            ),
        ],
        [python, "-B", "-m", "unittest", "-v", *test_suite],
    ]


def e2e_commands(
    *,
    python: str,
    tests: Sequence[str] | None = None,
    cache_modules: bool = False,
    require_live_runtime: bool = True,
) -> list[list[str]]:
    """Return the provider-free E2E and adversarial test commands."""
    test_suite = list(tests) if tests is not None else []
    e2e_cmd = [
        python,
        "-B",
        "tools/run_tests_with_progress.py",
        "-j",
        "4",
        "--timeout",
        "60",
    ]
    if require_live_runtime or not cache_modules:
        e2e_cmd.append("--require-live-runtime")
    else:
        e2e_cmd.append("--cache-modules")
    e2e_cmd.extend(test_suite)
    return [e2e_cmd]


def fast_recording_commands(
    *,
    python: str,
    tests: Sequence[str] | None = None,
) -> list[list[str]]:
    """Return the focused fast-recording initialization test commands."""
    test_suite = list(tests) if tests is not None else list(FAST_RECORDING_TESTS)
    return [
        [
            python,
            "-B",
            "tools/run_tests_with_progress.py",
            "-j",
            "4",
            "--timeout",
            "120",
            *test_suite,
        ],
    ]


def copilot_quality_commands(
    *,
    python: str,
    repository_root: Path,
) -> list[list[str]]:
    """Return the Wave 6 copilot-quality CI commands: test suite + quality gate evaluator."""
    return [
        [
            python,
            "-B",
            "-m",
            "pytest",
            "tests/test_copilot_feedback_loop_e2e.py",
            "tests/test_copilot_composer_adversarial.py",
            "-v",
            "--timeout=60",
        ],
        [
            python,
            "-B",
            "evals/copilot_quality_gate.py",
            "--holdout-path",
            str(repository_root / "tests/evals/copilot_golden/v1/sealed_holdout.jsonl"),
            "--receipt-out",
            str(repository_root / "reports/copilot_wave6_feedback_receipt.json"),
        ],
    ]
