"""Runner for strategic waves shadow gate execution and chained receipt generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    ROOT,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)

from .gates import GATES
from .specs import PROGRAM_ID, WAVES, _file_evidence


def run_strategic_waves(
    *,
    output_root: Path,
    graph_path: Path,
    active_pair_path: Path | None = None,
    root: Path = ROOT,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Execute all shadow gates and write one immutable chained receipt per wave."""
    root = root.resolve()
    graph_path = graph_path.resolve()
    output_root = output_root.resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(graph_path)
    timestamp = completed_at or utc_now()
    graph_before = sha256_file(graph_path)
    state: dict[str, Any] = {
        "graph_path": graph_path,
        "graph_sha256_before": graph_before,
        "active_pair_path": (
            active_pair_path.resolve() if active_pair_path is not None else None
        ),
        "active_pair_sha256_before": (
            sha256_file(active_pair_path)
            if active_pair_path is not None and active_pair_path.is_file()
            else None
        ),
    }
    receipts: list[dict[str, Any]] = []
    prior_digest: str | None = None
    for spec in WAVES:
        evidence = _file_evidence(root, spec.evidence_paths)
        status = "PASS"
        try:
            detail = GATES[spec.number](root, output_root, state)
        except Exception as exc:
            status = "FAIL"
            detail = f"{type(exc).__name__}: {exc}"
        generated_path = state.get("generated_evidence", {}).get(spec.number)
        if isinstance(generated_path, Path):
            evidence.append(
                {
                    "name": generated_path.name,
                    "kind": "analysis" if spec.number >= 6 else "artifact",
                    "reference": str(generated_path),
                    "sha256": sha256_file(generated_path),
                }
            )
        graph_after = sha256_file(graph_path)
        if graph_after != graph_before:
            status = "FAIL"
            detail = f"{detail} Graph hash changed during wave."
        gate_id = stable_id(
            "strategic-wave-gate",
            spec.number,
            spec.name,
            length=24,
        )
        gates = [{"gate_id": gate_id, "status": status, "detail": detail}]
        receipt_id = stable_id(
            "strategic-wave-receipt",
            PROGRAM_ID,
            spec.number,
            prior_digest,
            evidence,
            gates,
            length=28,
        )
        receipt = create_artifact(
            "strategic-wave-receipt",
            receipt_id,
            receipt_id=receipt_id,
            program_id=PROGRAM_ID,
            wave_number=spec.number,
            wave_name=spec.name,
            status=status,
            prior_receipt_digest=prior_digest,
            evidence=evidence,
            gates=gates,
            graph_sha256_before=graph_before,
            graph_sha256_after=graph_after,
            provider_call_count=0,
            model_call_count=0,
            graph_mutation_count=0,
            index_mutation_count=0,
            active_pair_mutation_count=0,
            activation_state=(
                "READY_NOT_ACTIVATED" if spec.number == 8 else "NOT_APPLICABLE"
            ),
            completed_at=timestamp,
            extensions={},
        )
        receipt_path = output_root / "receipts" / f"wave-{spec.number:02d}.json"
        write_json_exclusive(receipt_path, receipt)
        receipts.append(receipt)
        prior_digest = receipt["canonical_digest"]["value"]
        if status != "PASS":
            break
    summary = {
        "program_id": PROGRAM_ID,
        "status": (
            "PASS"
            if len(receipts) == len(WAVES)
            and all(receipt["status"] == "PASS" for receipt in receipts)
            else "FAIL"
        ),
        "receipt_count": len(receipts),
        "receipt_chain": [
            {
                "wave_number": receipt["wave_number"],
                "receipt_id": receipt["receipt_id"],
                "receipt_digest": receipt["canonical_digest"]["value"],
                "prior_receipt_digest": receipt["prior_receipt_digest"],
            }
            for receipt in receipts
        ],
        "graph_sha256_before": graph_before,
        "graph_sha256_after": sha256_file(graph_path),
        "graph_mutation_count": 0,
        "index_mutation_count": 0,
        "active_pair_mutation_count": 0,
        "activation_state": (
            "READY_NOT_ACTIVATED"
            if len(receipts) == len(WAVES)
            else "NOT_APPLICABLE"
        ),
        "completed_at": timestamp,
    }
    write_json_exclusive(output_root / "strategic-wave-summary.json", summary)
    return summary
