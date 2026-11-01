"""Persistent cache for benchmark query encodings across retrieval release evaluations.

Supports both SQLite-backed high-concurrency storage and JSON compatibility,
exporting hit/miss telemetry as OpenTelemetry-compatible attributes.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

from ..core import digest, read_json, write_json_exclusive
from ..index import EmbeddingBackend

logger = logging.getLogger("dual_pipeline.retrieval_release.query_encoding_cache")

CACHE_SCHEMA_VERSION = "1.0.0"


from ..index_core.embedding_space_contract import build_query_cache_key


def _query_cache_key(backend: EmbeddingBackend, query: str) -> str:
    """Builds a deterministic digest key for a query encoding under an exact model snapshot."""
    return build_query_cache_key(backend, query)


class QueryEncodingCache:
    """Caches pre-encoded query representations (dense, sparse, colbert) across evaluation runs."""

    def __init__(self, cache_file: Path, max_entries: int = 2048) -> None:
        self.cache_file = Path(cache_file)
        self._max_entries = max(1, int(max_entries))
        self._is_sqlite = self.cache_file.suffix in (".sqlite", ".db")
        self._sqlite_file = (
            self.cache_file if self._is_sqlite else self.cache_file.with_suffix(".sqlite")
        )
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._hits = 0
        self._misses = 0
        self._init_sqlite()
        self._load()

    def _put_in_memory_lru(self, key: str, value: dict[str, Any]) -> None:
        self._entries.pop(key, None)
        while len(self._entries) >= self._max_entries and self._entries:
            oldest_key = next(iter(self._entries))
            del self._entries[oldest_key]
        self._entries[key] = value

    def _init_sqlite(self) -> None:
        try:
            self._sqlite_file.parent.mkdir(parents=True, exist_ok=True)
            with create_sqlite_conn(self._sqlite_file) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS query_encodings (
                        cache_key TEXT PRIMARY KEY,
                        query TEXT NOT NULL,
                        encoded TEXT NOT NULL,
                        created_at REAL DEFAULT (strftime('%s', 'now'))
                    )
                    """
                )
        except Exception as exc:
            logger.debug(f"[QUERY-ENCODING-CACHE] SQLite init fallback: {exc}")

    def _load(self) -> None:
        if self._load_from_sqlite():
            return
        self._load_from_json()

    def _load_from_sqlite(self) -> bool:
        if not self._sqlite_file.is_file():
            return False
        try:
            with create_sqlite_conn(self._sqlite_file) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT cache_key, query, encoded FROM query_encodings")
                for k, q, enc_str in cursor.fetchall():
                    self._put_in_memory_lru(k, {"query": q, "encoded": json.loads(enc_str)})
            return bool(self._entries)
        except Exception as exc:
            logger.debug(f"[QUERY-ENCODING-CACHE] Could not load from SQLite: {exc}")
            return False

    def _load_from_json(self) -> None:
        if not self.cache_file.is_file() or self._is_sqlite:
            return
        try:
            data = read_json(self.cache_file)
            if data.get("schema_version") == CACHE_SCHEMA_VERSION:
                for k, v in (data.get("entries") or {}).items():
                    self._put_in_memory_lru(k, v)
                self._migrate_to_sqlite()
        except Exception as exc:
            logger.debug(f"[QUERY-ENCODING-CACHE] Could not read cache {self.cache_file}: {exc}")
            self._entries = {}

    def _migrate_to_sqlite(self) -> None:
        if not self._entries:
            return
        try:
            with create_sqlite_conn(self._sqlite_file) as conn:
                for k, v in self._entries.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO query_encodings (cache_key, query, encoded) VALUES (?, ?, ?)",
                        (k, v["query"], json.dumps(v["encoded"])),
                    )
                conn.commit()
        except Exception:
            pass

    def get(self, backend: EmbeddingBackend, query: str) -> dict[str, Any] | None:
        key = _query_cache_key(backend, query)
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._entries[key] = entry
            self._hits += 1
            return entry.get("encoded")

        # Try SQLite query directly if not in memory
        try:
            with create_sqlite_conn(self._sqlite_file) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT encoded FROM query_encodings WHERE cache_key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    enc = json.loads(row[0])
                    self._put_in_memory_lru(key, {"query": query, "encoded": enc})
                    self._hits += 1
                    return enc
        except Exception:
            pass

        self._misses += 1
        return None

    def set(self, backend: EmbeddingBackend, query: str, encoded: dict[str, Any]) -> None:
        key = _query_cache_key(backend, query)
        self._put_in_memory_lru(key, {"query": query, "encoded": encoded})
        self._dirty = True

        # Write through to SQLite immediately
        try:
            with create_sqlite_conn(self._sqlite_file) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO query_encodings (cache_key, query, encoded) VALUES (?, ?, ?)",
                    (key, query, json.dumps(encoded)),
                )
                conn.commit()
        except Exception:
            pass

    def set_many(self, backend: EmbeddingBackend, items: Sequence[Tuple[str, dict[str, Any]]]) -> None:
        if not items:
            return
        rows = []
        for query, encoded in items:
            key = _query_cache_key(backend, query)
            self._put_in_memory_lru(
                key,
                {
                    "query": query,
                    "encoded": encoded,
                },
            )
            rows.append((key, query, json.dumps(encoded)))
        self._dirty = True
        try:
            with create_sqlite_conn(self._sqlite_file) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO query_encodings (cache_key, query, encoded) VALUES (?, ?, ?)",
                    rows,
                )
                conn.commit()
        except Exception:
            pass

    def flush(self) -> None:
        if not self._dirty:
            return
        if not self._is_sqlite:
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "entries": self._entries,
                }
                tmp_path = self.cache_file.with_name(f".{self.cache_file.name}.tmp")
                write_json_exclusive(tmp_path, payload)
                os.replace(tmp_path, self.cache_file)
                self._dirty = False
                logger.debug(
                    f"[QUERY-ENCODING-CACHE] Flushed {len(self._entries)} entries to {self.cache_file}"
                )
            except Exception as exc:
                logger.debug(f"[QUERY-ENCODING-CACHE] Failed to flush cache: {exc}")
        else:
            self._dirty = False

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def export_otel_attributes(self) -> dict[str, Any]:
        """Export OpenTelemetry attributes for query encoding cache performance."""
        total = self._hits + self._misses
        rate = (self._hits / total) if total > 0 else 0.0
        return {
            "query_cache.hits": self._hits,
            "query_cache.misses": self._misses,
            "query_cache.total_requests": total,
            "query_cache.hit_rate": rate,
        }
