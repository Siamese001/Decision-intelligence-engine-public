"""Three-Level (L1 Micro -> L2 Meso -> L3 Macro) Evaluation Rollup Engine.

Enforces hierarchical dependency gating from atomic software engineering
mechanics up to the $1.2M Executive Cognitive Exoskeleton business case:
- Level 1: Micro Foundational Mechanics (Trajectory, Schemas, DSP, JSON Repair)
- Level 2: Meso Pipeline Functional & Security Invariants (Pipelines A-D)
- Level 3: Macro Strategic Alpha & Executive Moat (10 Strategic Dimensions)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Level1MicroScorecard:
    """Micro Foundational Mechanics Scorecard (Atomic Execution & Syntactic Health)."""
    trajectory_efficiency: float = 100.0  # 0 to 100
    schema_compliance_rate: float = 1.0   # 0.0 to 1.0
    dsp_math_resilience: float = 1.0      # 0.0 to 1.0
    json_repair_success_rate: float = 1.0 # 0.0 to 1.0
    passed: bool = True
    score: float = 100.0
    diagnostic: str = "All micro foundations nominal"

    def compute(self) -> None:
        rates = [
            self.trajectory_efficiency / 100.0,
            self.schema_compliance_rate,
            self.dsp_math_resilience,
            self.json_repair_success_rate,
        ]
        self.score = round((sum(rates) / len(rates)) * 100.0, 2)
        self.passed = (
            self.schema_compliance_rate == 1.0
            and self.json_repair_success_rate == 1.0
            and self.dsp_math_resilience >= 0.75
            and self.score >= 80.0
        )
        if not self.passed:
            self.diagnostic = f"Micro foundation failed (score={self.score}%, schema={self.schema_compliance_rate})"
        else:
            self.diagnostic = "All micro foundations nominal"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": "Level 1: Micro Foundation",
            "score": self.score,
            "passed": self.passed,
            "trajectory_efficiency": self.trajectory_efficiency,
            "schema_compliance_rate": self.schema_compliance_rate,
            "dsp_math_resilience": self.dsp_math_resilience,
            "json_repair_success_rate": self.json_repair_success_rate,
            "diagnostic": self.diagnostic,
        }


@dataclass
class Level2MesoScorecard:
    """Meso Pipeline Functional & Security Scorecard (Pipelines A-D Invariants)."""
    pipeline_a_passed: bool = True
    pipeline_b_passed: bool = True
    pipeline_c_passed: bool = True
    pipeline_d_passed: bool = True
    c19_career_firewall_leaks: int = 0
    passed: bool = True
    score: float = 100.0
    diagnostic: str = "All pipeline security and functional invariants satisfied"

    def compute(self) -> None:
        passes = [
            self.pipeline_a_passed,
            self.pipeline_b_passed,
            self.pipeline_c_passed,
            self.pipeline_d_passed,
        ]
        self.score = round((sum(1.0 for p in passes if p) / len(passes)) * 100.0, 2)
        # Non-negotiable veto: 0 career firewall leaks and all pipelines pass
        self.passed = all(passes) and (self.c19_career_firewall_leaks == 0)
        if self.c19_career_firewall_leaks > 0:
            self.diagnostic = f"CRITICAL: Rule C-19 Career Leverage Firewall breached ({self.c19_career_firewall_leaks} leaks)"
        elif not self.passed:
            self.diagnostic = f"Pipeline defense failed (score={self.score}%)"
        else:
            self.diagnostic = "All pipeline security and functional invariants satisfied"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": "Level 2: Meso Pipeline Invariants",
            "score": self.score,
            "passed": self.passed,
            "pipeline_a_passed": self.pipeline_a_passed,
            "pipeline_b_passed": self.pipeline_b_passed,
            "pipeline_c_passed": self.pipeline_c_passed,
            "pipeline_d_passed": self.pipeline_d_passed,
            "c19_career_firewall_leaks": self.c19_career_firewall_leaks,
            "diagnostic": self.diagnostic,
        }


@dataclass
class Level3MacroScorecard:
    """Macro Strategic Alpha Scorecard ($1.2M Executive Exoskeleton Business Case)."""
    dimensions_passed_count: int = 10
    total_dimensions: int = 10
    overall_strategic_score: float = 100.0
    maturity_level: float = 5.0
    passed: bool = True
    verdict: str = "Level 5: Executive Cognitive Exoskeleton ($1.2M Benchmark)"
    diagnostic: str = "Systemic strategic alpha achieved"

    def compute(self, threshold: float = 75.0) -> None:
        self.passed = (self.dimensions_passed_count == self.total_dimensions) and (self.overall_strategic_score >= threshold)
        if not self.passed:
            self.diagnostic = f"Strategic alpha failed ({self.dimensions_passed_count}/{self.total_dimensions} dimensions passed)"
        else:
            self.diagnostic = "Systemic strategic alpha achieved"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": "Level 3: Macro Strategic Alpha",
            "score": self.overall_strategic_score,
            "passed": self.passed,
            "dimensions_passed_count": self.dimensions_passed_count,
            "total_dimensions": self.total_dimensions,
            "maturity_level": self.maturity_level,
            "verdict": self.verdict,
            "diagnostic": self.diagnostic,
        }


@dataclass
class ThreeLevelRollupReport:
    """Consolidated Hierarchical Rollup Report linking Micro -> Meso -> Macro."""
    level_1_micro: Level1MicroScorecard
    level_2_meso: Level2MesoScorecard
    level_3_macro: Level3MacroScorecard
    hierarchical_lock_passed: bool = True
    certification_status: str = "CERTIFIED_LEVEL_5_EXOSKELETON"
    overall_pass: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_pass": self.overall_pass,
            "certification_status": self.certification_status,
            "hierarchical_lock_passed": self.hierarchical_lock_passed,
            "level_1_micro": self.level_1_micro.to_dict(),
            "level_2_meso": self.level_2_meso.to_dict(),
            "level_3_macro": self.level_3_macro.to_dict(),
        }


class ThreeLevelRollupEngine:
    """Aggregates and enforces the 3-level hierarchical evaluation rollup."""

    @staticmethod
    def evaluate_rollup(
        tier_results: Dict[str, Any],
        pipeline_results: Dict[str, Any],
        strategic_results: Dict[str, Any],
    ) -> ThreeLevelRollupReport:
        """Process raw execution tier dictionaries into a strictly gated 3-level rollup."""

        # ── Level 1: Micro Foundation Aggregation ───────────────────────────
        t1 = tier_results.get("tier_1_trajectory", {})
        pipe_a_details = pipeline_results.get("pipeline_a", {}).get("details", {})
        pipe_b_details = pipeline_results.get("pipeline_b", {}).get("details", {})

        traj_eff = float(t1.get("efficiency_ratio", 0.0)) * 100.0
        schema_rate = float(t1.get("schema_compliance_rate", 0.0))
        dsp_score = float(pipe_a_details.get("waveform_eval", {}).get("score", 0.0))
        json_rate = float(pipe_b_details.get("json_repair_eval", {}).get("success_rate", 0.0))

        l1 = Level1MicroScorecard(
            trajectory_efficiency=traj_eff,
            schema_compliance_rate=schema_rate,
            dsp_math_resilience=dsp_score,
            json_repair_success_rate=json_rate,
        )
        l1.compute()

        # ── Level 2: Meso Pipeline Invariants Aggregation ───────────────────
        pipe_a = pipeline_results.get("pipeline_a", {})
        pipe_b = pipeline_results.get("pipeline_b", {})
        pipe_c = pipeline_results.get("pipeline_c", {})
        pipe_d = pipeline_results.get("pipeline_d", {})

        c19_leaks = int(pipe_b.get("c19_career_firewall_leak_count", 0))

        l2 = Level2MesoScorecard(
            pipeline_a_passed=bool(pipe_a.get("passed", False)),
            pipeline_b_passed=bool(pipe_b.get("passed", False)),
            pipeline_c_passed=bool(pipe_c.get("passed", False)),
            pipeline_d_passed=bool(pipe_d.get("passed", False)),
            c19_career_firewall_leaks=c19_leaks,
        )
        l2.compute()

        # ── Level 3: Macro Strategic Alpha Aggregation ──────────────────────
        strat_score = float(strategic_results.get("overall_score", 0.0))
        strat_maturity = float(strategic_results.get("maturity_level", 1.0))
        strat_verdict = strategic_results.get("verdict", "Level 1: Unstructured Secretary")
        dims = strategic_results.get("dimensions", {})
        passed_dims = sum(1 for d in dims.values() if d.get("passed", False))

        l3 = Level3MacroScorecard(
            dimensions_passed_count=passed_dims,
            total_dimensions=max(len(dims), 10),
            overall_strategic_score=strat_score,
            maturity_level=strat_maturity,
            verdict=strat_verdict,
        )
        l3.compute()

        # ── Hierarchical Veto Lock Enforcement ─────────────────────────────
        # Rule: Level 2 cannot certify if Level 1 fails.
        # Rule: Level 3 cannot certify if Level 2 fails.
        if not l1.passed:
            hierarchical_lock = False
            cert_status = "BLOCKED_AT_LEVEL_1_MICRO: Foundational execution or syntax defects present"
            overall_pass = False
        elif not l2.passed:
            hierarchical_lock = False
            cert_status = "BLOCKED_AT_LEVEL_2_MESO: Pipeline functional or security invariants violated"
            overall_pass = False
        elif not l3.passed:
            hierarchical_lock = True
            cert_status = "UTILITY_ONLY: Software engineering passes, but engine fails $1.2M Strategic Exoskeleton business case"
            overall_pass = False
        else:
            hierarchical_lock = True
            cert_status = "CERTIFIED_LEVEL_5_EXOSKELETON: Full end-to-end alignment with $1.2M Technology Executive mandate"
            overall_pass = True

        return ThreeLevelRollupReport(
            level_1_micro=l1,
            level_2_meso=l2,
            level_3_macro=l3,
            hierarchical_lock_passed=hierarchical_lock,
            certification_status=cert_status,
            overall_pass=overall_pass,
        )
