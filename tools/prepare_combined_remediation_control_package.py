#!/usr/bin/env python3
"""Wrap an exact combined-remediation control in a source-bound intake package.

The combined control is a release instruction, not source material by itself.
This helper creates a minimal, contract-valid Markdown control source that
embeds the complete immutable control under a namespaced extension.  It gives
the approval and release lifecycle one exact package to bind without
presenting the remediation instruction as a new business fact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file  # noqa: E402
from dual_pipeline.project_status_reclassification import (  # noqa: E402
    validate_project_status_reclassification_control,
)
from dual_pipeline.source_retirement import (  # noqa: E402
    validate_source_retirement_control,
)
from tools.prepare_markdown_source_intake import prepare_package  # noqa: E402
from tools.validate_dual_pipeline_contracts import ContractError  # noqa: E402

COMBINED_REMEDIATION_CONTROL_KEY = "com.partner_advisory.nee.combined-remediation-control"
_REQUIRED_CONTROL_KEYS = {
    "schema_version",
    "control_type",
    "base_active_pair",
    "source_retirement_execution_order",
    "source_retirement_controls",
    "project_status_reclassification_control",
    "reason",
    "canonical_digest",
}


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"combined remediation {label} must be nonblank text")
    return value.strip()


def _load_control(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        control = json.load(handle)
    if not isinstance(control, dict) or set(control) != _REQUIRED_CONTROL_KEYS:
        raise ValueError("combined remediation control has an invalid shape")
    supplied_digest = _require_text(control["canonical_digest"], label="canonical_digest")
    unsigned = dict(control)
    unsigned.pop("canonical_digest")
    if supplied_digest != digest(unsigned):
        raise ValueError("combined remediation control digest mismatch")
    if control["schema_version"] != "1.0.0":
        raise ValueError("combined remediation control has an unsupported version")
    if control["control_type"] != "combined_sherpa_retirement_and_project_status_demotion":
        raise ValueError("combined remediation control type is unsupported")
    if not isinstance(control["base_active_pair"], dict):
        raise ValueError("combined remediation control has no active-pair binding")
    for field in (
        "runtime_root",
        "generation_id",
        "graph_sha256",
        "sidecar_sha256",
        "embedding_backend",
        "model_id",
        "model_revision",
        "model_manifest_digest",
    ):
        _require_text(control["base_active_pair"].get(field), label=f"base_active_pair.{field}")
    if not isinstance(control["base_active_pair"].get("production_qualified"), bool):
        raise ValueError("combined remediation base active pair lacks qualification state")
    raw_retirements = control["source_retirement_controls"]
    if not isinstance(raw_retirements, list) or not raw_retirements:
        raise ValueError("combined remediation control has no source-retirement controls")
    retirements = [validate_source_retirement_control(item) for item in raw_retirements]
    order = control["source_retirement_execution_order"]
    if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
        raise ValueError("combined remediation execution order must be a source-ID list")
    source_ids = [item["target_source_id"] for item in retirements]
    if order != source_ids or len(source_ids) != len(set(source_ids)):
        raise ValueError("combined remediation execution order does not exactly bind controls")
    validate_project_status_reclassification_control(
        control["project_status_reclassification_control"]
    )
    _require_text(control["reason"], label="reason")
    return control


def _markdown(*, source_id: str, source_date: str, control: dict[str, Any]) -> str:
    baseline = control["base_active_pair"]
    status_control = control["project_status_reclassification_control"]
    return "\n".join(
        (
            "# Sherpa retirement and project-status reclassification control",
            "",
            f"**Source ID:** `{source_id}`",
            f"**Source date:** `{source_date}`",
            "",
            "## Requested remediation",
            "",
            "This is an operator control document, not business or architecture evidence.",
            "It binds one future release to the embedded combined remediation control.",
            f"The control removes {len(control['source_retirement_controls'])} sealed stale Sherpa sources ",
            f"and reclassifies {len(status_control['targets'])} dated project-status sources as citation-only evidence.",
            f"The exact combined-control digest is `{control['canonical_digest']}`.",
            "",
            "## Boundary and release binding",
            "",
            f"The release may run only from generation `{baseline['generation_id']}` ",
            f"with graph SHA-256 `{baseline['graph_sha256']}` and sidecar SHA-256 ",
            f"`{baseline['sidecar_sha256']}`.",
            f"It is bound to `{baseline['embedding_backend']}` using `{baseline['model_id']}` at ",
            f"revision `{baseline['model_revision']}`.",
            "The control adds no timeless facts. Dated EDO and NEA status material remains ",
            "ephemeral citation evidence, with current-status retrieval selecting the newest ",
            "applicable snapshot and history available only on explicit request.",
            "",
        )
    )


def _write_source(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise RuntimeError("combined remediation Markdown path is occupied by different bytes")
        return
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-date", required=True)
    args = parser.parse_args()
    try:
        control_path = args.control.resolve()
        control = _load_control(control_path)
        source_path = args.source.resolve()
        output_path = args.output.resolve()
        source_id = _require_text(args.source_id, label="source_id")
        source_date = _require_text(args.source_date, label="source_date")
        _write_source(
            source_path,
            _markdown(source_id=source_id, source_date=source_date, control=control),
        )
        package = prepare_package(
            source_path,
            output_path,
            source_id=source_id,
            source_date=source_date,
            source_location=str(source_path),
            classification="internal",
            audiences=["internal_consulting", "nee-engineering"],
            source_posture="governed_internal",
            authority_rank=9,
            anchor_entity="Sherpa graph remediation control",
            anchor_stable_identifier="sherpa-graph-remediation-w7",
            anchor_context=(
                "Operator-approved remediation control. It removes only the exact "
                "sealed stale source scope and reclassifies named dated status sources. "
                "It establishes no business or architecture fact."
            ),
            chunk_sections=["Requested remediation", "Boundary and release binding"],
            additional_extensions={
                COMBINED_REMEDIATION_CONTROL_KEY: {
                    "control_file_sha256": sha256_file(control_path),
                    "control_canonical_digest": control["canonical_digest"],
                    "control": control,
                }
            },
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, ContractError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "PASS",
                "source": str(source_path),
                "package": str(output_path),
                "artifact_id": package["artifact_id"],
                "package_canonical_digest": package["canonical_digest"]["value"],
                "control_canonical_digest": control["canonical_digest"],
                "control_file_sha256": sha256_file(control_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
