"""Specifications, constants, and evidence helpers for strategic wave verification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    digest,
    read_json,
    sha256_file,
    write_json_exclusive,
)
from dual_pipeline.strategic_reasoning import build_strategic_channels

PROGRAM_ID = "strategic-reasoning-refactor-v1"


@dataclass(frozen=True)
class WaveSpec:
    number: int
    name: str
    evidence_paths: tuple[str, ...]


WAVES = (
    WaveSpec(
        1,
        "Semantic foundation",
        (
            "contracts/dual_pipeline/schemas/strategic-assertion-profile.schema.json",
            "dual_pipeline/strategic_reasoning.py",
        ),
    ),
    WaveSpec(
        2,
        "Source-bound ingestion projection",
        (
            "dual_pipeline/strategic_ingestion.py",
            "tests/fixtures/sherpa_official_business_case_intake.json",
        ),
    ),
    WaveSpec(
        3,
        "Role-aware retrieval policy",
        (
            "contracts/dual_pipeline/schemas/retrieval-record.schema.json",
            "dual_pipeline/index.py",
        ),
    ),
    WaveSpec(
        4,
        "Role-separated evidence portfolio",
        (
            "contracts/dual_pipeline/schemas/strategic-evidence-packet.schema.json",
            "tests/fixtures/sherpa_strategic_evidence.json",
        ),
    ),
    WaveSpec(
        5,
        "Strategic reasoning decision trace",
        (
            "contracts/dual_pipeline/schemas/strategic-reasoning-output.schema.json",
            "dual_pipeline/l2_output_contract.py",
        ),
    ),
    WaveSpec(
        6,
        "Strategic safety evaluation",
        (
            "tests/test_strategic_reasoning.py",
            "tests/test_strategic_waves.py",
        ),
    ),
    WaveSpec(
        7,
        "Existing-graph migration analysis",
        (
            "dual_pipeline/strategic_waves.py",
            "graph/nee_graph.sqlite",
        ),
    ),
    WaveSpec(
        8,
        "Release readiness without activation",
        (
            "contracts/dual_pipeline/schemas/strategic-wave-receipt.schema.json",
            "PROJECT_INSTRUCTIONS.md",
        ),
    ),
)


def _fixture_packet(root: Path) -> dict[str, Any]:
    packet = read_json(root / "tests" / "fixtures" / "sherpa_strategic_evidence.json")
    packet["retrieval_mode"] = "strategic"
    packet["allowed_lanes"] = ["approved", "candidate", "external_public"]
    packet["strategic_context"] = build_strategic_channels(packet)
    packet["packet_digest"] = digest(packet)
    return packet


def _write_generated(
    output_root: Path,
    name: str,
    value: dict[str, Any],
) -> Path:
    path = output_root / "evidence" / name
    write_json_exclusive(path, value)
    return path


def _file_evidence(root: Path, paths: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.append(
            {
                "name": path.name,
                "kind": (
                    "contract"
                    if "contracts/dual_pipeline/schemas" in relative
                    else "fixture"
                    if "fixtures" in relative
                    else "test"
                    if relative.startswith("tests/")
                    else "artifact"
                ),
                "reference": relative.replace("\\", "/"),
                "sha256": sha256_file(path),
            }
        )
    return rows
