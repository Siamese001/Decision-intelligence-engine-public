"""Constants and report classification rules for readiness qualification caching."""

from __future__ import annotations

QUALITY_INPUT_PATTERNS = (
    "reports/alignment/",
    "governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT",
    "tests/manifests/",
)

GENERATED_REPORT_PATTERNS = (
    "reports/pr_latency/",
    "reports/pr_review_",
    "reports/pr_merge_",
    "reports/pr_promotion_",
    "reports/readiness/",
    "reports/local_main_readiness_",
    ".local/",
)

CONFIG_FILES = (
    "pyproject.toml",
    "governance/policy_registry.yaml",
    "config/governance/risk_lanes_policy.json",
    "config/operator_decision_vocabulary.py",
    "config/governance/gate_manifest.json",
    "config/governance/file_budget_exclusions.json",
    "config/governance/module_budget_policy.json",
    "reports/file_budget_burndown_baseline.json",
    "tools/layer_hierarchy_manifest.json",
    "config/governance/mock_usage_baseline.json",
)

TEST_HELPER_PATTERNS = (
    "tests/conftest.py",
    "tests/helpers.py",
)

ENV_VARS_TO_BIND = (
    "CI",
    "MOCK_ENV",
    "NEE_ALLOW_TEST_FIXTURES",
    "PYTHONHASHSEED",
    "TOKENIZERS_PARALLELISM",
    "GOVERNED_PR_MERGE",
)

PACKAGES_TO_BIND = (
    "hypothesis",
    "pydantic",
    "pytest",
    "ruff",
)


def is_quality_input(path_str: str) -> bool:
    """Return True if the report/file is consumed as a quality input."""
    return any(p in path_str for p in QUALITY_INPUT_PATTERNS)


def is_generated_report(path_str: str) -> bool:
    """Return True if the file is an ephemeral or output-only report deliverable."""
    return any(path_str.startswith(p) or f"/{p}" in path_str for p in GENERATED_REPORT_PATTERNS)
