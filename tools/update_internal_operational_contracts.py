#!/usr/bin/env python3
"""Seal internal-operational artifacts and refresh the contract inventory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contract_validation import SchemaRegistry  # noqa: E402
from dual_pipeline.core import digest, read_json, sha256_file, write_json  # noqa: E402

SEALED_PATHS = (
    ROOT / "requirements" / "internal-operational-policy.json",
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "internal-operational-policy.json",
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "internal-operational-qualification-receipt.json",
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "internal-operational-revocation-receipt.json",
)


def seal(path: Path) -> None:
    value = read_json(path)
    value.pop("canonical_digest", None)
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(value),
    }
    write_json(path, value)


def update_inventory() -> None:
    contract_root = ROOT / "contracts" / "dual_pipeline"
    registry = SchemaRegistry(contract_root / "schemas")
    registry.load()
    rows = registry.check_schema_documents()
    inventory_path = contract_root / "contract_inventory.json"
    previous = read_json(inventory_path)
    inventory = {
        "compatibility_policy": previous["compatibility_policy"],
        "initiative_id": previous["initiative_id"],
        "inventory_version": previous["inventory_version"],
        "schemas": rows,
    }
    inventory["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(inventory),
    }
    write_json(inventory_path, inventory)


def main() -> int:
    for path in SEALED_PATHS:
        seal(path)
    update_inventory()
    print(
        json.dumps(
            {
                "status": "PASS",
                "sealed_artifact_count": len(SEALED_PATHS),
                "contract_inventory": str(
                    ROOT / "contracts" / "dual_pipeline" / "contract_inventory.json"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
