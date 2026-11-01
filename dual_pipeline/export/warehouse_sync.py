"""
dual_pipeline/export/warehouse_sync.py

Data warehouse synchronization module for canonical knowledge graph tables.
Exports nodes, edges, and assertions to tabular formats (parquet, jsonl, csv, sqlite)
for ingestion into Snowflake, BigQuery, Databricks, or cloud object storage.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import create_sqlite_conn, sqlite3
from storage.database import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class TableExportSummary:
    table_name: str
    record_count: int
    file_path: str
    file_bytes: int
    sha256: str


@dataclass
class WarehouseSyncResult:
    destination_format: str
    output_dir: str
    timestamp: str
    tables: Dict[str, TableExportSummary] = field(default_factory=dict)
    total_records: int = 0
    total_bytes: int = 0
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "destination_format": self.destination_format,
            "output_dir": self.output_dir,
            "timestamp": self.timestamp,
            "total_records": self.total_records,
            "total_bytes": self.total_bytes,
            "success": self.success,
            "tables": {k: v.__dict__ for k, v in self.tables.items()},
        }


class WarehouseSyncEngine:
    """Exports canonical graph data to data warehouse and lakehouse storage formats."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db_manager = db_manager or DatabaseManager()

    def sync(
        self,
        output_dir: str | Path,
        destination_format: str = "jsonl",
        incremental_since: Optional[str] = None,
    ) -> WarehouseSyncResult:
        """
        Exports canonical_graph_nodes, canonical_graph_edges, and assertion tables.
        destination_format: 'parquet', 'jsonl', 'csv', 'sqlite'
        """
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(timezone.utc).isoformat()

        result = WarehouseSyncResult(
            destination_format=destination_format.lower(),
            output_dir=str(out_path),
            timestamp=now_str,
        )

        with self.db_manager.get_canonical_conn(readonly=True) as conn:
            conn.row_factory = sqlite3.Row

            # 1. Fetch Nodes
            node_query = "SELECT * FROM canonical_graph_nodes"
            node_params: List[Any] = []
            if incremental_since:
                node_query += " WHERE updated_at >= ? OR created_at >= ?"
                node_params = [incremental_since, incremental_since]
            nodes = [dict(r) for r in conn.execute(node_query, node_params).fetchall()]

            # 2. Fetch Edges
            edge_query = "SELECT * FROM canonical_graph_edges"
            edge_params: List[Any] = []
            if incremental_since:
                edge_query += " WHERE updated_at >= ? OR created_at >= ?"
                edge_params = [incremental_since, incremental_since]
            edges = [dict(r) for r in conn.execute(edge_query, edge_params).fetchall()]

            # 3. Fetch Assertions
            assertion_query = "SELECT * FROM assertion"
            assertion_params: List[Any] = []
            if incremental_since:
                assertion_query += " WHERE observed_at >= ?"
                assertion_params = [incremental_since]
            assertions = [dict(r) for r in conn.execute(assertion_query, assertion_params).fetchall()]

        tables_data = {
            "canonical_graph_nodes": nodes,
            "canonical_graph_edges": edges,
            "assertions": assertions,
        }

        for table_name, records in tables_data.items():
            summary = self._export_table(table_name, records, out_path, result.destination_format)
            result.tables[table_name] = summary
            result.total_records += summary.record_count
            result.total_bytes += summary.file_bytes

        # Write sync manifest
        manifest_path = out_path / "sync_manifest.json"
        manifest_content = json.dumps(result.to_dict(), indent=2)
        manifest_path.write_text(manifest_content, encoding="utf-8")

        return result

    def _export_table(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        out_dir: Path,
        fmt: str,
    ) -> TableExportSummary:
        if fmt == "csv":
            file_path = out_dir / f"{table_name}.csv"
            if records:
                fieldnames = list(records[0].keys())
                with open(file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in records:
                        writer.writerow(r)
            else:
                file_path.write_text("", encoding="utf-8")

        elif fmt == "sqlite":
            file_path = out_dir / f"{table_name}.sqlite"
            if file_path.exists():
                file_path.unlink()
            replica_conn = create_sqlite_conn(file_path)
            try:
                if records:
                    cols = list(records[0].keys())
                    col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
                    placeholders = ", ".join("?" for _ in cols)
                    replica_conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
                    rows = [[str(r.get(c, "")) if r.get(c) is not None else None for c in cols] for r in records]
                    replica_conn.executemany(f'INSERT INTO "{table_name}" VALUES ({placeholders})', rows)
                    replica_conn.commit()
                else:
                    replica_conn.execute(f'CREATE TABLE "{table_name}" (id TEXT)')
                    replica_conn.commit()
            finally:
                replica_conn.close()

        elif fmt == "parquet":
            # Attempt PyArrow export; fallback to JSONL if pyarrow is unavailable
            file_path = out_dir / f"{table_name}.parquet"
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq

                if records:
                    table = pa.Table.from_pylist(records)
                    pq.write_table(table, str(file_path))
                else:
                    file_path = out_dir / f"{table_name}.jsonl"
                    file_path.write_text("", encoding="utf-8")
            except ImportError:
                # Graceful fallback to JSONL with notice
                logger.info("pyarrow not installed, exporting %s as jsonl instead", table_name)
                file_path = out_dir / f"{table_name}.jsonl"
                with open(file_path, "w", encoding="utf-8") as f:
                    for r in records:
                        f.write(json.dumps(r) + "\n")

        else:  # default 'jsonl'
            file_path = out_dir / f"{table_name}.jsonl"
            with open(file_path, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

        raw_bytes = file_path.read_bytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        return TableExportSummary(
            table_name=table_name,
            record_count=len(records),
            file_path=str(file_path),
            file_bytes=len(raw_bytes),
            sha256=sha256,
        )
