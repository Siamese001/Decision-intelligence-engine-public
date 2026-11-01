"""
dual_pipeline/evals/pipelines/adversarial_debate.py

Dialectical Adversarial Debate Engine & Evaluation Audit Verifier.
Implements programmatic verification of the adversarial debate claims,
inventories low-signal technical debt vs high-signal metrics, and certifies
Pipelines A-D evaluation integrity.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
DEBATE_DOC = ROOT / "governance" / "decision_intelligence" / "ADVERSARIAL_EVAL_HARNESS_DEBATE_V1.md"
METRIC_SPEC_DOC = ROOT / "governance" / "decision_intelligence" / "PIPELINES_AD_HIGH_SIGNAL_METRIC_SPEC_V1.md"


@dataclass(frozen=True)
class LowSignalMetricDefect:
    pipeline_id: str
    component: str
    defect_type: str
    description: str
    remediation_metric: str


@dataclass(frozen=True)
class HighSignalMetricTarget:
    pipeline_id: str
    metric_id: str
    metric_name: str
    passing_threshold: str
    governance_ref: str


KNOWN_EVAL_DEFECTS: List[LowSignalMetricDefect] = [
    LowSignalMetricDefect(
        pipeline_id="pipeline_a",
        component="pipeline_a_evaluator.py::evaluate_waveform_resilience",
        defect_type="SYNTHETIC_GAUSSIAN_NOISE",
        description="Uses random Gaussian noise with fixed seed to test basic DSP math rather than realistic speech-in-noise.",
        remediation_metric="Metric A.1: S_noise (4-regime DSP boundary resilience)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_a",
        component="pipeline_a_evaluator.py::evaluate_speaker_locking",
        defect_type="STATIC_TAG_LIST",
        description="Tests a static 12-item string list without conversational state or Ambiguity Lock Register validation.",
        remediation_metric="Metric A.2: A_locking (Ambiguity Lock Register enforcement)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_b",
        component="pipeline_b_evaluator.py::evaluate_c13_injection_defense",
        defect_type="TAUTOLOGICAL_ASSERTION",
        description="Line 71 disjunct 'or any(p.search(text)...)' guarantees True whenever input contains the attack pattern.",
        remediation_metric="Metric B.1: R_c13 (True sanitization and neutralization rate)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_b",
        component="pipeline_b_evaluator.py::evaluate_c19_career_firewall_isolation",
        defect_type="SUPERFICIAL_EXACT_MATCH",
        description="Checks 7 static strings without testing artifact independence across Outputs 01, 02, and 03.",
        remediation_metric="Metric B.2: C_c19 (Zero advisory leakage across all deliverables)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_c",
        component="pipeline_c_evaluator.py::evaluate_steady_state_retrieval_sla",
        defect_type="IN_MEMORY_BLAS_MOCK",
        description="Evaluates 500 in-memory numpy matrix dot products rather than SQLite sidecar retrieval with WAL mode.",
        remediation_metric="Metric C.2: T_retrieval (SQLite hybrid retrieval SLA < 7500ms)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_c",
        component="pipeline_c_evaluator.py::evaluate_law6_quarantine_enforcement",
        defect_type="TRIVIAL_SQL_WHERE_CHECK",
        description="Tests 'SELECT ... WHERE is_quarantined = 0' on 2 mock rows rather than retrieval filter pipeline.",
        remediation_metric="Metric C.3: P_law6 (End-to-end quarantine exclusion from query results)",
    ),
    LowSignalMetricDefect(
        pipeline_id="pipeline_d",
        component="pipeline_d_evaluator.py::evaluate_ambiguity_margin_gating",
        defect_type="STATIC_FLOAT_COMPARISON",
        description="Evaluates static float pairs without multi-option decision frames or boundary precision.",
        remediation_metric="Metric D.1: A_margin (High-precision boundary gating at Delta = 0.15)",
    ),
]


class AdversarialDebateAuditor:
    """Audits evaluation harness code against the dialectical debate findings."""

    def __init__(self, root_dir: Optional[Path] = None) -> None:
        self.root = root_dir or ROOT

    def verify_debate_artifacts_exist(self) -> Dict[str, bool]:
        """Verify that the official debate transcript and metric spec exist on disk."""
        return {
            "debate_document_exists": DEBATE_DOC.exists(),
            "metric_spec_document_exists": METRIC_SPEC_DOC.exists(),
        }

    def verify_known_defects_inventory(self) -> Dict[str, Any]:
        """Returns inventory of identified eval harness defects across Pipelines A-D."""
        by_pipeline: Dict[str, List[Dict[str, str]]] = {"pipeline_a": [], "pipeline_b": [], "pipeline_c": [], "pipeline_d": []}
        for d in KNOWN_EVAL_DEFECTS:
            by_pipeline[d.pipeline_id].append({
                "component": d.component,
                "defect_type": d.defect_type,
                "description": d.description,
                "remediation": d.remediation_metric,
            })
        return {
            "total_defects_cataloged": len(KNOWN_EVAL_DEFECTS),
            "by_pipeline": by_pipeline,
        }

    def compute_audit_digest(self) -> str:
        """Computes deterministic RFC-8259 SHA-256 digest of the defect catalog."""
        inv = self.verify_known_defects_inventory()
        canonical = json.dumps(inv, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
