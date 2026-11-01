"""Command registry for lazy CLI subcommand dispatch.

Provides decentralized registration of CLI command handlers so that command
execution logic and heavy dependencies are loaded on-demand rather than at
import time.
"""
from __future__ import annotations

import argparse
from typing import Any, Callable, Dict, List, Optional

HandlerFunc = Callable[[argparse.Namespace], Any]
HandlerLoader = Callable[[], HandlerFunc]


class CommandRegistry:
    """Registry holding command registration callbacks and lazy execution handlers."""

    def __init__(self) -> None:
        self._commands: Dict[str, HandlerLoader] = {}

    def register(
        self,
        name: str,
        handler_loader: HandlerLoader,
    ) -> None:
        """Register a command name with its lazy handler loader."""
        self._commands[name] = handler_loader

    def is_registered(self, name: str) -> bool:
        """Check if a command is registered."""
        return name in self._commands

    def all_commands(self) -> List[str]:
        """Return sorted list of all registered command names."""
        return sorted(self._commands.keys())

    list_commands = all_commands

    def get_handler(self, name: str) -> HandlerFunc:
        """Load and return the handler function for a command."""
        loader = self._commands.get(name)
        if loader is None:
            raise ValueError(f"Unknown CLI command: {name!r}")
        return loader()

    def dispatch(self, args: argparse.Namespace) -> Any:
        """Dispatch execution to the registered lazy handler for the parsed arguments."""
        cmd = getattr(args, "command", None)
        if not cmd:
            raise ValueError("No command specified in parsed arguments.")
        handler = self.get_handler(cmd)
        return handler(args)


def _register_default_commands(reg: CommandRegistry) -> None:
    # Direct completion guard commands
    reg.register("intake-directive-open", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).open(
        directive_id=args.directive_id, scope=args.scope, packages=args.package, batch_admission_manifest=args.batch_manifest, require_bge_m3=args.require_bge_m3,
    ))
    reg.register("intake-directive-attach", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).attach(args.directive_id, args.package))
    reg.register("intake-directive-approve", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).record_human_decision(
        args.directive_id,
        decision=args.decision,
        classification_digest=args.classification_digest,
        reviewer=getattr(args, "reviewer", None) or "operator",
        rationale=getattr(args, "rationale", None) or f"Operator recorded {args.decision} decision for directive {args.directive_id}",
    ))
    reg.register("intake-directive-classify", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).classify(args.directive_id))
    reg.register("intake-directive-admit-batch", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_directive_admit_batch"])._handle_intake_directive_admit_batch)
    reg.register("intake-directive-status", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).status(args.directive_id))
    reg.register("intake-delivery-status", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).delivery_status(args.directive_id))
    reg.register("intake-directive-block", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).block(
        args.directive_id,
        reason_code=args.reason_code,
        reason=args.reason,
    ))
    reg.register("intake-directive-cancel", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).cancel(
        args.directive_id,
        reason=args.reason,
    ))
    reg.register("intake-completion-gate", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).complete(args.directive_id, args.receipt))
    reg.register("intake-completion-noop", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_completion_guard"])._completion_guard(args).complete_noop(
        args.directive_id,
        run_ids=args.run_id,
    ))

    # Handlers from dual_pipeline.cli.handlers
    reg.register("candidate-backlog-cleanup", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_candidate_backlog_cleanup"])._candidate_backlog_cleanup)
    reg.register("source-retirement", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_source_retirement"])._source_retirement)
    reg.register("graph-consolidate", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_graph_consolidate"])._graph_consolidate)
    reg.register("graph-consolidate-scan", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_graph_consolidate_scan"])._graph_consolidate_scan)
    reg.register("combined-remediation", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_combined_remediation"])._combined_remediation)
    reg.register("combined-remediation-finalize", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_combined_remediation_finalize"])._combined_remediation_finalize)
    reg.register("validation-closeout", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_validation_closeout"])._validation_closeout)
    reg.register("intake-publish", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_intake_publish"])._intake_publish)
    reg.register("intake-approval-resume", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_intake_approval_resume"])._intake_approval_resume)
    reg.register("ingest-meeting", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_ingest_meeting"])._ingest_meeting)
    reg.register("ingest-document", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_ingest_document"])._ingest_document)
    reg.register("formats", lambda: __import__("dual_pipeline.cli.formats_cmd", fromlist=["handle_formats_command"]).handle_formats_command)
    reg.register("ingest-resolve", lambda: __import__("dual_pipeline.cli.ingest_resolve_cmd", fromlist=["execute_ingest_resolve"]).execute_ingest_resolve)
    reg.register("hitl-inbox", lambda: __import__("dual_pipeline.hitl_governance_core.inbox_runner", fromlist=["execute_hitl_inbox"]).execute_hitl_inbox)
    reg.register("cards-refactor", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_cards_refactor"])._cards_refactor)
    reg.register("graph-scout", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["_graph_scout"])._graph_scout)
    reg.register("learn-voiceprints", lambda: __import__("dual_pipeline.cli.voiceprint_handlers", fromlist=["handle_learn_voiceprints"]).handle_learn_voiceprints)

    # Handlers from dual_pipeline.cli.operational
    reg.register("graph-diff", lambda: __import__("dual_pipeline.cli.operational", fromlist=["execute_graph_diff"]).execute_graph_diff)
    reg.register("graph-stats", lambda: __import__("dual_pipeline.cli.operational", fromlist=["execute_graph_stats"]).execute_graph_stats)
    reg.register("graph-gc", lambda: __import__("dual_pipeline.cli.operational", fromlist=["execute_graph_gc"]).execute_graph_gc)
    reg.register("runtime-evidence", lambda: __import__("dual_pipeline.cli.operational", fromlist=["execute_runtime_evidence"]).execute_runtime_evidence)
    reg.register("semantic-unit-audit", lambda: __import__("dual_pipeline.cli.operational", fromlist=["execute_semantic_unit_audit"]).execute_semantic_unit_audit)
    reg.register("semantic-unit-release", lambda: __import__("dual_pipeline.cli.semantic_unit_release_cmd", fromlist=["execute_semantic_unit_release"]).execute_semantic_unit_release)

    # Core engine handlers
    reg.register("ingest", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_ingest"])._handle_ingest)
    reg.register("ingest-evidence-envelope", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_ingest"])._handle_ingest)
    reg.register("intake-status", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_status"])._handle_intake_status)
    reg.register("intake-review", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_review"])._handle_intake_review)
    reg.register("semantic-cache-config", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_semantic_cache_config"])._handle_semantic_cache_config)
    reg.register("semantic-cache-status", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_semantic_cache_status"])._handle_semantic_cache_status)
    reg.register("bootstrap-runtime", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_bootstrap_runtime"])._handle_bootstrap_runtime)
    reg.register("uwg-evaluate", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_uwg_evaluate"])._handle_uwg_evaluate)
    reg.register("retrieval-release-evaluate", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_retrieval_release_evaluate"])._handle_retrieval_release_evaluate)
    reg.register("retrieval-release-suite-build", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_retrieval_release_suite_build"])._handle_retrieval_release_suite_build)
    reg.register("uwg-register-approval", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_uwg_register_approval"])._handle_uwg_register_approval)
    reg.register("uwg-promote", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_uwg_promote"])._handle_uwg_promote)
    reg.register("uwg-activate", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_uwg_activate_or_rollback"])._handle_uwg_activate_or_rollback)
    reg.register("uwg-rollback", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_uwg_activate_or_rollback"])._handle_uwg_activate_or_rollback)
    reg.register("intake-release-prepare", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_release_prepare"])._handle_intake_release_prepare)
    reg.register("intake-release-activate", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_release_activate"])._handle_intake_release_activate)
    reg.register("intake-batch-release", lambda: lambda args: __import__("dual_pipeline.cli.handlers", fromlist=["_release_batch"])._release_batch(args, run_ids=args.run_id))
    reg.register("intake-release-rollback", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_intake_release_rollback"])._handle_intake_release_rollback)
    reg.register("ask", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_ask"])._handle_ask)
    reg.register("query", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_ask"])._handle_ask)
    reg.register("decision-serve", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_decision_serve"])._handle_decision_serve)
    reg.register("eval-route-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_route_run"])._handle_eval_route_run)
    reg.register("eval-l1-controller-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_l1_controller_run"])._handle_eval_l1_controller_run)
    reg.register("eval-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_run"])._handle_eval_run)
    reg.register("eval-bridge-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_bridge_run"])._handle_eval_bridge_run)
    reg.register("eval-stress-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_stress_run"])._handle_eval_stress_run)
    reg.register("eval-assurance-plan", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_assurance_plan"])._handle_eval_assurance_plan)
    reg.register("eval-compare", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_compare"])._handle_eval_compare)
    reg.register("eval-workflow-run", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_workflow_run"])._handle_eval_workflow_run)
    reg.register("eval-workflow-compare", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_eval_workflow_compare"])._handle_eval_workflow_compare)
    reg.register("audit-run", lambda: lambda args: __import__("dual_pipeline.cli.engine", fromlist=["_audit_run"])._audit_run(args))
    reg.register("validate-runtime", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_validate_runtime"])._handle_validate_runtime)
    reg.register("internal-operational-qualify", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_internal_operational_qualify"])._handle_internal_operational_qualify)
    reg.register("internal-operational-status", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_internal_operational_status"])._handle_internal_operational_status)
    reg.register("internal-operational-revoke", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_internal_operational_revoke"])._handle_internal_operational_revoke)
    reg.register("production-preflight", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_production_preflight"])._handle_production_preflight)
    reg.register("executive-insights", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_executive_insights"])._handle_executive_insights)
    reg.register("demo", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_demo"])._handle_demo)
    reg.register("session-inspect", lambda: __import__("dual_pipeline.cli.copilot_handlers", fromlist=["handle_session_inspect"]).handle_session_inspect)
    reg.register("copilot", lambda: __import__("dual_pipeline.cli.copilot_handlers", fromlist=["handle_copilot"]).handle_copilot)
    reg.register("answer-feedback", lambda: __import__("dual_pipeline.cli.answer_feedback_handlers", fromlist=["handle_answer_feedback"]).handle_answer_feedback)
    reg.register("answer-eval", lambda: __import__("dual_pipeline.cli.answer_eval_handlers", fromlist=["handle_answer_eval"]).handle_answer_eval)
    reg.register("golden-label", lambda: __import__("dual_pipeline.cli.golden_label_handlers", fromlist=["handle_golden_label"]).handle_golden_label)
    reg.register("speaker-hint", lambda: __import__("dual_pipeline.cli.speaker_hint_handlers", fromlist=["handle_speaker_hint"]).handle_speaker_hint)
    reg.register("hint", lambda: __import__("dual_pipeline.cli.speaker_hint_handlers", fromlist=["handle_speaker_hint"]).handle_speaker_hint)
    reg.register("record-meeting", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_record_meeting"])._handle_record_meeting)
    reg.register("record", lambda: __import__("dual_pipeline.cli.engine", fromlist=["_handle_record_meeting"])._handle_record_meeting)
    reg.register("run-history", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["handle_run_history"]).handle_run_history)
    reg.register("graph-export", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["handle_graph_export"]).handle_graph_export)
    reg.register("graph-sync", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["handle_graph_sync"]).handle_graph_sync)
    reg.register("backtest-collect", lambda: __import__("dual_pipeline.cli.batch_cli", fromlist=["_backtest_collect"])._backtest_collect)
    reg.register("batch-queue", lambda: __import__("dual_pipeline.cli.batch_cli", fromlist=["_batch_queue"])._batch_queue)
    reg.register("backtest-promote", lambda: __import__("dual_pipeline.cli.promote", fromlist=["_backtest_promote"])._backtest_promote)
    reg.register("backtest-health", lambda: __import__("dual_pipeline.cli.health_cli", fromlist=["_backtest_health"])._backtest_health)
    reg.register("recover-session", lambda: __import__("dual_pipeline.cli.handlers", fromlist=["handle_recover_session"]).handle_recover_session)


_default_registry: Optional[CommandRegistry] = None


def get_default_registry() -> CommandRegistry:
    """Return the initialized default command registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = CommandRegistry()
        _register_default_commands(_default_registry)
        _default_registry.register("promote-fast", lambda: __import__("dual_pipeline.cli.promote", fromlist=["_promote_fast"])._promote_fast)
    return _default_registry


registry = get_default_registry()
