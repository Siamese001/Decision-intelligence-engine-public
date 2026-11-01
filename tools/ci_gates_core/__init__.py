"""
tools/ci_gates_core

Wave 5 CI Gates Core Architecture: modularized profile runners, commands, and baselines.
"""

from __future__ import annotations

from .base_tests import (
    BASE_BGE_TESTS,
    BASE_DETERMINISTIC_TESTS,
    BASE_E2E_TESTS,
    FAST_RECORDING_TESTS,
    ROUTE_EVALUATION_SUITES,
)
from .commands import (
    _current_branch,
    bge_commands,
    deterministic_commands,
    e2e_commands,
    fast_recording_commands,
)
from .runner import (
    _claim_profile_output_root,
    _process_group_options,
    _terminate_process_tree,
    _write_profile_receipt,
    bge_environment,
    resolve_python_interpreter,
    run_commands,
    verify_ci_test_list_integrity,
)

__all__ = [
    "BASE_BGE_TESTS",
    "BASE_DETERMINISTIC_TESTS",
    "BASE_E2E_TESTS",
    "FAST_RECORDING_TESTS",
    "ROUTE_EVALUATION_SUITES",
    "_current_branch",
    "bge_commands",
    "deterministic_commands",
    "e2e_commands",
    "fast_recording_commands",
    "_claim_profile_output_root",
    "_process_group_options",
    "_terminate_process_tree",
    "_write_profile_receipt",
    "bge_environment",
    "resolve_python_interpreter",
    "run_commands",
    "verify_ci_test_list_integrity",
]
