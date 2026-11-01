"""Validation execution orchestrator, report writer, and CLI entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .fixtures import validate_fixtures, verify_inventory
from .issues import ContractError, ValidationFailure
from .registry import DEFAULT_CONTRACT_ROOT, SCHEMA_DIALECT, SchemaRegistry


def run_validation(contract_root: Path) -> dict[str, Any]:
    registry = SchemaRegistry(contract_root / "schemas")
    registry.load()
    schema_rows = registry.check_schema_documents()
    inventory = verify_inventory(contract_root, schema_rows)
    fixtures = validate_fixtures(contract_root, registry)
    return {
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "wave": 1,
        "status": "PASS",
        "schema_dialect": SCHEMA_DIALECT,
        "schema_count": len(schema_rows),
        "artifact_contract_count": len(schema_rows) - 1,
        "schemas": schema_rows,
        "inventory": inventory,
        "fixtures": fixtures,
        "provider_call_count": 0,
        "graph_mutation_count": 0,
        "index_mutation_count": 0,
        "configuration_mutation_count": 0,
        "model_mutation_count": 0,
        "active_runtime_mutation_count": 0,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-dir", type=Path, default=DEFAULT_CONTRACT_ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = run_validation(args.contracts_dir.resolve())
        if args.report:
            write_report(args.report.resolve(), report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "schemas": report["schema_count"],
                    "artifact_contracts": report["artifact_contract_count"],
                    "valid_fixtures": report["fixtures"]["valid_count"],
                    "invalid_fixtures_rejected": report["fixtures"]["invalid_count"],
                    "provider_calls": report["provider_call_count"],
                    "runtime_mutations": report["active_runtime_mutation_count"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (ContractError, ValidationFailure) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
