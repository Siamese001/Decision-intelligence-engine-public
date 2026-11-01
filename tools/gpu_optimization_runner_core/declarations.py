"""Wave declarations, milestones, deliverables, and acceptance tests for GPU optimization."""
from __future__ import annotations

from typing import Any, Dict

WAVE_DECLARATIONS: Dict[int, Dict[str, Any]] = {
    1: {
        "title": "Establish correct execution and scoring",
        "deliverables": [
            "tools/run_gpu_optimization_wave.py",
            "tools/gpu_optimization_runner_core/__init__.py",
            "tools/gpu_optimization_runner_core/declarations.py",
            "tools/gpu_optimization_runner_core/telemetry.py",
            "tools/gpu_optimization_runner_core/receipt.py",
            "dual_pipeline/index_core/colbert_codec.py",
            "dual_pipeline/index_core/fusion.py",
            "dual_pipeline/runtime/model_runtime.py",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave1_e2e.py",
            "tests/adversarial/test_gpu_wave1_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave1_e2e.py",
            "tests/adversarial/test_gpu_wave1_adversarial.py",
        ],
        "prerequisite_waves": [],
    },
    2: {
        "title": "Establish reproducible baselines",
        "deliverables": [
            "benchmarks/gpu_optimization/__init__.py",
            "benchmarks/gpu_optimization/corpus_manifests.py",
            "benchmarks/gpu_optimization/workload_specs.py",
            "benchmarks/gpu_optimization/phase_instrumentation.py",
            "benchmarks/gpu_optimization/adapters.py",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave2_e2e.py",
            "tests/adversarial/test_gpu_wave2_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave2_e2e.py",
            "tests/adversarial/test_gpu_wave2_adversarial.py",
        ],
        "prerequisite_waves": [1],
    },
    3: {
        "title": "Bound model ownership and admission",
        "deliverables": [
            "dual_pipeline/runtime/memory_governor.py",
            "dual_pipeline/runtime/model_runtime.py",
            "dual_pipeline/runtime/host_admission.py",
            "dual_pipeline/runtime/__init__.py",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave3_e2e.py",
            "tests/adversarial/test_gpu_wave3_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave3_e2e.py",
            "tests/adversarial/test_gpu_wave3_adversarial.py",
        ],
        "prerequisite_waves": [1, 2],
    },
    4: {
        "title": "Stream and tune embedding batches",
        "deliverables": [
            "dual_pipeline/index_core/backends.py",
            "dual_pipeline/index_core/differential.py",
            "dual_pipeline/index_core/sidecar.py",
            "dual_pipeline/index_core/embedding_space_contract.py",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave4_e2e.py",
            "tests/adversarial/test_gpu_wave4_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave4_e2e.py",
            "tests/adversarial/test_gpu_wave4_adversarial.py",
        ],
        "prerequisite_waves": [1, 2, 3],
    },
    5: {
        "title": "Optimize retrieval at the measured crossover",
        "deliverables": [
            "dual_pipeline/index_core/retriever_cache.py",
            "dual_pipeline/index_core/fusion.py",
            "dual_pipeline/index_core/gpu_scoring.py",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave5_e2e.py",
            "tests/adversarial/test_gpu_wave5_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave5_e2e.py",
            "tests/adversarial/test_gpu_wave5_adversarial.py",
        ],
        "prerequisite_waves": [1, 2, 3, 4],
    },
    6: {
        "title": "Qualify and prepare reversible rollout",
        "deliverables": [
            "docs/operations/gpu_optimization.md",
            "tests/manifests/gpu_optimization.json",
            "tests/end_to_end/test_gpu_wave6_e2e.py",
            "tests/adversarial/test_gpu_wave6_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/end_to_end/test_gpu_wave6_e2e.py",
            "tests/adversarial/test_gpu_wave6_adversarial.py",
        ],
        "prerequisite_waves": [1, 2, 3, 4, 5],
    },
}
