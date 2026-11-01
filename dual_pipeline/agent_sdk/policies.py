"""Safety, cost, and graph-mutation policies for Antigravity SDK agents.

Enforces:
1. Free auto-allowance for non-mutating read-only graph and retrieval tools
2. Mandatory operator confirmation (ask_user) for expensive Dataproc batch compute
3. Hard denial (deny) on unmanaged shell execution and ungoverned graph drops/deletions
"""
from __future__ import annotations

from typing import List

from google.antigravity.hooks import policy
from google.antigravity.hooks.policy import Decision, Policy

# Read-only tools authorized for autonomous evaluation
READ_ONLY_SAFE_TOOLS = (
    "query_knowledge_graph",
    "inspect_graph_snapshot",
    "read_assertion_provenance",
    "evaluate_candidate_retrieval",
    "compute_hybrid_scoring",
    "execute_spark_sql",
    "inspect_spark_batch_status",
    "validate_rfc8259_canonical_hash",
    "verify_contract_compliance",
    "nee_query",
    "nee_intake_status",
    "graphdb_read_query",
    "graphdb_snapshot_state",
)

# Compute-intensive or state-mutating tools requiring human confirmation
HITL_REQUIRED_TOOLS = (
    "submit_spark_graph_batch",
    "intake_directive_approve",
    "uwg_write_transaction",
)

# Forbidden actions that fail closed
STRICTLY_DENIED_TOOLS = (
    "run_command",  # Prevent arbitrary shell bypasses
    "drop_graph_database",
    "purge_audit_ledger",
)


class NeeSecurityPolicy:
    """Configures multi-layer safety policies for Decision Intelligence agents."""

    @classmethod
    def build_governed_policies(
        cls,
        allow_read_only: bool = True,
        require_hitl_for_compute: bool = True,
        deny_arbitrary_shell: bool = True,
    ) -> List[Policy]:
        """Construct the prioritized policy list matching Decision Intelligence governance."""
        policies: List[Policy] = []

        # 1. Highest Priority: Specific Deny for forbidden destructive tools
        if deny_arbitrary_shell:
            for tool_name in STRICTLY_DENIED_TOOLS:
                policies.append(policy.deny(tool_name))

        # 2. Specific Ask for expensive Dataproc compute or intake mutation
        if require_hitl_for_compute:
            for tool_name in HITL_REQUIRED_TOOLS:
                policies.append(policy.ask_user(tool_name))

        # 3. Specific Allow for read-only query and validation tools
        if allow_read_only:
            for tool_name in READ_ONLY_SAFE_TOOLS:
                policies.append(policy.allow(tool_name))

        return policies

    @classmethod
    def evaluate_static_decision(
        cls, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Decision:
        """Helper to evaluate what decision would be reached for a given tool name."""
        # Unrelated DeepWiki ask_question tool is read-only documentation query
        if tool_name == "ask_question" and arguments and ("repoName" in arguments or "repo_name" in arguments):
            return Decision.APPROVE
        if tool_name in STRICTLY_DENIED_TOOLS:
            return Decision.DENY
        if tool_name in HITL_REQUIRED_TOOLS:
            return Decision.ASK_USER
        if tool_name in READ_ONLY_SAFE_TOOLS:
            return Decision.APPROVE
        # Default policy for un-enumerated tools is conservative DENY or ASK
        return Decision.ASK_USER
