"""
tests/test_all_agents_system_learning_top10_adversarial.py

Adversarial and edge-case verification for Wave 5:
Fleet-Wide Agent System Learning Remediation & Rollout.

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty strings, 0-length inputs, 0-byte priors, empty candidate votes.
2. Malformed input: Corrupted JSON priors, invalid type schemas (strings, lists in numeric slots), non-existent files.
3. Invariant bypass & clamping: Bounds enforcement for route multipliers [0.20, 0.70], council weights [0.5, 3.0], roundtable weights [0.5, 3.0], and worker batch floor (min 10).
4. Overflow & exhaustion: Token budget explosions, high error-rate bursts triggering worker batch throttling, retry exhaustion elevation.
5. Concurrency & race: Multi-threaded concurrent access across 16 threads for accumulators, weight resolvers, and specialist adapters.
6. Exact production regression: Component retry exhaustion elevation on output_2:appendix, specialist escalation to FRONTIER_HIGH_REASONING with recovery instructions.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.agent_sdk.orchestration import NeeAgentHierarchy
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.query_swarm import (
    DEFAULT_ROUTE_MULTIPLIERS,
    CompetitiveQuerySwarm,
    get_learned_route_multipliers,
)
from dual_pipeline.roundtable_orchestrator import (
    DEFAULT_ROUNDTABLE_ROLE_WEIGHTS,
    RoundtableOrchestrator,
    get_learned_roundtable_weights,
)
from dual_pipeline.spark.agent_worker import (
    WorkerTokenAccumulator,
)
from dual_pipeline.token_optimization.agent import (
    ReasoningEffortTier,
    TokenOptimizationAgent,
)
from tools.pr_governance_core.council import (
    ROLE_WEIGHTS,
    CouncilRole,
    get_calibrated_council_weights,
)


@pytest.fixture
def temp_integrator(tmp_path: Path) -> LivePipelineIntegrator:
    events_log = tmp_path / "wave5_adversarial_events.jsonl"
    return LivePipelineIntegrator(events_log_path=events_log)


# ==============================================================================
# Category 1: Boundary Payloads
# ==============================================================================


def test_boundary_empty_and_zero_byte_inputs(temp_integrator: LivePipelineIntegrator, tmp_path: Path):
    """Verifies that empty strings, empty files, and zero-length payloads are handled safely."""
    # Empty query in CompetitiveQuerySwarm
    swarm = CompetitiveQuerySwarm(integrator=temp_integrator)
    res = swarm.route_query("")
    assert res["route_id"] is not None
    assert "Default fallback plan" in res["plan"]
    assert res["escalation_required"] is True

    # Empty priors file fallback
    empty_file = tmp_path / "empty_priors.json"
    empty_file.write_text("", encoding="utf-8")
    assert get_learned_route_multipliers(empty_file) == DEFAULT_ROUTE_MULTIPLIERS
    assert get_learned_roundtable_weights(empty_file) == DEFAULT_ROUNDTABLE_ROLE_WEIGHTS
    assert get_calibrated_council_weights(empty_file) == ROLE_WEIGHTS

    # Empty votes dict in RoundtableOrchestrator
    orchestrator = RoundtableOrchestrator(topic="Testing Boundary")
    consensus = orchestrator.compute_weighted_consensus({})
    assert consensus == 0.0

    # WorkerTokenAccumulator on empty metrics
    acc = WorkerTokenAccumulator()
    assert acc.adapt_partition_batch_size(100) == 100
    assert acc.adapt_partition_batch_size(0) == 0

    # Specialist hierarchy on unknown name
    with pytest.raises(KeyError):
        NeeAgentHierarchy.adapt_specialist_for_failures("nonexistent_specialist", 3)


# ==============================================================================
# Category 2: Malformed Inputs
# ==============================================================================


def test_malformed_priors_and_corrupted_json(tmp_path: Path):
    """Verifies that corrupted JSON, invalid types, and malformed files safely fall back to defaults."""
    corrupted_file = tmp_path / "corrupted.json"
    corrupted_file.write_text("{\"learned_query_route_priors\": {malformed...", encoding="utf-8")

    assert get_learned_route_multipliers(corrupted_file) == DEFAULT_ROUTE_MULTIPLIERS
    assert get_learned_roundtable_weights(corrupted_file) == DEFAULT_ROUNDTABLE_ROLE_WEIGHTS
    assert get_calibrated_council_weights(corrupted_file) == ROLE_WEIGHTS

    # Non-numeric types inside priors
    type_violation_file = tmp_path / "type_violation.json"
    type_violation_file.write_text(json.dumps({
        "learned_query_route_priors": {
            "relational": "high",
            "temporal": [0.65],
            "strategic": None,
        },
        "learned_roundtable_weights": {
            "Governance_HITL_Agent": "strong",
        },
        "learned_council_weights": {
            "security": {"score": 2.0},
        }
    }), encoding="utf-8")

    # Non-numeric values must not overwrite defaults
    mults = get_learned_route_multipliers(type_violation_file)
    assert mults["relational"] == DEFAULT_ROUTE_MULTIPLIERS["relational"]
    assert mults["temporal"] == DEFAULT_ROUTE_MULTIPLIERS["temporal"]

    r_weights = get_learned_roundtable_weights(type_violation_file)
    assert r_weights["Governance_HITL_Agent"] == DEFAULT_ROUNDTABLE_ROLE_WEIGHTS["Governance_HITL_Agent"]

    c_weights = get_calibrated_council_weights(type_violation_file)
    assert c_weights[CouncilRole.SECURITY.value] == ROLE_WEIGHTS[CouncilRole.SECURITY.value]


# ==============================================================================
# Category 3: Invariant Bypass & Clamping
# ==============================================================================


def test_invariant_bypass_clamping_ranges(tmp_path: Path):
    """Verifies that malicious or out-of-bound weights are strictly clamped to governed ranges."""
    bypass_file = tmp_path / "bypass_priors.json"
    bypass_file.write_text(json.dumps({
        "learned_query_route_priors": {
            "relational": 0.01,   # Below floor 0.20
            "temporal": 999.0,    # Above ceiling 0.70
        },
        "learned_roundtable_weights": {
            "Governance_HITL_Agent": 0.05,  # Below floor 0.5
            "Transcript_Synthesis_Agent": 50.0,   # Above ceiling 3.0
        },
        "learned_council_weights": {
            "security": 0.0,      # Below floor 0.5
            "quality": 100.0,     # Above ceiling 3.0
        }
    }), encoding="utf-8")

    # Route multipliers strictly clamped to [0.20, 0.70]
    mults = get_learned_route_multipliers(bypass_file)
    assert mults["relational"] == 0.20
    assert mults["temporal"] == 0.70

    # Roundtable weights strictly clamped to [0.5, 3.0]
    r_weights = get_learned_roundtable_weights(bypass_file)
    assert r_weights["Governance_HITL_Agent"] == 0.5
    assert r_weights["Transcript_Synthesis_Agent"] == 3.0

    # Council weights strictly clamped to [0.5, 3.0]
    c_weights = get_calibrated_council_weights(bypass_file)
    assert c_weights["security"] == 0.5
    assert c_weights["quality"] == 3.0
    assert c_weights[CouncilRole.SECURITY.value] == 0.5
    assert c_weights[CouncilRole.QUALITY.value] == 3.0


# ==============================================================================
# Category 4: Overflow & Exhaustion
# ==============================================================================


def test_token_and_error_overflow_exhaustion():
    """Verifies accumulator stability under large token volumes and high error rates."""
    acc = WorkerTokenAccumulator()

    # Huge token additions without integer overflow
    huge_val = 10**9
    acc.add(prompt=huge_val, completion=huge_val, latency=50.0, is_error=False)
    metrics = acc.value
    assert metrics.total_tokens == 2 * huge_val

    # Burst of errors > 10% throttles batch size to half, clamped to floor 10
    acc.reset()
    for _ in range(5):
        acc.add(prompt=100, completion=100, latency=20.0, is_error=False)
    for _ in range(2):
        acc.add(prompt=100, completion=100, latency=20.0, is_error=True)
    # Error rate = 2 / 7 = 28.5% > 10%
    assert acc.adapt_partition_batch_size(100) == 50
    assert acc.adapt_partition_batch_size(15) == 10  # Enforces floor 10


# ==============================================================================
# Category 5: Concurrency & Race Conditions
# ==============================================================================


def test_concurrency_across_weight_lookups_and_accumulator(tmp_path: Path):
    """Verifies thread-safe execution across 16 parallel workers under high concurrent contention."""
    priors_file = tmp_path / "concurrent_priors.json"
    priors_file.write_text(json.dumps({
        "learned_query_route_priors": {"relational": 0.55},
        "learned_roundtable_weights": {"Governance_HITL_Agent": 2.2},
        "learned_council_weights": {"security": 2.5},
    }), encoding="utf-8")

    acc = WorkerTokenAccumulator()

    def worker_action(worker_id: int) -> bool:
        # Concurrent reads
        m = get_learned_route_multipliers(priors_file)
        assert m["relational"] == 0.55
        r = get_learned_roundtable_weights(priors_file)
        assert r["Governance_HITL_Agent"] == 2.2
        c = get_calibrated_council_weights(priors_file)
        assert c["security"] == 2.5

        # Concurrent accumulator updates
        is_err = (worker_id % 3 == 0)
        acc.add(prompt=100, completion=50, latency=10.0, is_error=is_err)

        # Specialist adaptation
        spec = NeeAgentHierarchy.adapt_specialist_for_failures(
            NeeAgentHierarchy.INGESTION_AGENT, failure_count=worker_id % 4
        )
        assert spec.name == "ingestion_specialist"
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(worker_action, i) for i in range(64)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 64
    assert all(results)
    assert acc.value.record_count == 64


# ==============================================================================
# Category 6: Exact Production Regression
# ==============================================================================


def test_exact_production_regression_retry_exhaustion_elevation(temp_integrator: LivePipelineIntegrator):
    """Regression: Retry exhaustion on specific deliverables must escalate effort without polluting general tasks."""
    opt_agent = TokenOptimizationAgent(integrator=temp_integrator)

    # Router tasks default to LOW
    assert opt_agent.calibrate_reasoning_effort("router_query") == ReasoningEffortTier.LOW

    # Inject production retry exhaustion on output_2:appendix
    event = temp_integrator.adapter.build_retry_exhaustion_event(
        target_component="output_2:appendix",
        total_attempts=2,
        reason="Repeated markdown syntax violations in appendix",
    )
    temp_integrator.record_outcome(event)

    # output_2:appendix elevates to XHIGH
    assert opt_agent.calibrate_reasoning_effort("output_2:appendix") == ReasoningEffortTier.XHIGH
    # Other router tasks remain unaffected at LOW
    assert opt_agent.calibrate_reasoning_effort("router_query") == ReasoningEffortTier.LOW

    # Specialist adaptation under retry failures elevates to FRONTIER_HIGH_REASONING
    frontier_model = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING)
    adapted = NeeAgentHierarchy.adapt_specialist_for_failures(
        NeeAgentHierarchy.SPARK_AGENT, failure_count=2
    )
    assert adapted.model == frontier_model
    assert "RECOVERY MODE ACTIVE" in adapted.system_instructions
