"""Models for production preflight."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core import ROOT


@dataclass(frozen=True)
class PreflightConfig:
    runtime_root: Path
    graph: Path
    model_path: Path
    model_revision: str
    model_manifest: Path
    report: Path
    repo_root: Path = ROOT
    strict_production: bool = False
    auth_token: Path | None = None
    auth_public_key: Path | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    copilot_package: Path | None = None
    architecture_package: Path | None = None
    pdlc_question_file: Path | None = None
    approval_id: str | None = None
    copilot_decisions: Path | None = None
    architecture_decisions: Path | None = None
