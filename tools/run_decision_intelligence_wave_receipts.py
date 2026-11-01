#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Build one read-only W0–W5.1 runtime-receipt chain for Decision Intelligence.

The W0–W2 audits intentionally reproduce their documented baseline debt.  A
receipt is therefore complete when that debt is measured against the exact
graph without provider calls, graph mutation, release preparation, or
activation.  It is not a release-readiness approval.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, utc_now
from dual_pipeline.label_quality import audit_graph_labels
from dual_pipeline.release import ReleaseManager
from tools.audit_decision_intelligence_boundaries import audit as audit_w1
from tools.audit_decision_intelligence_semantic_spine import audit as audit_w2
from tools.run_w3_temporal_observation_evaluation import evaluate as evaluate_w3
from tools.run_w4_daily_intelligence_evaluation import evaluate as evaluate_w4
from tools.run_w5_trust_foundation_evaluation import evaluate as evaluate_w5
from tools.run_w5_1_semantic_expansion_evaluation import evaluate as evaluate_w51
from tools.run_w6_strategic_evidence_evaluation import evaluate as evaluate_w6
from tools.run_w7_ai_use_case_qualification import evaluate as evaluate_w7


DEFAULT_RUNTIME = ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729"
DEFAULT_GRAPH = (
    DEFAULT_RUNTIME
    / "generations"
    / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3"
    / "graph.sqlite"
)
DEFAULT_OUTPUT_DIR = ROOT / ".local" / "w5-trust-foundation-20260810" / "wave-receipts"
W0_MANIFEST = ROOT / "governance" / "decision_intelligence" / "W0_BASELINE_MANIFEST.json"
W1_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W1_BOUNDARY_CONTRACT.json"
W2_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W2_SEMANTIC_SPINE_CONTRACT.json"
W3_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W3_TEMPORAL_OBSERVATION_CONTRACT.json"
)
W4_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W4_DAILY_INTELLIGENCE_CONTRACT.json"
)
W5_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W5_TRUST_FOUNDATION_CONTRACT.json"
)
W51_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W5_1_SEMANTIC_EXPANSION_CONTRACT.json"
)
W6_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W6_STRATEGIC_EVIDENCE_CONTRACT.json"
)
W7_CONTRACT = (
    ROOT / "governance" / "decision_intelligence" / "W7_AI_USE_CASE_QUALIFICATION_CONTRACT.json"
)
RECEIPT_RUNNER_COMMAND = (
    ".venv\\Scripts\\python.exe " "tools\\run_decision_intelligence_wave_receipts.py"
)

WAVE_CRITERIA = {
    "W0": (
        "Active pair validates; exact graph hash is bound; contract validation passes; "
        "strict label debt is reproduced as an expected release blocker; no provider, "
        "graph, or release mutation."
    ),
    "W1": (
        "Closed-world boundary policy parses; the exact current graph baseline is measured; "
        "documented FAIL baseline is reproduced; graph is unchanged."
    ),
    "W2": (
        "Semantic-spine policy parses; every current graph predicate and node type is measured "
        "against it; documented FAIL baseline is reproduced; graph is unchanged."
    ),
    "W3": (
        "All temporal-observation cases pass with authenticated audience, immutable source "
        "registry, and valid- plus known-time filtering; graph is unchanged."
    ),
    "W4": (
        "All candidate-only daily-intelligence cases pass with W5 filtering before projection; "
        "graph is unchanged."
    ),
    "W5.0": (
        "All adversarial source, audience, classification, bitemporal, revocation, and "
        "sidecar-retrieval cases pass; graph is unchanged."
    ),
    "W5.1": (
        "The typed agent/version/deployment/tool/evaluation/readiness chain is source-bound, "
        "closed-world, telemetry-free, candidate-only, and graph-unchanged; the exact W5.0 "
        "receipt is valid."
    ),
    "W6": (
        "Strategic evidence packets resolve to immutable W5 source envelopes with exact citation "
        "integrity, counterevidence and falsification gaps remain visible, outputs are "
        "candidate-only, and the graph is unchanged."
    ),
    "W7": (
        "The three priority NEA AI use cases qualify as source-bound candidate answers; missing, "
        "conflicting, unreviewed, or contract-invalid inputs fail closed; graph is unchanged."
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _baseline_id(manifest: dict[str, Any], manifest_path: Path) -> str:
    """Return the declared immutable baseline identity for receipt binding."""

    value = manifest.get("baseline_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"baseline manifest has no nonempty baseline_id: {manifest_path}")
    return value


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["canonical_digest"] = {"algorithm": "sha256", "value": digest(result)}
    return result


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path.resolve()


def _contract_validation() -> dict[str, Any]:
    command = [sys.executable, "tools/validate_dual_pipeline_contracts.py"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed_value = json.loads(completed.stdout)
        if isinstance(parsed_value, dict):
            parsed = parsed_value
    except json.JSONDecodeError:
        pass
    return {
        "command": ".venv\\Scripts\\python.exe tools\\validate_dual_pipeline_contracts.py",
        "return_code": completed.returncode,
        "stdout_digest": digest(completed.stdout),
        "stderr_digest": digest(completed.stderr),
        "summary": parsed,
        "passed": completed.returncode == 0,
    }


def _w0_receipt(graph: Path, runtime_root: Path, manifest_path: Path) -> dict[str, Any]:
    graph_before = sha256_file(graph)
    manifest = _read_json(manifest_path)
    active_pair_path = runtime_root / "active_pair.json"
    active_pair = _read_json(active_pair_path)
    manager = ReleaseManager(runtime_root, graph)
    validation = manager.validate_pair(active_pair)
    strict_audit = audit_graph_labels(graph, strict=True)
    contract_validation = _contract_validation()
    graph_after = sha256_file(graph)
    baseline_id = _baseline_id(manifest, manifest_path)
    expected_label_status = manifest["validation_evidence"]["strict_label_audit"]["status"]
    release_blockers = manifest["w0_readiness_decision"][
        "blocking_conditions_for_decision_intelligence_release"
    ]
    active_pair_matches_graph = (
        active_pair.get("graph_sha256") == graph_before
        and manifest["active_runtime"].get("graph_sha256") == graph_before
    )
    checks = {
        "active_pair_matches_exact_graph": active_pair_matches_graph,
        "runtime_pair_validates": (
            validation.get("status") == "PASS"
            and validation.get("graph_integrity", {}).get("integrity_check") == "ok"
        ),
        "contract_validation_passes": contract_validation["passed"],
        "strict_label_status_reproduced": strict_audit.get("status") == expected_label_status,
        "expected_baseline_has_release_blockers": (
            manifest["w0_readiness_decision"].get("status") == "COMPLETE_WITH_BLOCKERS"
            and int(strict_audit.get("blocking_violation_count", 0)) > 0
        ),
        "graph_unchanged": graph_before == graph_after,
    }
    complete = all(checks.values())
    return _seal(
        {
            "schema_version": "1.0",
            "artifact_type": "decision_intelligence_wave_runtime_receipt",
            "wave": "W0",
            "receipt_id": f"{baseline_id}-w0-runtime-receipt",
            "executed_at": utc_now(),
            "runtime_root": str(runtime_root.resolve()),
            "command": RECEIPT_RUNNER_COMMAND,
            "baseline_manifest_path": str(manifest_path.resolve()),
            "baseline_manifest_digest": digest(manifest),
            "active_pair_path": str(active_pair_path.resolve()),
            "active_pair_digest": digest(active_pair),
            "active_pair": active_pair,
            "runtime_validation": validation,
            "contract_validation": contract_validation,
            "strict_label_audit": strict_audit,
            "checks": checks,
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
            "graph_path": str(graph.resolve()),
            "graph_sha256_before": graph_before,
            "graph_sha256_after": graph_after,
            "graph_unchanged": graph_before == graph_after,
            "receipt_status": "PASS_WITH_EXPECTED_BLOCKERS" if complete else "FAIL",
            "wave_status": "COMPLETE" if complete else "OPEN",
            "release_readiness": "NOT_ESTABLISHED",
            "release_blockers": release_blockers,
        }
    )


def _expected_baseline_receipt(
    *,
    wave: str,
    receipt_id: str,
    graph: Path,
    runtime_root: Path,
    contract_path: Path,
    audit_function: Callable[[Path, Path], dict[str, Any]],
    expected_key: str,
) -> dict[str, Any]:
    graph_before = sha256_file(graph)
    contract = _read_json(contract_path)
    audit = audit_function(graph, contract_path)
    graph_after = sha256_file(graph)
    checks = {
        "policy_parses": bool(contract.get("contract_id")),
        "expected_baseline_status_reproduced": audit.get("status") == audit.get(expected_key),
        "provider_calls_zero": audit.get("provider_calls") == 0,
        "graph_mutations_zero": audit.get("graph_mutations") == 0,
        "release_activation_false": audit.get("release_activation") is False,
        "graph_unchanged": graph_before == graph_after == audit.get("graph_sha256"),
    }
    complete = all(checks.values())
    return _seal(
        {
            "schema_version": "1.0",
            "artifact_type": "decision_intelligence_wave_runtime_receipt",
            "wave": wave,
            "receipt_id": receipt_id,
            "executed_at": utc_now(),
            "runtime_root": str(runtime_root.resolve()),
            "command": RECEIPT_RUNNER_COMMAND,
            "contract_path": str(contract_path.resolve()),
            "contract_digest": digest(contract),
            "audit": audit,
            "checks": checks,
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
            "graph_path": str(graph.resolve()),
            "graph_sha256_before": graph_before,
            "graph_sha256_after": graph_after,
            "graph_unchanged": graph_before == graph_after,
            "receipt_status": "PASS_WITH_EXPECTED_BLOCKERS" if complete else "FAIL",
            "wave_status": "COMPLETE" if complete else "OPEN",
            "release_readiness": "NOT_ESTABLISHED",
            "release_blockers": audit.get("blockers", {}),
        }
    )


def _not_started_receipt(wave: str, reason: str) -> dict[str, Any]:
    return {
        "wave": wave,
        "wave_status": "NOT_STARTED",
        "receipt_status": "NOT_EXECUTED",
        "reason": reason,
    }


def _receipt_entry(wave: str, receipt: dict[str, Any], path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "wave": wave,
        "status": receipt["wave_status"],
        "completion_criteria": WAVE_CRITERIA[wave],
        "runtime_receipt": None,
        "validation_result": {
            "receipt_status": receipt["receipt_status"],
            "provider_calls": receipt.get("provider_calls"),
            "graph_mutations": receipt.get("graph_mutations"),
            "release_activation": receipt.get("release_activation"),
            "graph_unchanged": receipt.get("graph_unchanged"),
        },
        "open_blocker_or_next_authorization": None,
    }
    if path is not None:
        result["runtime_receipt"] = {
            "path": str(path),
            "canonical_digest": receipt.get("canonical_digest", {}).get("value"),
        }
    if receipt.get("release_blockers"):
        result["open_blocker_or_next_authorization"] = {
            "release_blockers": receipt["release_blockers"],
            "next_authorization": "No graph or release action is authorized by this receipt chain.",
        }
    elif receipt["wave_status"] == "COMPLETE":
        result["open_blocker_or_next_authorization"] = (
            "Next work requires explicit authorization and a receipt-gated successor wave."
        )
    else:
        result["open_blocker_or_next_authorization"] = receipt.get("reason", "Validation failed.")
    return result


def run(
    graph: Path,
    runtime_root: Path,
    output_dir: Path,
    *,
    baseline_manifest: Path = W0_MANIFEST,
) -> dict[str, Any]:
    graph = graph.resolve()
    runtime_root = runtime_root.resolve()
    output_dir = output_dir.resolve()
    baseline_manifest = baseline_manifest.resolve()
    baseline = _read_json(baseline_manifest)
    baseline_id = _baseline_id(baseline, baseline_manifest)
    graph_before = sha256_file(graph)
    receipts: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}

    receipts["W0"] = _w0_receipt(graph, runtime_root, baseline_manifest)
    paths["W0"] = _write(output_dir / "w0-receipt.json", receipts["W0"])
    if receipts["W0"]["wave_status"] == "COMPLETE":
        receipts["W1"] = _expected_baseline_receipt(
            wave="W1",
            receipt_id=f"{baseline_id}-w1-boundary-baseline-receipt",
            graph=graph,
            runtime_root=runtime_root,
            contract_path=W1_CONTRACT,
            audit_function=audit_w1,
            expected_key="expected_w1_baseline",
        )
        paths["W1"] = _write(output_dir / "w1-receipt.json", receipts["W1"])
    else:
        receipts["W1"] = _not_started_receipt("W1", "W0 receipt did not complete.")

    if receipts["W1"]["wave_status"] == "COMPLETE":
        receipts["W2"] = _expected_baseline_receipt(
            wave="W2",
            receipt_id=f"{baseline_id}-w2-semantic-spine-baseline-receipt",
            graph=graph,
            runtime_root=runtime_root,
            contract_path=W2_CONTRACT,
            audit_function=audit_w2,
            expected_key="expected_w2_baseline",
        )
        paths["W2"] = _write(output_dir / "w2-receipt.json", receipts["W2"])
    else:
        receipts["W2"] = _not_started_receipt("W2", "W1 receipt did not complete.")

    downstream: tuple[tuple[str, Path, Callable[[Path, Path], dict[str, Any]], str], ...] = (
        ("W3", W3_CONTRACT, evaluate_w3, "w3-receipt.json"),
        ("W4", W4_CONTRACT, evaluate_w4, "w4-receipt.json"),
        ("W5.0", W5_CONTRACT, evaluate_w5, "w5-receipt.json"),
        (
            "W5.1",
            W51_CONTRACT,
            lambda graph_path, contract_path: evaluate_w51(
                graph_path,
                contract_path,
                upstream_w5_receipt=receipts["W5.0"],
            ),
            "w5-1-receipt.json",
        ),
        ("W6", W6_CONTRACT, evaluate_w6, "w6-receipt.json"),
        ("W7", W7_CONTRACT, evaluate_w7, "w7-receipt.json"),
    )
    previous_wave = "W2"
    for wave, contract_path, evaluator, filename in downstream:
        if receipts[previous_wave]["wave_status"] == "COMPLETE":
            receipt = evaluator(graph, contract_path)
            receipt["wave"] = wave
            receipt["wave_status"] = "COMPLETE" if receipt.get("status") == "PASS" else "OPEN"
            receipt["receipt_status"] = receipt.pop("status")
            receipt["release_readiness"] = "NOT_ESTABLISHED"
            receipt["release_blockers"] = [
                "Candidate-only control evidence does not authorize graph mutation, "
                "release activation, or client delivery."
            ]
            receipt_without_digest = {
                key: value for key, value in receipt.items() if key != "canonical_digest"
            }
            receipt = _seal(receipt_without_digest)
            receipts[wave] = receipt
            paths[wave] = _write(output_dir / filename, receipt)
        else:
            receipts[wave] = _not_started_receipt(
                wave, f"{previous_wave} receipt did not complete."
            )
        previous_wave = wave

    graph_after = sha256_file(graph)
    ordered_waves = ("W0", "W1", "W2", "W3", "W4", "W5.0", "W5.1", "W6", "W7")
    ledger = _seal(
        {
            "schema_version": "1.0",
            "artifact_type": "decision_intelligence_wave_receipt_ledger",
            "ledger_id": f"{baseline_id}-w0-w7-receipt-chain",
            "executed_at": utc_now(),
            "runtime_root": str(runtime_root),
            "command": RECEIPT_RUNNER_COMMAND,
            "baseline_manifest_path": str(baseline_manifest),
            "baseline_manifest_digest": digest(baseline),
            "baseline_id": baseline_id,
            "graph_path": str(graph),
            "graph_sha256_before": graph_before,
            "graph_sha256_after": graph_after,
            "graph_unchanged": graph_before == graph_after,
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
            "release_readiness": "NOT_ESTABLISHED",
            "waves": [
                _receipt_entry(wave, receipts[wave], paths.get(wave)) for wave in ordered_waves
            ],
            "status": "PASS"
            if graph_before == graph_after
            and all(receipts[wave]["wave_status"] == "COMPLETE" for wave in ordered_waves)
            else "FAIL",
        }
    )
    ledger_path = _write(output_dir / "wave-receipt-ledger.json", ledger)
    return {
        "ledger": ledger,
        "ledger_path": ledger_path,
        "receipts": receipts,
        "paths": paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--baseline-manifest",
        type=Path,
        default=W0_MANIFEST,
        help="Exact W0 baseline manifest to bind into the W0-W7 receipt chain.",
    )
    args = parser.parse_args()
    result = run(
        args.graph,
        args.runtime_root,
        args.output_dir,
        baseline_manifest=args.baseline_manifest,
    )
    ledger = result["ledger"]
    print(
        json.dumps(
            {
                "status": ledger["status"],
                "ledger": str(result["ledger_path"]),
                "graph_unchanged": ledger["graph_unchanged"],
                "completed_waves": [
                    item["wave"]
                    for item in ledger["waves"]
                    if item["status"] == "COMPLETE"
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if ledger["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
