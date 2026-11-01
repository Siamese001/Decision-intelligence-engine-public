from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..core import ROOT
from ..runtime_root import resolve_authoritative_runtime_root

DEFAULT_GRAPH = ROOT / 'graph' / 'nee_graph.sqlite'
DEFAULT_RUNTIME = resolve_authoritative_runtime_root(ROOT)
DEFAULT_INGESTION_REASONING_POLICY = ROOT / 'requirements' / 'ingestion-reasoning-policy.json'
DEFAULT_INTERNAL_OPERATIONAL_POLICY = ROOT / 'requirements' / 'internal-operational-policy.json'

_BGE_M3_BACKENDS = frozenset({'flagembedding-bge-m3', 'flagembedding-bge-m3-dev', 'flagembedding-bge-m3-internal'})
_CLAIM_KINDS = ("backend-active", "backend-inactive", "internal-operational-active", "retrieval-present", "retrieval-absent", "l1-terminal-present", "l1-terminal-absent")

class _AttachmentCollectorAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest, None)
        if items is None:
            items = []
        if isinstance(values, list):
            items.extend(str(v) for v in values)
        else:
            items.append(str(values))
        setattr(namespace, self.dest, items)
        namespace.attachment = items
        namespace.attachments = items


class _BacktestAction(argparse.Action):
    """Activates cost-optimized backtest configuration: implies force-rerun, fast judge tier, and regenerate 02,03."""

    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        if getattr(namespace, "judge_tier", None) in (None, "production"):
            namespace.judge_tier = "fast"
        if getattr(namespace, "regenerate", None) is None:
            namespace.regenerate = "02,03"
        namespace.force_rerun = True

def _add_runtime(parser: argparse.ArgumentParser, *, graph: bool = True) -> None:
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    if graph:
        parser.add_argument("--graph", type=Path, default=None, help="Explicit path to graph SQLite database (defaults to active graph from active_pair.json)")

def _add_backend(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--embedding-backend", choices=["bge-m3", "bge-m3-dev", "bge-m3-internal"], default="bge-m3")
    parser.add_argument("--embedding-device", choices=["auto", "mps", "cuda", "cpu"], default="auto", help="Target execution device for BGE-M3 embeddings (default: auto)")
    parser.add_argument("--embedding-fp16", dest="embedding_fp16", action="store_true", default=None, help="Force half-precision (FP16) inference for BGE-M3")
    parser.add_argument("--no-embedding-fp16", dest="embedding_fp16", action="store_false", help="Force full-precision (FP32) inference for BGE-M3")
    parser.add_argument("--model-path", default="BAAI/bge-m3")  # noqa: SSOT_EXEMPT
    parser.add_argument("--model-manifest", help="Complete SHA-256 inventory for the locally provisioned model snapshot")
    parser.add_argument("--model-revision", help="Immutable revision identifier for the locally provisioned BGE-M3 model")
    parser.add_argument(
        "--allow-dev-bge-m3",
        action="store_true",
        help=(
            "Explicitly permit real BGE-M3 in a development-only run; all "
            "resulting pairs and receipts remain non-production"
        ),
    )
    parser.add_argument(
        "--allow-internal-operational",
        action="store_true",
        help=(
            "Explicitly permit real BGE-M3 under the configured trusted-team "
            "internal operational policy"
        ),
    )
    parser.add_argument(
        "--internal-operational-policy",
        type=Path,
        default=DEFAULT_INTERNAL_OPERATIONAL_POLICY,
        help="Configured policy for the internal operational assurance tier",
    )

def _add_authorization(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--auth-token", type=Path, help="Path to a short-lived RS256 JWT")
    parser.add_argument("--auto-auth", action=argparse.BooleanOptionalAction, default=True, help="Auto-resolve local operator auth token")
    parser.add_argument("--auth-public-key", type=Path, help="Path to the trusted RSA public key")
    parser.add_argument("--auth-issuer", help="Exact expected JWT issuer")
    parser.add_argument("--auth-audience", help="Exact expected JWT relying-party audience")

def _add_l2_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--materiality",
        choices=["low", "medium", "high"],
        help="Explicit deterministic materiality input to the governed L1 query plan",
    )

def _add_l1_controller_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--l1-controller-mode",
        choices=["off", "shadow", "bounded"],
        default="off",
        help=(
            "Keep the governed v2 query path off by default, emit provider-free "
            "shadow receipts, or run an explicit non-production bounded candidate."
        ),
    )
    parser.add_argument(
        "--l1-controller-policy",
        type=Path,
        help="Explicit L1 control policy; bounded mode requires a policy whose mode is bounded.",
    )

def _l1_control_policy_from_args(args: argparse.Namespace):
    """Load only an operator-supplied candidate policy; defaults stay default-off."""
    policy = None
    if args.l1_controller_policy is not None:
        from ..core import read_json
        from ..l1_control import load_policy
        policy = load_policy(read_json(args.l1_controller_policy))
    if args.l1_controller_mode == "bounded":
        if policy is None or policy.mode != "bounded":
            raise ValueError(
                "L1_BOUNDED_POLICY_REQUIRED: bounded mode requires --l1-controller-policy with mode=bounded"
            )
    return policy

def _add_query_routing_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--destination",
        choices=["GENERAL_ASSISTANT_HANDOFF", "OPERATIONAL_WORKFLOW_HANDOFF", "GRAPH_EVIDENCE_UTILITY", "NEE_DECISION_INTELLIGENCE"],
        help="Optional governed destination override. It cannot bypass ACL, retrieval coverage, provider-egress, or release gates.",
    )
    parser.add_argument(
        "--delivery-profile",
        choices=["internal_technical", "internal_executive", "client_technical", "client_executive", "commercial"],
        default="internal_technical",
    )
    parser.add_argument(
        "--artifact-type",
        choices=["answer", "decision_brief", "architecture_deck", "commercial_case", "evidence_export"],
        default="answer",
    )

def _add_semantic_cache_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--semantic-cache-mode",
        choices=["off", "read", "write", "read_write"],
        help=(
            "Optional assertion against the runtime-persisted L0 semantic cache "
            "mode. Configure changes with semantic-cache-config."
        ),
    )

def _add_ingestion_reasoning_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ingestion-reasoning",
        choices=["off", "shadow", "advisory"],
        default="off",
        help=(
            "Optionally run one post-C0 advisory reasoning pass. It never changes "
            "deterministic dispositions, identities, approvals, or graph operations."
        ),
    )
    parser.add_argument(
        "--ingestion-reasoning-policy",
        type=Path,
        default=DEFAULT_INGESTION_REASONING_POLICY,
        help="Versioned policy containing exact model tiers and call caps",
    )

def _add_common_ingest_args(command: argparse.ArgumentParser, *, provider_help: str) -> None:
    command.add_argument("--metadata", type=Path, default=None, help="Path to metadata JSON (optional; auto-scaffolded if omitted)")
    command.add_argument("--project", type=str, default=None, help="Governed project name (e.g. project_nexus, project_lens, nea)")
    command.add_argument("--date", type=str, default=None, help="Source date (MM/DD/YYYY or MM-DD-YYYY)")
    command.add_argument("--classification", type=str, choices=["internal", "confidential", "restricted"], default="restricted", help="Classification tier (default: restricted)")
    command.add_argument("--model", type=str, default=None, help="LLM model for synthesis and extraction")
    command.add_argument("--output-dir", type=str, default="auto", help="Output directory for artifacts")
    _provider_choices = ["llm", "openai", "local-mlx"]
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
        or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
        or os.environ.get("NEE_TEST_MODE")
    ):
        _provider_choices.append("offline")
    command.add_argument("--provider", choices=_provider_choices, default="llm", help=provider_help)
    command.add_argument("--directive-id", help="Optional directive ID for graph publication")
    command.add_argument("--max-judge-retries", type=int, default=1, choices=[0, 1, 2], help="Maximum judge remediation retry attempts (default: 1, capped at 2)")
    command.add_argument("--judge-model", type=str, default=None, help="Model for multi-micro-judge and LLM synthesis (defaults to --model or DIALECTICAL_JUDGE)")
    command.add_argument(
        "--judge-tier",
        type=str,
        default="production",
        choices=["production", "standard", "fast"],
        help="Model tier for multi-micro-judge during backtests/re-runs: 'production' (gpt-6-luna), 'standard' (gpt-6.1-sol), or 'fast' (gpt-6-luna).",
    )
    command.add_argument("--reasoning-effort", type=str, default="high", choices=["none", "low", "medium", "high", "xhigh", "max"], help="Reasoning effort level (default: high)")
    command.add_argument("--require-bge-m3", action=argparse.BooleanOptionalAction, default=True, help="Enforce real BGE-M3 model")
    command.add_argument("--allow-duplicate", action="store_true", default=False, help="Allow ingestion despite detected temporal/discourse collision with an existing source on the same date")
    command.add_argument("--collision-override-rationale", type=str, default=None, help="Documented rationale required when --allow-duplicate is used")
    command.add_argument("--force-rerun", action="store_true", default=False, help="Force re-execution bypassing idempotency check")
    command.add_argument("--resume", action="store_true", default=False, help="Resume execution from existing artifacts/checkpoints without full re-run")
    command.add_argument(
        "--regenerate", type=str, default=None,
        help="Comma-separated output deliverables to regenerate (e.g. '02,03', '01,02,03', or 'all'). Excluded outputs are frozen from prior run artifacts.",
    )
    command.add_argument(
        "--backtest",
        action=_BacktestAction,
        default=False,
        help="Activate cost-optimized re-run configuration for backtests: implies --force-rerun, --judge-tier fast, and --regenerate 02,03 (overridable individually), and generates a run-over-run comparison report.",
    )
    command.add_argument("--batch-queue", action="store_true", default=False, help="Queue synthesis calls via asynchronous OpenAI Batch API for 50%% token cost discount (24h non-interactive SLA).")
    command.add_argument("--no-subfolder", action="store_true", default=False, help="Prevent automatic subfolder creation under date directory")
    command.add_argument("--progress-mode", choices=["auto", "tqdm", "fenced", "plain", "silent"], default="auto", help="Progress bar rendering mode: 'auto' (fenced for non-TTY, tqdm for TTY), 'fenced', 'tqdm', 'plain', or 'silent'")
    command.add_argument("--timeout", "--timeout-seconds", dest="timeout_seconds", type=int, default=3600, help="Maximum execution timeout in seconds")
    command.add_argument("--enforce-worktree", action="store_true", default=True, help="Mandatory isolated Git worktree branch creation (.worktrees/<slug>/ on ingest/<modality>-<slug>)")
    command.add_argument("--worktree-dir", type=str, default=None, help="Explicit isolated worktree directory path (.worktrees/<slug>) to reuse across multi-attachment batch ingestion")
    command.add_argument("--worktree-branch", type=str, default=None, help="Explicit conventional branch name (ingest/<modality>-<slug>) to reuse across multi-attachment batch ingestion")
    command.add_argument("--cleanup-worktree", action=argparse.BooleanOptionalAction, default=False, help="Automatically commit deliverables, dehydrate worktree directory, and preserve unmerged branch ref upon verified COMPLETE publication")
    command.add_argument("--fast-promote", action="store_true", default=False, help="Fast promote clean Tier-0 claims inline to canonical, parking conflicted claims in nee hitl-inbox queue without interactive blocking.")


def _add_hitl_inbox_subparser(subparsers: Any) -> None:
    command = subparsers.add_parser(
        "hitl-inbox",
        help="Manage, review, and resolve deferred shift-left HITL exceptions parked during --fast-promote.",
    )
    command.add_argument("--list", action="store_true", default=False, help="List all pending HITL exceptions in queue")
    command.add_argument("--resolve", type=str, default=None, help="Exception ID, Candidate ID, or Inbox ID to resolve")
    command.add_argument("--decision", type=str, default=None, choices=["SUPERSEDE", "KEEP_CANDIDATE", "MERGE_ALIAS", "CREATE_NEW", "PROMOTE_APPROVED", "REJECT", "QUARANTINE"], help="Decision for resolution")
    command.add_argument("--batch", type=Path, default=None, help="Path to JSON file containing batch resolutions")
    command.add_argument("--rationale", type=str, default=None, help="Optional operator rationale for resolution")
    command.add_argument("--reviewer", type=str, default="operator", help="Reviewer identity")
    command.add_argument("--json", dest="json_output", action="store_true", default=False, help="Output results in JSON format")
    command.add_argument("--inbox-file", type=Path, default=None, help="Explicit path to hitl_inbox.json file")
    _add_runtime(command)


REMOVED_SIMULATED_FLAGS = {
    "--package-only": "flag removed: '--package-only' has been eliminated. Ingestion always seals packages and enters AWAITING_APPROVAL.",
    "--dry-run-graph": "flag removed: '--dry-run-graph' has been eliminated. Ingestion mutations are gated by authentic approvals.",
    "--no-mutate-graph": "flag removed: '--no-mutate-graph' has been eliminated. Ingestion always requests operator approval.",
    "--dry-run-qa": "flag removed: '--dry-run-qa' has been eliminated. Simulated run modes are prohibited.",
    "--plan-only": "flag removed: '--plan-only' has been eliminated. Simulated run modes are prohibited.",
    "--no-enforce-worktree": "flag removed: '--no-enforce-worktree' has been eliminated. Worktree isolation is mandatory for all ingestion and automation pipelines.",
    "--reviewer": "flag removed: '--reviewer' has been eliminated. Operator identity is derived from verified auth tokens or interactive HITL approvals.",
    "--rationale": "flag removed: '--rationale' has been eliminated. Approval rationale is captured in the cryptographic HITL decision ledger.",
}


class GovernedArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors, reserving code 2 strictly for HITL."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")

    def parse_known_args(self, args=None, namespace=None):
        if args is None:
            raw_args = sys.argv[1:]
        else:
            raw_args = list(args)

        for raw in raw_args:
            flag_name = raw.split("=")[0]
            if flag_name in REMOVED_SIMULATED_FLAGS:
                self.error(REMOVED_SIMULATED_FLAGS[flag_name])
            if flag_name == "--dry-run":
                self.error("flag removed: '--dry-run' has been eliminated. Simulated run modes are prohibited.")
            if flag_name == "--mock" and not (
                os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
                or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
                or os.environ.get("NEE_TEST_MODE")
            ):
                self.error("flag removed: '--mock' has been eliminated. Simulated run modes are prohibited.")
            if flag_name == "--provider" and len(raw_args) > raw_args.index(raw) + 1:
                val = raw_args[raw_args.index(raw) + 1]
                if val == "offline" and not (
                    os.environ.get("PYTEST_CURRENT_TEST")
                    or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
                    or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
                    or os.environ.get("NEE_TEST_MODE")
                ):
                    self.error("flag removed: '--provider offline' has been eliminated. Simulated run modes are prohibited.")
            elif raw.startswith("--provider=offline") and not (
                os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
                or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
                or os.environ.get("NEE_TEST_MODE")
            ):
                self.error("flag removed: '--provider offline' has been eliminated. Simulated run modes are prohibited.")

        return super().parse_known_args(args=args, namespace=namespace)


def build_parser() -> argparse.ArgumentParser:
    parser = GovernedArgumentParser(
        prog="nee",
        description=(
            "Routed NEE decision intelligence over governed graph and retrieval evidence."
        ),
    )
    parser.add_argument("--log-format", choices=["text", "json"], default="text", help="Log output format (text or json)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="INFO", help="Logging verbosity level")
    parser.add_argument("--traceparent", help="W3C TraceContext traceparent header for distributed trace propagation")
    parser.add_argument("--max-cost-usd", type=float, help="Hard budget ceiling in USD for LLM synthesis costs in this run")
    parser.add_argument("--profile", action="store_true", default=False, help="Profile command execution with cProfile and tracemalloc")
    parser.add_argument("--profile-output", type=str, default=None, help="File path to save profile statistics (.pstats file)")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=GovernedArgumentParser)

    for name in ("ingest", "ingest-evidence-envelope"):
        command = subparsers.add_parser(name)

        command.add_argument("--package", type=Path, required=True)

        command.add_argument("--directive-id", required=True)
        command.add_argument(
            "--batch-manifest",
            type=Path,
            help="Exact source-bound batch manifest for scalable production admission",
        )
        command.add_argument(
            "--audience", default="internal_consulting,nee-engineering"
        )
        _add_runtime(command)
        _add_backend(command)
        _add_authorization(command)
        _add_ingestion_reasoning_controls(command)
        _add_semantic_cache_controls(command)

    command = subparsers.add_parser("intake-status")
    command.add_argument("--run-id", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-open")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--scope", required=True)
    command.add_argument("--package", type=Path, action="append", required=True)
    command.add_argument("--batch-manifest", type=Path)
    command.add_argument(
        "--require-bge-m3",
        action="store_true",
        help=(
            "Bind the directive and its HITL approval to the active pinned "
            "BAAI/bge-m3 runtime; reject non-BGE runtimes."
        ),
    )
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-attach")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--package", type=Path, action="append", default=[])
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-approve")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--decision", choices=["APPROVE", "REJECT"], required=True)
    command.add_argument("--classification-digest", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-classify")
    command.add_argument("--directive-id", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-admit-batch")
    command.add_argument("--directive-id", required=True)
    _add_runtime(command)
    _add_authorization(command)

    command = subparsers.add_parser("intake-directive-status")
    command.add_argument("--directive-id", required=True)
    _add_runtime(command)

    command = subparsers.add_parser(
        "intake-delivery-status",
        help=(
            "Project one directive's intake, graph/index, retrieval-evaluation, "
            "activation, and completion receipts into one read-only status view"
        ),
    )
    command.add_argument("--directive-id", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-block")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--reason-code", required=True)
    command.add_argument("--reason", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-directive-cancel")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--reason", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-completion-gate")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--receipt", type=Path, required=True)
    _add_runtime(command)

    command = subparsers.add_parser("intake-completion-noop")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", action="append", required=True)
    _add_runtime(command)

    command = subparsers.add_parser("candidate-backlog-cleanup")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", required=True)
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "source-retirement",
        help="Remove one exact obsolete source scope from the active graph/index pair",
    )
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument(
        "--control-package",
        type=Path,
        required=True,
        help="Exact source-bound Markdown control package used by the intake run.",
    )
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "graph-consolidate",
        help="Governed graph consolidation scan, plan, and release lifecycle",
    )
    command.add_argument(
        "action",
        choices=["scan", "plan", "release"],
        help="Consolidation lifecycle action: scan (preview), plan (generate plan JSON), or release (prepare & activate)",
    )
    command.add_argument("--plan", type=Path, default=None, help="Path to consolidation plan JSON (for release)")
    command.add_argument("--output", type=Path, default=None, help="Output path for plan JSON or release receipt")
    command.add_argument("--directive-id", default=None, help="Governed directive ID (required for release)")
    command.add_argument("--run-id", default=None, help="Governed run ID (required for release)")
    command.add_argument("--approval-id", default=None, help="Approval ID or directive approval text (required for release)")
    command.add_argument("--node-threshold", type=float, default=0.65, help="Similarity threshold for node clustering")
    command.add_argument("--literal-threshold", type=float, default=0.85, help="Similarity threshold for assertion literal overlap")
    command.add_argument("--evidence-threshold", type=float, default=0.90, help="Similarity threshold for evidence quote dedup")
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "graph-consolidate-scan",
        help="Scan active graph for duplicate entities, assertion overlaps, and evidence fragmentation",
    )
    _add_runtime(command)
    command.add_argument("--output", type=Path, default=None, help="Optional output path for consolidation plan JSON")
    command.add_argument("--node-threshold", type=float, default=0.65, help="Similarity threshold for node clustering")
    command.add_argument("--literal-threshold", type=float, default=0.85, help="Similarity threshold for assertion literal overlap")
    command.add_argument("--evidence-threshold", type=float, default=0.90, help="Similarity threshold for evidence quote dedup")

    command = subparsers.add_parser(
        "combined-remediation-finalize",
        help=(
            "Re-evaluate and, only on PASS, activate one existing exact combined "
            "remediation receipt"
        ),
    )
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--receipt", type=Path, required=True)
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "combined-remediation",
        help=(
            "Apply one sealed Sherpa source-retirement and project-status-demotion "
            "control to a new graph/index generation"
        ),
    )
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument(
        "--control-package",
        type=Path,
        required=True,
        help="Exact source-bound Markdown package containing the combined remediation control.",
    )
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser("validation-closeout")
    command.add_argument("--directive-id", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--validation-id", required=True)
    command.add_argument("--resolution", required=True)
    command.add_argument(
        "--disposition",
        choices=[
            "retain_evidence_only",
            "remove_from_graph",
            "confirm_mapping",
            "confirm_canonical_name",
        ],
        default="retain_evidence_only",
    )
    command.add_argument(
        "--confirmed-edge",
        action="append",
        default=[],
        help="Exact legacy edge ID covered by a confirmed mapping decision; repeat as needed.",
    )
    command.add_argument(
        "--candidate-claim",
        action="append",
        default=[],
        help="Candidate explanatory claim to retain as evidence after the mapping is confirmed.",
    )
    command.add_argument(
        "--confirmed-name",
        help="Exact official/current name confirmed by the HITL decision.",
    )
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "graph-scout",
        help="Run the read-only Governed Graph Health & Remediation Scout",
    )
    _add_runtime(command)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write diagnostic report and proposals",
    )
    command.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.72,
        help="Fuzzy similarity threshold for near-duplicate entity detection",
    )
    command.add_argument(
        "--max-candidate-age-days",
        type=int,
        default=30,
        help="Age threshold in days for flagging unreviewed candidate assertions",
    )
    command.add_argument(
        "--json",
        action="store_true",
        help="Emit only machine-readable JSON output",
    )
    command.add_argument(
        "--scheduled",
        action="store_true",
        help="Run in scheduled mode (evaluates mutation cadence and proposal queue backlog)",
    )
    command.add_argument(
        "--cadence-mutations",
        type=int,
        default=10,
        help="Cadence threshold in mutations for scheduled scout",
    )
    command.add_argument(
        "--cadence-days",
        type=int,
        default=7,
        help="Cadence threshold in days for scheduled scout",
    )
    command.add_argument(
        "--max-proposal-backlog",
        type=int,
        default=20,
        help="Maximum open proposal backlog before scheduled scout is gated",
    )

    command = subparsers.add_parser(
        "graph-diff",
        help="Compare two GraphDB snapshots and format 14-table delta breakdown and itemized assertions",
    )
    command.add_argument("--baseline", type=Path, default=None, help="Path to baseline graph SQLite file (defaults to active pair or seed)")
    command.add_argument("--candidate", type=Path, default=None, help="Path to candidate/mutated graph SQLite file")
    command.add_argument("--baseline-sidecar", type=Path, default=None, help="Path to baseline sidecar index SQLite file")
    command.add_argument("--candidate-sidecar", type=Path, default=None, help="Path to candidate sidecar index SQLite file")
    command.add_argument("--limit", type=int, default=50, help="Maximum number of itemized assertions to inspect (default: 50)")
    command.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    command.add_argument("--json", action="store_true", default=False, help="Emit only machine-readable JSON")
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "graph-stats",
        help="Collect and output comprehensive baseline table counts and computed metrics",
    )
    command.add_argument("--graph", type=Path, default=None, help="Path to graph SQLite file (defaults to active graph)")
    command.add_argument("--json", action="store_true", default=False, help="Emit only machine-readable JSON")
    command.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output serialization format (default: json)",
    )
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "graph-gc",
        help="Run graph garbage collection to archive stale entities and unreferenced orphan nodes",
    )
    command.add_argument("--graph", type=Path, default=None, help="Path to graph SQLite file (defaults to active graph)")
    command.add_argument("--as-of", type=str, default=None, help="Staleness cutoff date (YYYY-MM-DD, defaults to today)")
    command.add_argument("--prune-orphans", action=argparse.BooleanOptionalAction, default=True, help="Prune unreferenced orphan nodes into archived_node (default: True)")
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "runtime-evidence",
        help="Inventory runtime evidence or evaluate evidence-bound runtime and retrieval claims",
    )
    re_sub = command.add_subparsers(dest="subcommand", required=False)

    re_inv = re_sub.add_parser("inventory", help="Discover active pairs, query runs, and retrieval evaluations")
    re_inv.add_argument("--repository-root", type=Path, default=None)
    re_inv.add_argument("--extra-root", type=Path, action="append", default=[])
    re_inv.add_argument("--output", type=Path, default=None)

    re_claim = re_sub.add_parser("claim", help="Evaluate evidence-bound claim")
    re_claim.add_argument("--repository-root", type=Path, default=None)
    re_claim.add_argument("--extra-root", type=Path, action="append", default=[])
    re_claim.add_argument("--kind", choices=_CLAIM_KINDS, required=True)
    re_claim.add_argument("--backend", required=True)
    re_claim.add_argument("--scope-root", type=Path, action="append", default=[])
    re_claim.add_argument("--complete-scope", action="store_true", default=False)
    re_claim.add_argument("--output", type=Path, default=None)

    re_ctrl = re_sub.add_parser("enforce-controls", help="Enforce control files and invariants")
    re_ctrl.add_argument("--repository-root", type=Path, default=None)

    command = subparsers.add_parser("intake-review")
    command.add_argument("--run-id", required=True)
    command.add_argument("--decisions", type=Path, required=True)
    command.add_argument("--audience", default="internal_consulting,nee-engineering")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser("bootstrap-runtime")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "semantic-cache-config",
        help="Set the one authoritative semantic-cache mode for a runtime",
    )
    command.add_argument(
        "--mode",
        required=True,
        choices=["off", "read", "write", "read_write"],
    )
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "semantic-cache-status",
        help="Report semantic-cache configuration, health, and population",
    )
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "semantic-unit-audit",
        help="Audit semantic units, vector geometry, margin tie rates, and fact reachability",
    )
    _add_runtime(command)
    command.add_argument("--sidecar", type=Path, help="Explicit sidecar index path")
    command.add_argument(
        "--query-cache",
        type=Path,
        help="Path to query encoding cache JSON",
    )
    command.add_argument(
        "--hop-budget",
        type=int,
        default=1,
        help="Hop budget for profile reachability (default: 1)",
    )
    command.add_argument(
        "--margin-threshold",
        type=float,
        default=None,
        help="Confidence margin threshold (default: SSOT parameter)",
    )
    command.add_argument(
        "--policy",
        type=Path,
        help="Path to semantic unit policy JSON (default: governance/decision_intelligence/semantic_unit_policy.json if present)",
    )
    command.add_argument(
        "--output-receipt",
        type=Path,
        help="Path to write sealed audit receipt JSON",
    )
    command.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit raw JSON receipt",
    )

    command = subparsers.add_parser(
        "semantic-unit-release",
        help="Prepare, evaluate, and activate live semantic unit index release",
    )
    _add_runtime(command)
    _add_backend(command)
    command.add_argument("--run-id", help="Governed run ID")
    command.add_argument("--approval-id", help="Approval ID or directive approval text")
    command.add_argument("--policy", type=Path, help="Path to semantic unit policy JSON")
    command.add_argument("--output", type=Path, help="Output path for activation JSON")

    command = subparsers.add_parser(
        "uwg-evaluate",
        help="Run a Canonical Promotion Gateway benchmark against the active candidate BGE-M3 lane",
    )
    command.add_argument("--benchmark", type=Path, required=True)
    command.add_argument(
        "--output",
        type=Path,
        help="Immutable evaluation output path under the runtime root",
    )
    command.add_argument("--evaluation-id")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "retrieval-release-evaluate",
        help="Run the sealed BGE-M3 active-versus-candidate retrieval regression gate",
    )
    command.add_argument("--receipt", type=Path, required=True)
    command.add_argument(
        "--suite",
        type=Path,
        help=(
            "Optional explicit sealed suite. Omit to build and persist the "
            "governed receipt-bound suite automatically."
        ),
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "retrieval-release-suite-build",
        help="Build one deterministic receipt-bound retrieval-release suite",
    )
    command.add_argument("--receipt", type=Path, required=True)
    command.add_argument(
        "--output",
        type=Path,
        help=(
            "Optional new runtime-local path for a generated override suite. "
            "Use only when an earlier automatic suite is retained as a failed attempt."
        ),
    )
    _add_runtime(command)

    command = subparsers.add_parser(
        "uwg-register-approval",
        help="Prepare a signed exact-scope UWG governance-approval release",
    )
    command.add_argument("--approval-request", type=Path, required=True)
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "uwg-promote",
        help="Prepare a Canonical Promotion Gateway graph/index release from a promotion decision",
    )
    command.add_argument("--evaluation", type=Path, required=True)
    command.add_argument("--decision", type=Path, required=True)
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    for name in ("uwg-activate", "uwg-rollback"):
        command = subparsers.add_parser(
            name,
            help="Activate or roll back a prepared Canonical Promotion Gateway release",
        )
        command.add_argument("--receipt", type=Path, required=True)
        _add_runtime(command)
        _add_authorization(command)

    command = subparsers.add_parser("intake-release-prepare")
    command.add_argument("--run-id", action="append", required=True)
    command.add_argument("--directive-id", required=True)
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    for name in ("intake-release-activate", "intake-release-rollback"):
        command = subparsers.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
        if name == "intake-release-activate":
            command.add_argument("--directive-id", required=True)
        _add_runtime(command)
        _add_authorization(command)

    command = subparsers.add_parser(
        "intake-batch-release",
        help=(
            "Prepare one approved batch; non-production also activates and completes it. "
            "Production returns the exact receipt-bound activation request."
        ),
    )
    command.add_argument("--run-id", action="append", required=True)
    command.add_argument("--directive-id", required=True)
    command.add_argument(
        "--retrieval-release-suite",
        type=Path,
        help=(
            "Optional explicit sealed BGE-M3 suite override. Omit to build, "
            "persist, and run the governed receipt-bound suite automatically."
        ),
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "cards-refactor",
        help="Governed cards and facets refactoring release management",
    )
    cards_sub = command.add_subparsers(dest="subcommand", required=True)

    cards_plan = cards_sub.add_parser("plan", help="Build read-only cards refactor plan")
    cards_plan.add_argument("--output", type=Path, required=True)
    cards_plan.add_argument("--contract-output", type=Path)
    _add_runtime(cards_plan, graph=False)

    cards_audit = cards_sub.add_parser("audit", help="Audit facet schema and coverage")
    cards_audit.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    cards_audit.add_argument("--graph", type=Path, default=None, help="Explicit SQLite graph (defaults to active graph)")

    cards_apply = cards_sub.add_parser("apply", help="Apply approved plan to target graph")
    cards_apply.add_argument("--plan", type=Path, required=True)
    cards_apply.add_argument("--output-graph", type=Path)
    cards_apply.add_argument("--approval-id", type=str)
    cards_apply.add_argument("--run-id", type=str)
    cards_apply.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    cards_apply.add_argument("--graph", type=Path, default=None, help="Target SQLite graph (defaults to active graph)")

    cards_rel = cards_sub.add_parser("release-e2e", help="Execute complete governed release and activation")
    cards_rel.add_argument("--approval-id", type=str)
    cards_rel.add_argument("--run-id", type=str)
    cards_rel.add_argument("--output-plan", type=Path)
    _add_runtime(cards_rel, graph=False)

    cards_compile = cards_sub.add_parser("compile", help="Compile a card view to access-sliced markdown")
    cards_compile.add_argument("--card-id", type=str, required=True)
    cards_compile.add_argument("--output", type=Path, help="Target markdown file path")
    cards_compile.add_argument("--audience", type=str, default="internal_consulting")
    cards_compile.add_argument("--clearance", type=str, default="internal")
    _add_runtime(cards_compile, graph=False)

    cards_compile_all = cards_sub.add_parser("compile-all", help="Compile and export all active card views to markdown")
    cards_compile_all.add_argument("--output-dir", type=Path, required=True)
    cards_compile_all.add_argument("--audience", type=str, default="internal_consulting")
    cards_compile_all.add_argument("--clearance", type=str, default="internal")
    _add_runtime(cards_compile_all, graph=False)

    cards_drift = cards_sub.add_parser("drift-audit", help="Audit drift and parity between legacy cards and active graph")
    cards_drift.add_argument("--card-id", type=str, help="Optional specific card ID")
    _add_runtime(cards_drift, graph=False)

    command = subparsers.add_parser(
        "intake-publish",
        help=(
            "One candidate-ingestion action: classify, admit, ingest, release, "
            "activate, and emit a terminal receipt when no additional HITL is needed."
        ),
    )
    command.add_argument("--package", type=Path, action="append", required=True)
    command.add_argument(
        "--directive-id",
        help="Optional stable directive ID; omitted IDs are derived from the exact package bytes",
    )
    command.add_argument(
        "--scope",
        help=(
            "Optional human-readable scope; defaults to candidate-only publication "
            "of these exact packages"
        ),
    )
    command.add_argument(
        "--batch-manifest",
        type=Path,
        help="Exact signed-policy manifest for low-risk automatic candidate admission",
    )
    command.add_argument(
        "--require-bge-m3",
        action="store_true",
        help=(
            "Fail closed unless this publication, HITL approval, release, and "
            "activated pair use the active pinned BAAI/bge-m3 target."
        ),
    )
    command.add_argument(
        "--a2a-finalization-receipt",
        type=Path,
        help="Allowed only for a one-package A2A selected-span publication",
    )
    command.add_argument(
        "--retrieval-release-suite",
        type=Path,
        help=(
            "Optional explicit sealed BGE-M3 suite override. Omit to build, "
            "persist, and run the governed receipt-bound suite automatically."
        ),
    )
    command.add_argument("--audience", default="internal_consulting,nee-engineering")
    command.add_argument("--enforce-worktree", action="store_true", default=True, help="Mandatory isolated Git worktree branch creation (.worktrees/<slug>/ on ingest/<modality>-<slug>)")
    command.add_argument("--cleanup-worktree", action=argparse.BooleanOptionalAction, default=False, help="Automatically remove isolated worktree and delete its branch upon verified COMPLETE publication")
    command.add_argument("--worktree-dir", type=Path, default=None, help="Explicit target worktree directory for session inheritance")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)
    _add_ingestion_reasoning_controls(command)
    _add_semantic_cache_controls(command)

    command = subparsers.add_parser(
        "recover-session",
        help="Disaster recovery tool for unfinalized live recording sessions.",
    )
    command.add_argument("positional_dir", nargs="?", type=Path, default=None, help="Path to session directory")
    command.add_argument("--session-dir", "-s", type=Path, default=None, help="Path to unfinalized session directory")
    command.add_argument("--output-dir", "-o", type=Path, default=None, help="Optional destination output directory")
    command.add_argument("--project", "-p", type=str, default=None, help="Project name override")
    command.add_argument("--meeting", "-m", type=str, default=None, help="Meeting title override")
    command.add_argument("--date", "-d", type=str, default=None, help="Meeting date override")

    command = subparsers.add_parser(
        "ingest-meeting",
        help="End-to-end meeting transcript and audio ingestion pipeline.",
    )
    input_group = command.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input", "-i", type=Path, default=None, help="Path to raw transcript or audio file (.txt, .md, .docx, .vtt, .json, .wav, .mp3, .m4a)")
    input_group.add_argument("--inputs", nargs="*", type=Path, default=None, help="Multiple raw transcript or audio input paths for parallel ingestion batch")
    command.add_argument("--parallel-workers", type=int, default=None, help="Number of concurrent meeting ingestion workers for multi-meeting batches (default: auto-scaled for M5 Pro 48GB)")
    command.add_argument("--group-commit", action="store_true", default=False, help="Atomically group commit all parallel meeting runs via prepare_many")
    command.add_argument("--meeting", "--meeting-name", dest="meeting", type=str, default=None, help="Meeting title")
    command.add_argument("--time", type=str, default=None, help="Meeting time range (e.g. '03:00 PM - 03:30 PM ET')")
    command.add_argument("--platform", type=str, default=None, help="Originating platform (e.g. 'Webex', 'Google Meet', 'Microsoft Teams', 'CoPilot', 'Zoom')")
    command.add_argument("--attendees", type=str, default=None, help="Attendee roster (freeform string or JSON)")
    command.add_argument("--speaker-map", type=str, default=None, help="JSON string or file path containing pre-resolved speaker map {cluster: attendee_name}")
    command.add_argument("--entity-resolutions", type=str, default=None, help="JSON string or file path containing pre-resolved entity mappings {token: canonical_name}")
    command.add_argument("--transcript-cache", type=Path, default=None, help="Optional pre-computed transcript file path")
    command.add_argument("--archetype", type=str, default=None, choices=["GENERAL", "ARCHITECTURE_DEEP_DIVE", "STRATEGIC_DISCOVERY", "GOVERNANCE_REVIEW", "TACTICAL_ALIGNMENT", "LESSONS_LEARNED"], help="Explicit meeting archetype (e.g. ARCHITECTURE_DEEP_DIVE, GENERAL)")
    command.add_argument("--presentation", type=Path, default=None, help="Path to optional .pptx presentation deck for cross-modal alignment")
    command.add_argument("--attachment", "--image", dest="attachments", action=_AttachmentCollectorAction, type=str, default=None, help="Path to image, diagram, or collateral file uploaded during the meeting (repeatable)")
    command.add_argument("--attachments", "--images", dest="attachments", action=_AttachmentCollectorAction, type=str, nargs="*", default=None, help="Path(s) to multiple images, diagrams, or collateral files uploaded during the meeting")
    command.add_argument("--attachment-dir", "--images-dir", dest="attachment_dir", type=Path, default=None, help="Directory containing uploaded meeting attachments and images")
    command.add_argument("--document", dest="document", action="store_true", default=False, help="Explicitly designate source as a document/email (defaults to Deliverable 03)")
    command.add_argument("--output3-only", dest="output3_only", action="store_true", default=False, help="Emit strictly Deliverable 03 regardless of source class")
    command.add_argument("--notes", "--copilot-notes", dest="notes", action="store_true", default=False, help="Ingest as pre-processed meeting notes emitting Deliverables 02 & 03 (bypassing Deliverable 01)")
    command.add_argument("--augment-existing", action="store_true", default=False, help="Augment existing sibling meeting container instead of creating a duplicate")
    command.add_argument("--filename-slug", type=str, default=None, help="Explicit filename slug locking output filenames across lifecycle stages")
    command.add_argument(
        "--backend",
        choices=["auto", "mlx", "faster-whisper", "whisperx", "cpu"],
        default="auto",
        help="Local on-premise ASR backend ('auto', 'mlx', 'faster-whisper', 'whisperx', 'cpu')",
    )
    command.add_argument("--whisper-model", default=None, help="Custom local Whisper model path or repo identifier")
    _add_common_ingest_args(command, provider_help="Live synthesis inference provider ('llm', 'openai', 'local-mlx')")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "ingest-document",
        help="End-to-end document, email, or executive memo ingestion pipeline emitting strictly Deliverable 03.",
    )
    input_group = command.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input", "-i", type=Path, default=None, help="Path to document or email file (.txt, .md, .docx, .html, .eml, .json)")
    input_group.add_argument("--inputs", nargs="*", type=Path, default=None, help="Multiple document input paths for parallel ingestion batch")
    command.add_argument("--parallel-workers", type=int, default=None, help="Number of concurrent document ingestion workers for multi-document batches (default: auto-scaled for M5 Pro 48GB)")
    command.add_argument("--group-commit", action="store_true", default=False, help="Atomically group commit all parallel document runs via prepare_many")
    command.add_argument("--title", "--meeting", dest="title", type=str, default=None, help="Document title or subject")
    command.add_argument("--author", type=str, default=None, help="Document author or sender")
    command.add_argument("--audiences", type=str, default=None, help="Target audiences")
    command.add_argument("--conversion-cache", action=argparse.BooleanOptionalAction, default=True, help="Enable conversion caching (default: True)")
    command.add_argument("--no-cache", action="store_true", default=False, help="Bypass and disable conversion artifact cache")
    command.add_argument("--conversion-cache-dir", type=Path, default=None, help="Explicit conversion artifact cache directory")
    command.add_argument("--auto-reroute", action="store_true", default=False, help="Automatically reroute transcript cue files (.vtt, .srt) to the meeting ingestion workflow")
    _add_common_ingest_args(command, provider_help="Live extraction inference provider ('llm', 'openai', 'local-mlx')")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "ingest-resolve",
        help="Resolve an active ingestion approval request with an authentic operator decision (approve or decline).",
    )
    command.add_argument("--request-id", required=True, type=str, help="Approval request ID (e.g. req-ingest-xxxx)")
    command.add_argument("--decision", required=True, choices=["approve", "decline"], help="Operator decision: 'approve' to publish or 'decline' to halt without mutating graph")
    command.add_argument("--ledger-path", type=Path, default=None, help="Explicit path to HITL decision ledger")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    _add_hitl_inbox_subparser(subparsers)

    command = subparsers.add_parser(
        "backtest-promote",
        help="Promote a completed backtest meeting output to the active knowledge graph via Universal Write Gateway.",
    )
    command.add_argument(
        "--meeting-dir",
        type=Path,
        required=True,
        help="Path to completed backtest meeting output directory containing Output 03",
    )
    command.add_argument(
        "--directive-id",
        type=str,
        default=None,
        help="Optional directive ID for graph publication",
    )
    command.add_argument(
        "--require-bge-m3",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enforce real BGE-M3 model",
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "backtest-collect",
        help="Collect and materialize completed OpenAI Batch API outputs into canonical deliverables.",
    )
    command.add_argument(
        "--meeting-dir",
        type=Path,
        required=True,
        help="Path to meeting directory containing pending batch or batch_results.jsonl",
    )
    command.add_argument(
        "--results-file",
        type=Path,
        default=None,
        help="Optional path to downloaded batch output JSONL file",
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "batch-queue",
        help="Inspect or submit queued OpenAI Batch API jobs for meeting directories (50%% cost discount).",
    )
    command.add_argument(
        "--meeting-dir",
        type=Path,
        required=True,
        help="Path to meeting directory containing batch_requests.jsonl or pending_batches.json",
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser(
        "backtest-health",
        help="Audit graph health for backtest bloat, duplicate assertions, and orphan evidence.",
    )
    command.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format: markdown (default) or json",
    )
    command.add_argument(
        "--recommend-gc",
        action="store_true",
        default=False,
        help="Emit explicit GC recommendation warning if bloat exceeds threshold",
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser("ask", aliases=["query"])
    command.add_argument("query_text", nargs="?", default=None, help="Natural language query string")
    command.add_argument("--question", required=False, default=None, help="Natural language query string")
    command.add_argument("--option", action="append", default=[], help="Candidate decision option; repeat for each caller-supplied option")
    command.add_argument("--criterion", action="append", default=[], help="Decision criterion; repeat for each explicit criterion")
    command.add_argument("--constraint", action="append", default=[], help="Hard decision constraint; repeat as needed")
    command.add_argument(
        "--assumption",
        action="append",
        default=[],
        help="Caller-supplied decision assumption; repeat as needed",
    )
    command.add_argument("--time-horizon")
    command.add_argument(
        "--query-type",
        choices=[
            "exact_lookup",
            "current_state",
            "current_state_corroboration",
            "relationship_explanation",
            "cross_workstream_synthesis",
            "gap_risk_inconsistency",
            "opportunity_nba",
            "candidate_promotion_review",
        ],
    )
    command.add_argument("--output-dir", type=Path)
    command.add_argument("--user-id", default="local-authorized-operator")
    command.add_argument(
        "--classification",
        choices=["public", "internal", "confidential", "restricted"],
        default="restricted",
    )
    command.add_argument("--audience", default="internal_consulting,nee-engineering")
    command.add_argument(
        "--card-id",
        help="Restrict exact, hybrid, hydration, and evidence stages to one active card view",
    )
    command.add_argument("--card-view-version", type=int)
    command.add_argument(
        "--card-candidate-review",
        action="store_true",
        help="Authorize the separately labeled candidate lane of a candidate-review card",
    )
    command.add_argument(
        "--human",
        action="store_true",
        help="Render seamless, human-readable markdown with footer lineage instead of raw JSON",
    )
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)
    _add_l2_controls(command)
    _add_l1_controller_controls(command)
    _add_query_routing_controls(command)
    _add_semantic_cache_controls(command)

    command = subparsers.add_parser("decision-serve", help="Serve the canonical route, graph-evidence, and decision API")
    command.add_argument("--host", default=os.environ.get("NEE_DEFAULT_HOST", "127.0.0.1"))
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--max-concurrency", type=int, default=None)
    command.add_argument("--max-queue-size", type=int, default=None)
    command.add_argument("--request-timeout-seconds", type=float, default=60.0)
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)
    _add_l2_controls(command)
    _add_l1_controller_controls(command)
    _add_semantic_cache_controls(command)

    command = subparsers.add_parser("audit-run")
    command.add_argument("--run-id", required=True)
    command.add_argument("--required-stages", nargs="*", default=[])
    _add_runtime(command, graph=False)

    command = subparsers.add_parser(
        "eval-route-run",
        help="Evaluate provider-free query destinations without loading BGE-M3 or L2",
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    command.add_argument("--evaluation-id")
    command.add_argument("--tag", action="append", default=[])

    command = subparsers.add_parser(
        "eval-l1-controller-run",
        help="Run the sealed provider-free Wave 8 L1 controller E1 treatment matrix",
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    command.add_argument("--evaluation-id", required=True)
    command.add_argument(
        "--baseline-suite",
        type=Path,
        default=ROOT / "evals" / "suites" / "query-l1-agentic-baseline-e1.json",
    )

    command = subparsers.add_parser(
        "eval-run",
        help="Run a digest-bound BGE-M3 query, ingestion, or dual evaluation suite",
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--evaluation-id")
    command.add_argument("--tag", action="append", default=[])
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "eval-bridge-run",
        help="Run a same-runtime source intake, activation, and retrieval bridge evaluation",
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--evaluation-id")
    command.add_argument("--tag", action="append", default=[])
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "eval-stress-run",
        help="Repeat a digest-bound BGE-M3 evaluation and reject behavioral drift",
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--repetitions", type=int, required=True)
    command.add_argument("--stress-id")
    command.add_argument("--tag", action="append", default=[])
    _add_runtime(command)
    _add_backend(command)

    command = subparsers.add_parser(
        "eval-assurance-plan",
        help="Validate the complete release-assurance evaluation portfolio",
    )
    command.add_argument(
        "--portfolio",
        type=Path,
        default=ROOT / "evals" / "suites" / "pipeline-assurance-portfolio-e2.json",
    )

    command = subparsers.add_parser(
        "eval-compare",
        help="Compare two exactly compatible sealed evaluation results",
    )
    command.add_argument("--baseline", type=Path, required=True)
    command.add_argument("--candidate", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)

    command = subparsers.add_parser(
        "eval-workflow-run",
        help=(
            "Run the network-free, content-silent agentic workflow assurance "
            "suite for one declared treatment"
        ),
    )
    command.add_argument("--suite", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    command.add_argument("--treatment-id", required=True)
    command.add_argument("--evaluation-id")

    command = subparsers.add_parser(
        "eval-workflow-compare",
        help="Compare two invariant-bound workflow assurance results",
    )
    command.add_argument("--baseline", type=Path, required=True)
    command.add_argument("--candidate", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)

    command = subparsers.add_parser("validate-runtime")
    _add_runtime(command)
    command.add_argument(
        "--internal-operational-policy",
        type=Path,
        default=DEFAULT_INTERNAL_OPERATIONAL_POLICY,
        help="Policy used to validate an internal operational pair",
    )

    command = subparsers.add_parser("internal-operational-qualify", help="Qualify one exact active internal BGE-M3 pair for trusted-team use")
    command.add_argument("--directive", type=Path, required=True)
    command.add_argument("--source-evaluation", type=Path, required=True)
    command.add_argument("--suite", type=Path, required=True)
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)

    command = subparsers.add_parser("internal-operational-status", help="Report exact-pair internal operational qualification state")
    _add_runtime(command)
    command.add_argument("--internal-operational-policy", type=Path, default=DEFAULT_INTERNAL_OPERATIONAL_POLICY)

    command = subparsers.add_parser("internal-operational-revoke", help="Immutably revoke the active pair's internal qualification")
    command.add_argument("--reason", required=True)
    _add_runtime(command)
    _add_authorization(command)
    command.add_argument("--internal-operational-policy", type=Path, default=DEFAULT_INTERNAL_OPERATIONAL_POLICY)

    command = subparsers.add_parser(
        "production-preflight",
        help=(
            "Verify the pinned CPU/BGE-M3 runtime and optionally "
            "enterprise-production authorization"
        ),
    )
    _add_runtime(command)
    command.add_argument("--model-path", type=Path, required=True)
    command.add_argument("--model-manifest", type=Path, required=True)
    command.add_argument("--model-revision", required=True)
    command.add_argument("--report", type=Path, required=True)
    command.add_argument(
        "--strict-production",
        action="store_true",
        help="Require signed authorization bound to all qualification resources",
    )
    _add_authorization(command)
    command.add_argument("--copilot-package", type=Path)
    command.add_argument("--architecture-package", type=Path)
    command.add_argument("--pdlc-question-file", type=Path)
    command.add_argument("--approval-id")
    command.add_argument("--copilot-decisions", type=Path)
    command.add_argument("--architecture-decisions", type=Path)

    command = subparsers.add_parser(
        "executive-insights",
        help="Execute Golden CTO strategic queries and generate executive intelligence briefings",
    )
    _add_runtime(command)
    command.add_argument(
        "--pattern",
        choices=["all", "contradictions", "duplications", "blockers", "fragility", "reuse"],
        default="all",
        help="Target strategic insight pattern to evaluate",
    )
    command.add_argument(
        "--tenant-id",
        default=None,
        help="Filter findings by explicit tenant / client partition",
    )
    command.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output serialization format",
    )

    command = subparsers.add_parser("demo")
    command.add_argument("--package", type=Path, required=True)
    command.add_argument("--approval-id", default="local-demo-candidate-release")
    command.add_argument(
        "--question",
        default="What are the strongest PDLC opportunities across NEA, what pain points do they address, and why are these the right areas?",
    )
    command.add_argument("--output-dir", type=Path)
    command.add_argument(
        "--query-type",
        choices=[
            "exact_lookup",
            "current_state",
            "current_state_corroboration",
            "relationship_explanation",
            "cross_workstream_synthesis",
            "gap_risk_inconsistency",
            "opportunity_nba",
            "candidate_promotion_review",
        ],
        default="opportunity_nba",
    )
    command.add_argument("--rollback", action="store_true")
    command.add_argument("--audience", default="internal_consulting,nee-engineering")
    _add_runtime(command)
    _add_backend(command)
    _add_authorization(command)
    _add_l2_controls(command)
    _add_l1_controller_controls(command)
    _add_query_routing_controls(command)
    _add_semantic_cache_controls(command)

    command = subparsers.add_parser("copilot", help="Governed graph-grounded live meeting advisory")
    command.add_argument("prompt", nargs="?", default=None, help="Optional natural-language prompt or operator question")
    command.add_argument(
        "--intent", choices=["next-questions", "next-actions", "respond", "graph-context", "gaps", "connect", "nbq", "review"],
        required=False, default=None, help="Copilot advisory intent: next-questions, next-actions, respond, graph-context, gaps, or connect",
    )
    command.add_argument("--request", "--query", "--prompt", dest="request", help="Explicit natural-language prompt or operator instruction")
    command.add_argument("--topic", help="Explicit topic query or keyword filter for context selection")
    command.add_argument("--target", help="Target person name for --intent respond")
    command.add_argument("--words", type=int, default=None, help="Optional word limit for copilot response (unbounded by default)")
    command.add_argument("--json", action="store_true", help="Print machine-readable result and diagnostics")
    command.add_argument("--meeting-dir", help="Direct path to meeting output directory")
    command.add_argument("--project", "--engagement", dest="project", help="Explicit engagement scope (e.g. jarvis, lens)")
    command.add_argument("--bypass-graph-cache", action="store_true", default=False, help="Bypass in-memory precomputed graph cache and force direct disk or hybrid fallback retrieval")
    session_group = command.add_mutually_exclusive_group()
    session_group.add_argument("--session", help="Session ID in session registry")
    session_group.add_argument("--active", action="store_true", help="Inspect latest active session")
    _add_runtime(command)

    command = subparsers.add_parser(
        "session-inspect",
        help="Inspect active governed recording sessions"
    )
    inspect_group = command.add_mutually_exclusive_group()
    inspect_group.add_argument("--session")
    inspect_group.add_argument("--active", action="store_true")
    _add_runtime(command)

    command = subparsers.add_parser("answer-feedback", help="Record operator feedback on an AnswerLedger record")
    command.add_argument("answer_id", help="Target AnswerRecord ID (e.g. ans-...)")
    command.add_argument("--rating", choices=["useful", "not_useful", "wrong"], required=True, help="Operator rating")
    command.add_argument("--note", help="Optional qualitative rationale or feedback note")
    command.add_argument("--origin", default="USER_EXPLICIT", help="Feedback origin marker (default: USER_EXPLICIT)")
    _add_runtime(command)

    command = subparsers.add_parser("answer-eval", help="Offline sealed evaluation harness for AnswerLedger answers")
    command.add_argument("--pipeline", choices=["copilot", "query", "both"], default="both", help="Target pipeline")
    command.add_argument("--since", help="Filter answers on or after date (MM/DD/YYYY or MM-DD-YYYY)")
    command.add_argument("--session", help="Filter answers by session ID")
    command.add_argument("--candidate-params", type=Path, help="Path to candidate parameter JSON for replay simulation")
    command.add_argument("--llm-judge", action="store_true", default=False, help="Run optional LLM-judge scoring")
    command.add_argument("--output", type=Path, help="Optional output receipt file path")
    _add_runtime(command)

    from dual_pipeline.cli.golden_label_handlers import add_golden_label_parser
    add_golden_label_parser(subparsers)

    command = subparsers.add_parser(
        "record-meeting",
        aliases=["record"],
        help="Unified live audio recording and real-time transcription engine (mandatory isolated worktree branch & HITL enforced)",
    )
    command.add_argument("--project", default=None, help="Governed project name (collected asynchronously or post-recording if omitted)")
    command.add_argument("--meeting", default=None, help="Meeting title (collected asynchronously or post-recording if omitted)")
    command.add_argument("--date", default=None, help="Meeting date in MM-DD-YYYY or MM/DD/YYYY format (collected asynchronously or post-recording if omitted)")
    command.add_argument("--attendees", default=None, help="Attendee roster (freeform text or JSON) (collected asynchronously or post-recording if omitted)")
    command.add_argument("--device", default=":0", help="AVFoundation audio input device index (default: :0)")
    command.add_argument("--segment-seconds", type=int, default=15, help="Rolling slice duration in seconds")
    command.add_argument("--whisper-prompt", default=None, help="Custom Whisper terminology prompt")
    command.add_argument("--backend", choices=["auto", "mlx"], default="auto", help="Transcription backend (auto, mlx; cloud transcription of raw chunks is prohibited)")
    command.add_argument("--audio-source", choices=["auto", "system", "mic", "mixed", "loopback", "iphone-speaker", "iphone_speaker"], default="auto", help="Audio source (auto, system, mic, mixed, iphone-speaker)")
    command.add_argument("--iphone-speaker", dest="audio_source", action="store_const", const="iphone-speaker", help="Use MacBook Pro Microphone with Standard Mic Mode for iPhone speakerphone input")
    command.add_argument("--whisper-model", default=None, help="Custom MLX Whisper model path or HF repo")
    command.add_argument("--copilot", action="store_true", default=False, help="Enable live copilot advisory feedback (default: False, requires graph connection)")
    command.add_argument("--no-copilot", action="store_true", help="Explicitly disable live copilot advisory feedback")
    command.add_argument("--copilot-prefetch", action=argparse.BooleanOptionalAction, default=True, help="Enable knowledge graph context prefetch at meeting start")
    command.add_argument("--detach", action="store_true", default=False, help="Dispatch capture in background daemon and return immediate start receipt")
    command.add_argument("--session-id", default=None, help="Explicit session identifier for background capture")
    command.add_argument("--resume", action="store_true", default=False, help="Resume an existing session and append to prior chunks without quarantining")
    command.add_argument("--immediate-worktree", action="store_true", default=False, help="Immediately provisions the Git worktree branch on launch with clean-slate verification")
    command.add_argument("--fast-start", action="store_true", default=True, help="Fast-path audio capture launch in <= 500ms (default: True)")
    command.add_argument("--instant", action="store_true", default=True, help="Zero-latency instant audio capture (<200ms) with parallel worktree binding (default: True)")
    command.add_argument("--no-instant", action="store_false", dest="instant", help="Disable zero-latency instant audio capture spooler")
    command.add_argument("--probation-ms", type=int, default=1000, help="Probation verification duration in milliseconds before returning status envelope (default: 1000)")
    command.add_argument("--inactivity-timeout", type=float, default=0.0, help="Inactivity silence timeout in seconds (default: 0.0, disabled for live meetings)")

    command = subparsers.add_parser("speaker-hint", aliases=["hint"], help="Apply operator speaker hint by quoted words to live or recorded session")
    command.add_argument("--quote", default="", help="Exact or approximate words spoken by the speaker")
    command.add_argument("--speaker", default="", help="Speaker name from attendee roster")
    command.add_argument("--session", default=None, help="Target session ID")
    command.add_argument("--active", action="store_true", default=True, help="Target the currently active session")
    command.add_argument("--meeting-dir", type=Path, default=None, help="Target meeting directory if session is offline")
    command.add_argument("--force", action="store_true", default=False, help="Force assignment even if speaker is not in roster")
    command.add_argument("--remove-attendee", default=None, help="Remove mistakenly added attendee from session roster")
    command.add_argument("--project", default=None, help="Project name scope")
    _add_runtime(command, graph=False)

    from dual_pipeline.cli.voiceprint_handlers import add_learn_voiceprints_parser
    add_learn_voiceprints_parser(subparsers)

    command = subparsers.add_parser("run-history", help="Query and display execution run history and terminal status from the learning ledger")
    command.add_argument("--limit", type=int, default=20, help="Maximum number of runs to display (default: 20)")
    command.add_argument("--status", help="Filter runs by status (e.g. COMPLETE, FAIL, ROLLED_BACK)")
    command.add_argument("--since", help="Filter runs created on or after date/time (e.g. 2026-09-01)")
    command.add_argument("--json", action="store_true", help="Output history in JSON format instead of table")
    _add_runtime(command, graph=False)

    command = subparsers.add_parser("graph-export", help="Export canonical graph in standard formats (turtle, json-ld, csv)")
    _add_runtime(command, graph=False)
    command.add_argument("--graph", type=Path, default=None, help="Explicit path to canonical graph database")
    command.add_argument("--format", choices=["turtle", "json-ld", "csv"], default="turtle", help="Output export format (default: turtle)")
    command.add_argument("--output", type=Path, default=None, help="Target output file path (default: stdout)")

    command = subparsers.add_parser("graph-sync", help="Export canonical knowledge graph tables to data warehouse formats (parquet, jsonl, csv, sqlite)")
    _add_runtime(command, graph=False)
    command.add_argument("--graph", type=Path, default=None, help="Explicit path to canonical graph database")
    command.add_argument("--destination", choices=["parquet", "jsonl", "csv", "sqlite"], default="jsonl", help="Target warehouse format (default: jsonl)")
    command.add_argument("--output-dir", type=Path, required=True, help="Destination directory for synchronized table files")
    command.add_argument("--incremental-since", type=str, default=None, help="ISO timestamp filter to only sync records created/updated after this date")
    from dual_pipeline.cli.intake_approval_helper import add_intake_approval_resume_parser
    add_intake_approval_resume_parser(subparsers)
    from dual_pipeline.cli.formats_cmd import add_formats_parser
    add_formats_parser(subparsers)
    from dual_pipeline.cli.promote import add_promote_fast_parser
    add_promote_fast_parser(subparsers)
    return parser
