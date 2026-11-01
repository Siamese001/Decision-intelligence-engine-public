#!/usr/bin/env python3
"""Execute and seal the provider-free W5.1 semantic-expansion suite."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, utc_now
from tools.run_w5_trust_foundation_evaluation import evaluate as evaluate_w5

DEFAULT_RUNTIME = ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729"
DEFAULT_GRAPH = (
    DEFAULT_RUNTIME
    / "generations"
    / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3"
    / "graph.sqlite"
)
DEFAULT_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W5_1_SEMANTIC_EXPANSION_CONTRACT.json"
W5_CONTRACT = ROOT / "governance" / "decision_intelligence" / "W5_TRUST_FOUNDATION_CONTRACT.json"
DEFAULT_OUTPUT = ROOT / ".local" / "w5-trust-foundation-20260810" / "w5-1-semantic-expansion-evaluation.json"
CODE_PATHS = (
    ROOT / "tools" / "run_w5_1_semantic_expansion_evaluation.py",
    ROOT / "dual_pipeline" / "semantic_expansion.py",
    ROOT / "dual_pipeline" / "reference_monitor.py",
    ROOT / "tests" / "test_w5_1_semantic_expansion.py",
    ROOT / "tests" / "decision_intelligence_support.py",
)


class _RecordingResult(unittest.TextTestResult):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.outcomes: dict[str, str] = {}

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self.outcomes[test.id()] = "PASS"

    def addFailure(self, test: unittest.TestCase, err: object) -> None:  # noqa: N802
        super().addFailure(test, err)  # type: ignore[arg-type]
        self.outcomes[test.id()] = "FAIL"

    def addError(self, test: unittest.TestCase, err: object) -> None:  # noqa: N802
        super().addError(test, err)  # type: ignore[arg-type]
        self.outcomes[test.id()] = "ERROR"


def _run_suite() -> tuple[dict[str, str], str]:
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_w5_1_semantic_expansion")
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=_RecordingResult)
    result = runner.run(suite)
    assert isinstance(result, _RecordingResult)
    return dict(sorted(result.outcomes.items())), stream.getvalue()


def _valid_upstream(receipt: Mapping[str, Any]) -> bool:
    declared = receipt.get("canonical_digest")
    if not isinstance(declared, Mapping) or declared.get("algorithm") != "sha256":
        return False
    body = dict(receipt)
    body.pop("canonical_digest", None)
    status = receipt.get("status", receipt.get("receipt_status"))
    return (
        status == "PASS"
        and receipt.get("graph_unchanged") is True
        and receipt.get("provider_calls") == 0
        and receipt.get("graph_mutations") == 0
        and receipt.get("release_activation") is False
        and declared.get("value") == digest(body)
    )


def evaluate(
    graph: Path,
    contract: Path,
    *,
    upstream_w5_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    graph_before = sha256_file(graph) if graph.exists() else None
    upstream = dict(upstream_w5_receipt) if upstream_w5_receipt is not None else evaluate_w5(graph, W5_CONTRACT)
    upstream_valid = _valid_upstream(upstream)
    outcomes, log = _run_suite()
    graph_after = sha256_file(graph) if graph.exists() else None
    code_bindings = {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
        for path in CODE_PATHS
    }
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": "w5_1_semantic_expansion_runtime_receipt",
        "wave": "W5.1",
        "evaluation_id": "w5-1-semantic-expansion-20260810",
        "executed_at": utc_now(),
        "contract_path": str(contract.resolve()),
        "contract_digest": digest(json.loads(contract.read_text(encoding="utf-8"))),
        "runtime_root": str(ROOT.resolve()),
        "command": ".venv\\Scripts\\python.exe tools\\run_w5_1_semantic_expansion_evaluation.py",
        "code_bindings": code_bindings,
        "upstream_w5_receipt_digest": upstream.get("canonical_digest", {}).get("value"),
        "upstream_w5_receipt_status": upstream.get("status", upstream.get("receipt_status")),
        "upstream_w5_receipt_valid": upstream_valid,
        "adversarial_cases": outcomes,
        "test_log": log,
        "passed_cases": sum(value == "PASS" for value in outcomes.values()),
        "total_cases": len(outcomes),
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "graph_path": str(graph.resolve()),
        "graph_sha256_before": graph_before,
        "graph_sha256_after": graph_after,
        "graph_unchanged": graph_before == graph_after,
    }
    payload["status"] = (
        "PASS"
        if upstream_valid
        and outcomes
        and all(value == "PASS" for value in outcomes.values())
        and payload["graph_unchanged"]
        else "FAIL"
    )
    payload["canonical_digest"] = {"algorithm": "sha256", "value": digest(payload)}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.graph, args.contract)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(output),
                "passed_cases": result["passed_cases"],
                "total_cases": result["total_cases"],
                "upstream_w5_receipt_valid": result["upstream_w5_receipt_valid"],
                "graph_unchanged": result["graph_unchanged"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
