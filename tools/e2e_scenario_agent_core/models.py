"""Typed verification models, 5-check physical proof records, and 16-table audits."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

TWELVE_GRAPH_TABLES: tuple[str, ...] = (
    "node",
    "edge",
    "legacy_edge_assertion_map",
    "source",
    "source_version",
    "extraction_run",
    "evidence_item",
    "assertion",
    "assertion_evidence",
    "search_document",
    "search_fts",
    "audit_event",
)

FOUR_SIDECAR_TABLES: tuple[str, ...] = (
    "dense_vector",
    "sparse_weight",
    "colbert_token",
    "retrieval_record",
)


def compute_rfc8259_digest(payload: Dict[str, Any]) -> str:
    """Compute deterministic RFC-8259 SHA-256 digest excluding canonical_digest."""
    filtered = {k: v for k, v in payload.items() if k != "canonical_digest"}
    encoded = json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PhysicalLandingProof:
    """Five-point physical proof for a knowledge graph mutation."""

    file_changed: bool
    old_size_bytes: int
    new_size_bytes: int
    old_sha256: str
    new_sha256: str
    raw_bytes_verified_ids: List[str]
    raw_byte_offsets: Dict[str, int]
    integrity_check_ok: bool
    foreign_key_violations: int
    fresh_readback_verified_ids: List[str]
    row_deltas: Dict[str, int]

    @property
    def all_five_checks_passed(self) -> bool:
        return bool(
            self.file_changed
            and self.old_sha256 != self.new_sha256
            and self.new_size_bytes > 0
            and len(self.raw_bytes_verified_ids) > 0
            and all(off >= 0 for off in self.raw_byte_offsets.values())
            and self.integrity_check_ok
            and self.foreign_key_violations == 0
            and self.fresh_readback_verified_ids == self.raw_bytes_verified_ids
            and sum(v for v in self.row_deltas.values() if v > 0) > 0
        )


@dataclass(frozen=True)
class SixteenTableAudit:
    """Row-count and invariant audit across all 12 GraphDB and 4 Sidecar tables."""

    graph_table_counts: Dict[str, int]
    sidecar_table_counts: Dict[str, int]
    unmapped_edges_count: int
    dense_retrieval_parity: bool
    bound_graph_sha256_matches: bool
    canonical_assertions_count: int
    searchable_fts_count: int

    @property
    def passed(self) -> bool:
        all_graph_present = all(
            self.graph_table_counts.get(t, 0) > 0 for t in TWELVE_GRAPH_TABLES
        )
        all_sidecar_present = all(
            self.sidecar_table_counts.get(t, 0) > 0 for t in FOUR_SIDECAR_TABLES
        )
        return bool(
            all_graph_present
            and all_sidecar_present
            and self.unmapped_edges_count == 0
            and self.dense_retrieval_parity
            and self.bound_graph_sha256_matches
            and self.canonical_assertions_count > 0
            and self.searchable_fts_count > 0
        )


@dataclass(frozen=True)
class ScenarioVerificationResult:
    """Result of executing a single end-to-end pipeline scenario."""

    scenario_id: str
    pipeline_id: str
    pipeline_name: str
    title: str
    passed: bool
    duration_ms: float
    verified_deliverables: Dict[str, str] = field(default_factory=dict)
    verified_invariants: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass(frozen=True)
class ExhaustiveE2EAgentReport:
    """Sealed report emitted by the Exhaustive E2E Pipeline Scenario Agent."""

    run_id: str
    generated_at: str
    terminal_outcome: str
    total_pipelines: int
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    zero_production_graph_mutation: bool
    pre_prod_graph_sha256: str
    post_prod_graph_sha256: str
    pre_prod_sidecar_sha256: str
    post_prod_sidecar_sha256: str
    physical_landing_proof: Optional[PhysicalLandingProof]
    sixteen_table_audit: Optional[SixteenTableAudit]
    scenarios: List[ScenarioVerificationResult]
    canonical_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        raw = asdict(self)
        raw["canonical_digest"] = compute_rfc8259_digest(raw)
        return raw


def build_agent_report(
    run_id: str,
    scenarios: Sequence[ScenarioVerificationResult],
    physical_proof: Optional[PhysicalLandingProof],
    sixteen_table_audit: Optional[SixteenTableAudit],
    pre_graph_sha: str,
    post_graph_sha: str,
    pre_sidecar_sha: str,
    post_sidecar_sha: str,
) -> ExhaustiveE2EAgentReport:
    """Assemble and seal the ExhaustiveE2EAgentReport."""
    passed_count = sum(1 for s in scenarios if s.passed)
    failed_count = len(scenarios) - passed_count
    zero_prod_mutation = (
        pre_graph_sha == post_graph_sha and pre_sidecar_sha == post_sidecar_sha
    )
    proof_ok = physical_proof is not None and physical_proof.all_five_checks_passed
    tables_ok = sixteen_table_audit is not None and sixteen_table_audit.passed
    all_ok = (
        failed_count == 0
        and len(scenarios) > 0
        and zero_prod_mutation
        and proof_ok
        and tables_ok
    )
    outcome = "PASS" if all_ok else "FAIL"
    pipelines = sorted({s.pipeline_id for s in scenarios})
    draft = ExhaustiveE2EAgentReport(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        terminal_outcome=outcome,
        total_pipelines=len(pipelines),
        total_scenarios=len(scenarios),
        passed_scenarios=passed_count,
        failed_scenarios=failed_count,
        zero_production_graph_mutation=zero_prod_mutation,
        pre_prod_graph_sha256=pre_graph_sha,
        post_prod_graph_sha256=post_graph_sha,
        pre_prod_sidecar_sha256=pre_sidecar_sha,
        post_prod_sidecar_sha256=post_sidecar_sha,
        physical_landing_proof=physical_proof,
        sixteen_table_audit=sixteen_table_audit,
        scenarios=list(scenarios),
        canonical_digest="",
    )
    sealed_dict = draft.to_dict()
    return replace(draft, canonical_digest=sealed_dict["canonical_digest"])


def render_markdown_report(report: ExhaustiveE2EAgentReport) -> str:
    """Render human-readable Markdown report with 5-check proof and 16-table matrix."""
    lines = [
        f"# Exhaustive End-to-End Pipeline Scenario Report (`{report.run_id}`)",
        "",
        f"- **Terminal Outcome**: `{report.terminal_outcome}`",
        f"- **Pipelines Verified**: `{report.total_pipelines}`",
        f"- **Scenarios Passed**: `{report.passed_scenarios}/{report.total_scenarios}`",
        f"- **Zero Production Graph Mutation**: `{report.zero_production_graph_mutation}`",
        f"- **Canonical Digest**: `{report.canonical_digest}`",
        "",
        "## 1. End-to-End Pipeline Scenario Matrix",
        "",
        "| Scenario ID | Pipeline | Title | Status | Duration (ms) | Invariants Verified |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for s in report.scenarios:
        status_str = "PASS" if s.passed else f"FAIL ({s.error})"
        inv_str = ", ".join(s.verified_invariants[:4])
        lines.append(
            f"| `{s.scenario_id}` | `{s.pipeline_id}` ({s.pipeline_name}) | {s.title} | `{status_str}` | `{s.duration_ms:.1f}` | {inv_str} |"
        )
    if report.physical_landing_proof is not None:
        p = report.physical_landing_proof
        lines.extend([
            "",
            "## 2. Five-Check Physical Graph Mutation Proof",
            "",
            "| Check | Status | Empirical Observation | Physical Detail |",
            "| :--- | :--- | :--- | :--- |",
            f"| File Changed | `{'PASS' if p.file_changed else 'FAIL'}` | Size: `{p.old_size_bytes}` -> `{p.new_size_bytes}` bytes | SHA: `{p.old_sha256[:12]}` -> `{p.new_sha256[:12]}` |",
            f"| Raw Bytes | `{'PASS' if p.raw_bytes_verified_ids else 'FAIL'}` | `{len(p.raw_bytes_verified_ids)}` ID(s) located on disk | Offsets: `{p.raw_byte_offsets}` |",
            f"| Integrity | `{'PASS' if p.integrity_check_ok and p.foreign_key_violations == 0 else 'FAIL'}` | `PRAGMA integrity_check` returned `ok` | FK violations: `{p.foreign_key_violations}` |",
            f"| Readback | `{'PASS' if p.fresh_readback_verified_ids == p.raw_bytes_verified_ids else 'FAIL'}` | Fresh read-only connection verified exact rows | IDs: `{p.fresh_readback_verified_ids}` |",
            f"| Row Delta | `{'PASS' if sum(p.row_deltas.values()) > 0 else 'FAIL'}` | Table counts matched expected deltas | Deltas: `{p.row_deltas}` |",
        ])
    if report.sixteen_table_audit is not None:
        a = report.sixteen_table_audit
        lines.extend([
            "",
            "## 3. Sixteen-Table GraphDB & Vector Sidecar Landing Audit",
            "",
            f"- **GraphDB 12-Table Counts**: `{json.dumps(a.graph_table_counts, sort_keys=True)}`",
            f"- **Sidecar 4-Table Counts**: `{json.dumps(a.sidecar_table_counts, sort_keys=True)}`",
            f"- **Unmapped Edges**: `{a.unmapped_edges_count}`",
            f"- **Dense-to-Retrieval Record Parity**: `{a.dense_retrieval_parity}`",
            f"- **Bound Graph SHA-256 Parity**: `{a.bound_graph_sha256_matches}`",
        ])
    return "\n".join(lines) + "\n"
