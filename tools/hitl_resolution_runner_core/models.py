"""Data models for HITL resolution wave execution and receipt verification."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WaveSpec:
    wave: int
    title: str
    plan_path: str
    test_files: List[str]
    e2e_files: List[str]
    adversarial_files: List[str]
    per_test_timeout_seconds: int = 60
    whole_run_timeout_seconds: int = 120
    required_min_pass_count: int = 1
    parent_wave: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WaveSpec:
        return cls(
            wave=int(data["wave"]),
            title=str(data.get("title", "")),
            plan_path=str(data.get("plan_path", "")),
            test_files=list(data.get("test_files", [])),
            e2e_files=list(data.get("e2e_files", [])),
            adversarial_files=list(data.get("adversarial_files", [])),
            per_test_timeout_seconds=int(data.get("per_test_timeout_seconds", 60)),
            whole_run_timeout_seconds=int(data.get("whole_run_timeout_seconds", 120)),
            required_min_pass_count=int(data.get("required_min_pass_count", 1)),
            parent_wave=int(data["parent_wave"]) if data.get("parent_wave") is not None else None,
        )


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    total_collected: int
    passed_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    xfail_count: int
    xpass_count: int
    test_ids: List[str]
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass
class WaveReceipt:
    schema_version: str
    wave: int
    title: str
    run_id: str
    plan_digest: str
    spec_digest: str
    code_tree_digest: str
    runtime_root: str
    receipt_root: str
    parent_receipt_digest: Optional[str]
    command: str
    started_at: str
    completed_at: str
    terminal_result: str  # "COMPLETE" or "FAILED"
    exit_code: int
    test_counts: Dict[str, int]
    test_ids: List[str]
    ci_profile_resolution: Dict[str, Any]
    output_digests: Dict[str, str] = field(default_factory=dict)
    static_gate_results: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
