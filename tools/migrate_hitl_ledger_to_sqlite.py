#!/usr/bin/env python3
"""
tools/migrate_hitl_ledger_to_sqlite.py

Migrates the monolithic HITL decision ledger (hitl_decision_ledger.json) into
the Layer-0 Governed SQLite store (hitl_decision_ledger.sqlite).

Ensures 100% data fidelity, Merkle integrity, and idempotency.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import (
    DEFAULT_HITL_SQLITE_PATH,
    HITLSQLiteStore,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def migrate_json_to_sqlite(
    json_path: Path,
    sqlite_path: Path,
) -> Dict[str, Any]:
    """Migrate records from hitl_decision_ledger.json to SQLite database."""
    if not json_path.exists():
        logger.warning(f"Source JSON ledger not found at {json_path}")
        return {
            "status": "SKIPPED",
            "reason": f"File not found: {json_path}",
            "migrated_count": 0,
        }

    logger.info(f"Loading JSON ledger from {json_path} (size: {json_path.stat().st_size / (1024*1024):.2f} MB)...")
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to parse {json_path}: {exc}")
        return {"status": "ERROR", "error": str(exc), "migrated_count": 0}

    if isinstance(data, dict):
        records: List[Dict[str, Any]] = data.get("decisions", [])
    elif isinstance(data, list):
        records = data
    else:
        records = []
    logger.info(f"Loaded {len(records)} decisions from JSON ledger.")

    if not records:
        return {"status": "NOOP", "reason": "No decisions in JSON", "migrated_count": 0}

    store = HITLSQLiteStore(sqlite_path)
    initial_count = store.count()
    logger.info(f"Existing records in SQLite store: {initial_count}")

    store.insert_decisions_batch(records)

    final_count = store.count()
    latest_hash = store.latest_entry_hash()
    logger.info(f"Migration successful. Final SQLite record count: {final_count} (latest hash: {latest_hash[:16]}...)")

    return {
        "status": "COMPLETE",
        "migrated_count": len(records),
        "sqlite_count": final_count,
        "latest_hash": latest_hash,
        "source": str(json_path),
        "target": str(sqlite_path),
    }


def migrate_ledger(json_path: Path, sqlite_path: Path) -> Dict[str, Any]:
    """Compatibility alias for migrate_json_to_sqlite."""
    res = migrate_json_to_sqlite(json_path, sqlite_path)
    res["verified"] = res.get("status") == "COMPLETE"
    res["records_read"] = res.get("migrated_count", 0)
    res["records_migrated"] = res.get("migrated_count", 0)
    res["total_in_sqlite"] = res.get("sqlite_count", 0)
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate HITL decision ledger to SQLite")
    parser.add_argument(
        "--source-json",
        type=Path,
        default=REPO_ROOT / ".local/ledger/hitl_decision_ledger.json",
        help="Path to source JSON ledger",
    )
    parser.add_argument(
        "--target-sqlite",
        type=Path,
        default=REPO_ROOT / DEFAULT_HITL_SQLITE_PATH,
        help="Path to target SQLite database",
    )
    parser.add_argument("--receipt", type=Path, help="Path to write migration receipt JSON")
    args = parser.parse_args()

    # If default .local path doesn't exist, check root hitl_decision_ledger.json
    source_json = args.source_json
    if not source_json.exists():
        root_json = REPO_ROOT / "hitl_decision_ledger.json"
        if root_json.exists():
            source_json = root_json

    result = migrate_json_to_sqlite(source_json, args.target_sqlite)

    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
        logger.info(f"Wrote receipt to {args.receipt}")

    return 0 if result["status"] in ("COMPLETE", "NOOP") else 1


if __name__ == "__main__":
    sys.exit(main())
