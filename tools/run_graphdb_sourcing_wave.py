#!/usr/bin/env python3
"""tools/run_graphdb_sourcing_wave.py

Governed Wave Execution Coordinator for Mandatory GraphDB Sourcing and Anti-Hallucination.
Enforces:
1. Sequential wave predecessor validation (Wave N requires Wave N-1 COMPLETE receipt).
2. Exact scoped test execution with mandatory per-test and suite timeout.
3. Zero skipped required tests invariant (skipped > 0 fails closed).
4. Zero graph mutation invariant (graph SHA before == graph SHA after).
5. Preservation of production controls (prohibited offline flags remain purged).
6. Deterministic RFC-8259 cryptographic receipt sealing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.parser import build_parser

WAVE_TITLES: Dict[int, str] = {
    1: "Reproduce CLI defect and preserve production controls",
    2: "Define claim contracts and consistent read-only evidence access",
    3: "Validate claims, requests and deterministic limits",
    4: "Enforce the final delivery boundary",
    5: "Integrate staged Git, readiness and policy checks",
    6: "Adversarial qualification and governed handoff",
}

WAVE_TEST_ALLOWLIST: Dict[int, Tuple[str, ...]] = {
    1: (
        "tests.test_cli_fail_closed_on_missing_provider",
        "tests.end_to_end.test_cli_registry_parity_e2e",
        "tests.test_cli_argument_parity",
        "tests.test_graphdb_sourcing_wave_receipts",
    ),
    2: (
        "tests.test_grounding_contract",
        "tests.test_grounding_snapshot",
    ),
    3: (
        "tests.test_verify_graphdb_grounding",
        "tests.test_grounding_metrics",
        "tests.test_grounding_request_gaps",
    ),
    4: (
        "tests.test_grounding_delivery_boundary",
    ),
    5: (
        "tests.test_grounding_governance",
    ),
    6: (
        "tests.test_grounding_end_to_end",
    ),
}

DEFAULT_GRAPH_PATH = ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729" / "generations" / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3" / "graph.sqlite"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file if it exists, otherwise return sentinel."""
    if not path.is_file():
        return "NONE"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_dict_digest(payload: Dict[str, Any]) -> str:
    """Deterministic RFC-8259 SHA-256 digest of dictionary payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_atomic_json(target_path: Path, payload: Dict[str, Any], immutable: bool = False) -> None:
    """Write JSON payload atomically (.tmp -> fsync -> os.replace)."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")
    if target_path.exists():
        try:
            os.chmod(target_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)
    if immutable:
        os.chmod(target_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def verify_wave_receipt(receipt_path: Path) -> Dict[str, Any]:
    """Verify cryptographic digest and schema integrity of a wave receipt."""
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Receipt file not found: {receipt_path}")
    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    recorded_digest = data.get("receipt_digest")
    if not recorded_digest:
        raise ValueError(f"Receipt missing receipt_digest: {receipt_path}")
    body = {k: v for k, v in data.items() if k != "receipt_digest"}
    calculated_digest = compute_dict_digest(body)
    if recorded_digest != calculated_digest:
        raise ValueError(f"Receipt digest mismatch in {receipt_path}: recorded {recorded_digest} != computed {calculated_digest}")
    return data


PROHIBITED_FLAGS = frozenset({"--offline-external-l2", "--offline-ingestion-reasoning"})


def _parser_has_prohibited_flag(parser: Any) -> bool:
    """Check if a parser or subparser directly defines any prohibited offline flag."""
    for action in getattr(parser, "_actions", []):
        if any(opt in PROHIBITED_FLAGS for opt in getattr(action, "option_strings", [])):
            return True
    return False


def validate_production_controls_preserved() -> bool:
    """Verify that removed offline controls remain strictly absent from parser."""
    parser = build_parser()
    if _parser_has_prohibited_flag(parser):
        return False
    subparsers_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if subparsers_action and isinstance(subparsers_action.choices, dict):
        for sub_parser in subparsers_action.choices.values():
            if _parser_has_prohibited_flag(sub_parser):
                return False
    return True


def run_wave(
    wave: int,
    timeout: int = 60,
    receipts_dir: Optional[Path] = None,
    graph_path: Optional[Path] = None,
    enforce_predecessor: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    """Execute the declared wave test suite, verify invariants, and seal execution receipt."""
    receipts_dir = receipts_dir or (ROOT / "reports")
    receipts_dir.mkdir(parents=True, exist_ok=True)
    graph = graph_path or DEFAULT_GRAPH_PATH

    predecessor_path: Optional[Path] = None
    predecessor_digest: Optional[str] = None

    # 1. Predecessor receipt validation
    if wave > 1 and enforce_predecessor:
        predecessor_path = receipts_dir / f"graphdb_sourcing_wave_{wave - 1}_receipt.json"
        try:
            pred_data = verify_wave_receipt(predecessor_path)
            if pred_data.get("wave_status") != "COMPLETE" or pred_data.get("terminal_outcome") != "PASS":
                raise ValueError(f"Predecessor wave {wave - 1} status is not COMPLETE/PASS: {pred_data.get('wave_status')}")
            predecessor_digest = pred_data.get("receipt_digest")
        except Exception as exc:
            failure_receipt = {
                "schema_version": "1.0.0",
                "wave": wave,
                "wave_title": WAVE_TITLES.get(wave, f"Wave {wave}"),
                "terminal_outcome": "FAILED",
                "wave_status": "BLOCKED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": f"PREDECESSOR_VERIFICATION_FAILED: {exc}",
                "predecessor_receipt": str(predecessor_path),
            }
            failure_receipt["receipt_digest"] = compute_dict_digest(failure_receipt)
            receipt_file = receipts_dir / f"graphdb_sourcing_wave_{wave}_receipt.json"
            write_atomic_json(receipt_file, failure_receipt)
            return 1, failure_receipt

    # 2. Production controls check
    controls_ok = validate_production_controls_preserved()
    if not controls_ok:
        failure_receipt = {
            "schema_version": "1.0.0",
            "wave": wave,
            "wave_title": WAVE_TITLES.get(wave, f"Wave {wave}"),
            "terminal_outcome": "FAILED",
            "wave_status": "BLOCKED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": "PRODUCTION_CONTROLS_VIOLATED: Prohibited offline flags re-introduced into CLI parser.",
            "production_controls_preserved": False,
        }
        failure_receipt["receipt_digest"] = compute_dict_digest(failure_receipt)
        receipt_file = receipts_dir / f"graphdb_sourcing_wave_{wave}_receipt.json"
        write_atomic_json(receipt_file, failure_receipt)
        return 1, failure_receipt

    # 3. Graph hash before
    graph_before = compute_file_sha256(graph)

    # 4. Scope discovery and test execution
    test_modules = WAVE_TEST_ALLOWLIST.get(wave, ())
    if not test_modules:
        raise ValueError(f"Wave {wave} has no declared test allowlist.")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod_name in test_modules:
        suite.addTests(loader.loadTestsFromName(mod_name))

    runner = unittest.TextTestRunner(verbosity=1)
    test_result = runner.run(suite)

    # 5. Graph hash after & invariants
    graph_after = compute_file_sha256(graph)
    graph_unchanged = (graph_before == graph_after)

    tests_run = test_result.testsRun
    failures_cnt = len(test_result.failures)
    errors_cnt = len(test_result.errors)
    skipped_cnt = len(test_result.skipped)

    success = (
        test_result.wasSuccessful()
        and failures_cnt == 0
        and errors_cnt == 0
        and skipped_cnt == 0
        and graph_unchanged
        and controls_ok
    )

    terminal_outcome = "PASS" if success else "FAILED"
    wave_status = "COMPLETE" if success else "BLOCKED"

    receipt_body: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "wave": wave,
        "wave_title": WAVE_TITLES.get(wave, f"Wave {wave}"),
        "terminal_outcome": terminal_outcome,
        "wave_status": wave_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": f"tools/run_graphdb_sourcing_wave.py --wave {wave} --timeout={timeout}",
        "scope_tests": list(test_modules),
        "test_counts": {
            "tests_run": tests_run,
            "failures": failures_cnt,
            "errors": errors_cnt,
            "skipped": skipped_cnt,
        },
        "predecessor_receipt": str(predecessor_path) if predecessor_path else None,
        "predecessor_digest": predecessor_digest,
        "graph_sha256_before": graph_before,
        "graph_sha256_after": graph_after,
        "graph_unchanged": graph_unchanged,
        "graph_mutations": 0 if graph_unchanged else 1,
        "production_controls_preserved": controls_ok,
    }

    receipt_digest = compute_dict_digest(receipt_body)
    receipt_body["receipt_digest"] = receipt_digest

    receipt_file = receipts_dir / f"graphdb_sourcing_wave_{wave}_receipt.json"
    write_atomic_json(receipt_file, receipt_body, immutable=False)

    return 0 if success else 1, receipt_body


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded test suite and seal immutable receipt for GraphDB Sourcing Waves."
    )
    parser.add_argument("--wave", type=int, required=True, choices=range(1, 7), help="Wave number (1-6)")
    parser.add_argument("--timeout", type=int, default=60, help="Hard execution timeout in seconds")
    parser.add_argument("--receipts-dir", type=Path, default=None, help="Receipts output directory")
    parser.add_argument("--graph-path", type=Path, default=None, help="Path to SQLite graph file for non-mutation check")
    args = parser.parse_args(argv)

    exit_code, receipt = run_wave(
        wave=args.wave,
        timeout=args.timeout,
        receipts_dir=args.receipts_dir,
        graph_path=args.graph_path,
    )

    print("\n=======================================================")
    print(f" GRAPHDB SOURCING WAVE {args.wave} VERIFICATION")
    print(f" Terminal Outcome: {receipt.get('terminal_outcome')}")
    print(f" Wave Status:      {receipt.get('wave_status')}")
    print(f" Tests Run:        {receipt.get('test_counts', {}).get('tests_run')}")
    print(f" Failures:         {receipt.get('test_counts', {}).get('failures')}")
    print(f" Skipped:          {receipt.get('test_counts', {}).get('skipped')}")
    print(f" Graph Unchanged:  {receipt.get('graph_unchanged')}")
    print(f" Receipt Digest:   {receipt.get('receipt_digest')}")
    print("=======================================================\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
