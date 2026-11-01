"""Constants for governed query L1 shadow execution."""
from __future__ import annotations

SHADOW_MODES = ("off", "shadow")
ATTEMPT_CONTRACT = "query-retrieval-attempt"
OBSERVATION_CONTRACT = "query-plan-observation"
TERMINAL_CONTRACT = "query-plan-terminal"
TELEMETRY_KEYS = frozenset(
    {
        "event_type",
        "mode",
        "plan_digest",
        "coverage_manifest_digest",
        "attempt_zero_digest",
        "observation_zero_digest",
        "shadow_action_digest",
        "terminal_digest",
        "comparison_digest",
        "action_count",
        "attempt_count",
        "selected_record_count",
        "unresolved_requirement_count",
        "provider_call_count",
        "graph_mutation_count",
        "elapsed_ms",
        "result_bytes",
        "controller_terminal_reason",
        "runtime_terminal_outcome",
        "runtime_next_stage",
        "executed",
        "reason_code",
        "budget",
    }
)
LANES = ("approved", "candidate", "external_public")
