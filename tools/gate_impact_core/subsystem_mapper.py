"""
Subsystem impact classification and qualification gate recommendation mapper.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Dict, List


class GateLayer(IntEnum):
    DOCS = 1
    CLIENT_DELIVERABLES = 2
    DEVELOPER_TOOLS = 3
    CORE_ENGINE = 4


@dataclass
class GateProfileRecommendation:
    layer: GateLayer
    profile_name: str
    recommended_gates: List[str] = field(default_factory=list)
    estimated_runtime_seconds: float = 0.0
    bypasses_bge_m3: bool = False
    bypasses_pytorch: bool = False
    rationale: str = ""
    file_count: int = 0
    layer_breakdown: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["layer"] = self.layer.name
        return d


def classify_file_impact(path: str) -> GateLayer:
    p = path.strip().replace("\\", "/")

    # Layer 2: Client deliverables under output/ (takes precedence over generic .md)
    if p.startswith("output/"):
        return GateLayer.CLIENT_DELIVERABLES

    # Layer 1: Pure documentation and markdown
    if (
        p.startswith("docs/")
        or p.endswith(".md")
        or p.startswith(".gemini/")
        or p.startswith(".vscode/")
    ):
        return GateLayer.DOCS

    # Layer 3: Developer tools, scripts, and local PR governance
    if (
        p.startswith("tools/")
        or p.startswith("scripts/")
        or p.startswith(".githooks/")
        or (p.startswith("tests/") and not p.startswith("tests/end_to_end/"))
    ):
        return GateLayer.DEVELOPER_TOOLS

    # Layer 4: Core Engine, pipelines, storage, ontology, schemas, config
    return GateLayer.CORE_ENGINE


def classify_diff_impact(files: List[str]) -> GateProfileRecommendation:
    if not files:
        return GateProfileRecommendation(
            layer=GateLayer.DOCS,
            profile_name="docs",
            recommended_gates=["Gate 1: Fast Linter", "Gate 2: Hygiene"],
            estimated_runtime_seconds=2.0,
            bypasses_bge_m3=True,
            bypasses_pytorch=True,
            rationale="Empty diff: no qualification gates required.",
            file_count=0,
        )

    layer_counts: Dict[GateLayer, int] = {layer: 0 for layer in GateLayer}
    for f in files:
        file_layer = classify_file_impact(f)
        layer_counts[file_layer] += 1

    max_layer = max(fl for fl, count in layer_counts.items() if count > 0)
    breakdown = {layer.name: count for layer, count in layer_counts.items() if count > 0}

    if max_layer == GateLayer.DOCS:
        return GateProfileRecommendation(
            layer=GateLayer.DOCS,
            profile_name="docs",
            recommended_gates=[
                "Gate 1: Markdown Syntax & Links",
                "Gate 2: Document Line Limits & Hierarchy",
                "Gate 3: Output Word Count Caps",
            ],
            estimated_runtime_seconds=5.0,
            bypasses_bge_m3=True,
            bypasses_pytorch=True,
            rationale="Documentation-only changes: bypasses all Python test runners, PyTorch, and BGE-M3 evals.",
            file_count=len(files),
            layer_breakdown=breakdown,
        )

    if max_layer == GateLayer.CLIENT_DELIVERABLES:
        return GateProfileRecommendation(
            layer=GateLayer.CLIENT_DELIVERABLES,
            profile_name="data_highway",
            recommended_gates=[
                "Gate 1: Output Directory Hierarchy Compliance",
                "Gate 2: 01 HITL Resolved Transcript Schema",
                "Gate 3: 02 Executive Recap Structure",
                "Gate 4: 03 Canonical Signal Projection Schema",
                "Gate 5: Ed25519 ALR Cryptographic Verification",
                "Gate 6: RFC 8785 Canonical JSON Validation",
            ],
            estimated_runtime_seconds=20.0,
            bypasses_bge_m3=True,
            bypasses_pytorch=True,
            rationale="Client deliverables only: fast-tracked via Data Highway (Gates 1-6). Zero engine risk.",
            file_count=len(files),
            layer_breakdown=breakdown,
        )

    if max_layer == GateLayer.DEVELOPER_TOOLS:
        return GateProfileRecommendation(
            layer=GateLayer.DEVELOPER_TOOLS,
            profile_name="tooling",
            recommended_gates=[
                "Gate 1: AST Code Compilation",
                "Gate 2: Ruff Linter & Formatter",
                "Gate 3: Type Checking & Imports",
                "Gate 4: CLI Unit Test Suite",
                "Gate 5: Tool Argument Validation",
            ],
            estimated_runtime_seconds=45.0,
            bypasses_bge_m3=True,
            bypasses_pytorch=True,
            rationale="Developer tooling and tests: qualifies CLI functionality without BGE-M3 vector evals.",
            file_count=len(files),
            layer_breakdown=breakdown,
        )

    # Core Engine
    return GateProfileRecommendation(
        layer=GateLayer.CORE_ENGINE,
        profile_name="full",
        recommended_gates=[
            "All 29 Local Main Readiness Gates",
            "Gate 10: Testing Gaps Audit (Level 5)",
            "Gate 17: BGE-M3 11-Gate Retrieval Evaluation",
            "Gate 21: Ed25519 Signature & ALR Receipts",
            "Gate 28: Dry-run Merge & AST Conflict Check",
            "Gate 29: End-to-End Test Suite Execution",
        ],
        estimated_runtime_seconds=450.0,
        bypasses_bge_m3=False,
        bypasses_pytorch=False,
        rationale="Core engine, pipeline, or ontology changes detected: requires full 29-gate qualification.",
        file_count=len(files),
        layer_breakdown=breakdown,
    )
