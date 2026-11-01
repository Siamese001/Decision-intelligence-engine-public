"""Constants and policy definitions for runtime evidence discovery."""

from __future__ import annotations

from ..assurance import (  # noqa: F401
    INTERNAL_OPERATIONAL,
    assurance_status_label,
    internal_operational_status,
    pair_assurance_tier,
)

CONTROL_BEGIN = "<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->"
CONTROL_END = "<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->"
HOOK_COMMAND = "-m dual_pipeline.runtime_evidence enforce-controls"
REQUIRED_CONTROL_PHRASES = (
    "Every `active_pair.json` is scoped to its explicit runtime root",
    "An empty filename or pattern search is never proof that runtime or retrieval "
    "evidence is absent",
    "`production_qualified: false` is an authorization boundary",
    "Sealed retrieval release evaluations are retrieval evidence",
    "dual_pipeline.runtime_evidence inventory",
    "dual_pipeline.runtime_evidence claim",
    "--complete-scope",
)
CONTROL_FILES = (
    ".gitattributes",
    ".githooks/pre-commit",
    ".githooks/pre-push",
    "documentation/RUNTIME_EVIDENCE_CLAIM_ENFORCEMENT.md",
    "documentation/BGE_M3_RETRIEVAL_RELEASE_GATE.md",
    "dual_pipeline/retrieval_release.py",
    "dual_pipeline/retrieval_suite.py",
    "requirements/retrieval-release-evaluation-policy.json",
    "requirements/retrieval-release-suite-generation-policy.json",
    "tests/test_retrieval_release_evaluation.py",
    "tests/test_bge_m3_deprecation_gates.py",
    "tests/test_no_synthetic_embedding_ban.py",
    "tools/validate_no_synthetic_embeddings.py",
    "tools/run_automatic_evaluation_gates.py",
)
RETRIEVAL_RELEASE_GATE_BINDINGS = {
    ".githooks/pre-commit": (
        "tests.test_retrieval_release_evaluation.RetrievalReleaseStaticGateTests"
    ),
    ".githooks/pre-push": "tests.test_retrieval_release_evaluation",
    "dual_pipeline/cli/parser.py": "retrieval-release-evaluate",
    "dual_pipeline/release.py": "validate_activation_evaluation",
    "dual_pipeline/uwg.py": "validate_activation_evaluation",
}
AUTOMATIC_EVAL_GATE_BINDINGS = {
    ".githooks/pre-push": ("tools/run_automatic_evaluation_gates.py",),
    "tools/run_automatic_evaluation_gates.py": ("run_workflow_suite",),
    ".gitattributes": (
        "dual_pipeline/evals/assurance.py text eol=lf",
        "evals/workloads/*.json text eol=lf",
        "evals/suites/*.json text eol=lf",
    ),
}
DEPRECATION_GATE_BINDINGS = {
    ".githooks/pre-commit": (
        "tools/validate_no_synthetic_embeddings.py",
        "tests.test_no_synthetic_embedding_ban",
        "tests.test_bge_m3_deprecation_gates",
    ),
    ".githooks/pre-push": (
        "tools/validate_no_synthetic_embeddings.py",
        "tests.test_no_synthetic_embedding_ban",
        "tests.test_bge_m3_deprecation_gates",
    ),
}
REFERENCE_KEYS = frozenset({"runtime_root", "evidence_root"})
BASE_PRUNE_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".worktrees",
        "node_modules",
        "__pycache__",
        "site-packages",
        "Cache",
        "Code Cache",
        "GPUCache",
        "Service Worker",
    }
)
RUNTIME_PRUNE_NAMES = frozenset(
    {
        "failed_generations",
        "generations",
        "ledger",
        "releases",
        "runs",
        "source_captures",
        "telemetry",
    }
)
PAIR_REQUIRED_FIELDS = (
    "embedding_backend",
    "generation_id",
    "graph_path",
    "graph_sha256",
    "model_id",
    "model_revision",
    "production_qualified",
    "sidecar_path",
    "sidecar_sha256",
)
NEGATIVE_CLAIMS = frozenset({"backend-inactive", "retrieval-absent", "l1-terminal-absent"})
ACTIVE_PAIR_ISSUE_CODES = frozenset(
    {
        "ACTIVE_PAIR_INVALID",
        "ACTIVE_PAIR_FIELDS_MISSING",
        "ACTIVE_GRAPH_MISSING",
        "ACTIVE_SIDECAR_MISSING",
        "INTERNAL_OPERATIONAL_EVIDENCE_INVALID",
    }
)
CLAIM_KINDS = (
    "backend-active",
    "backend-inactive",
    "internal-operational-active",
    "retrieval-present",
    "retrieval-absent",
    "l1-terminal-present",
    "l1-terminal-absent",
)
