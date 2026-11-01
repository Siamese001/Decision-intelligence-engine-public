"""4-Tier Deterministic Cache Hierarchy for Tokenomics Cost Optimization.

Implements four non-semantic deterministic cache layers backed by WAL-mode SQLite:
1. ExactResponseDigestCache: Content-addressable exact response cache keyed by
   sha256(model + effort + canonical_messages + schema_hash).
2. TurnWindowIncrementalCache: 25-turn dialogue window cache for incremental transcript
   normalization so single-turn edits only re-process dirty windows.
3. PersistentNegativeFailureCache: Cross-process SQLite failure digest quarantine
   integrated with CandidateCircuitBreaker so malformed/failing payloads never burn
   repeated retry tokens across process restarts.
4. SubgraphReadMemoCache: Generation-bound memoization for repeated graph/entity
   subgraph reads within an active graph generation.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..storage.governed_context import GovernedStorageContext, Row

_MULTI_TIER_SCHEMA = """
CREATE TABLE IF NOT EXISTS exact_response_cache (
    digest_key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    response_json TEXT NOT NULL,
    saved_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    saved_completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_epoch REAL NOT NULL,
    expires_epoch REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turn_window_cache (
    window_digest TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    window_index INTEGER NOT NULL,
    normalized_turns_json TEXT NOT NULL,
    created_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS negative_failure_cache (
    failure_digest TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    defect_code TEXT NOT NULL,
    rationale TEXT NOT NULL,
    fail_count INTEGER NOT NULL DEFAULT 1,
    updated_epoch REAL NOT NULL,
    expires_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subgraph_memo_cache (
    memo_key TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_epoch REAL NOT NULL
);
"""

_SCHEMA_INIT_LOCK = threading.Lock()
_INITIALIZED_SCHEMA_INODES: dict[str, int] = {}


def _connect_cache_db(db_path: Path) -> Any:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(db_path.resolve())
    conn = GovernedStorageContext.connect(db_path)
    conn.row_factory = Row
    with _SCHEMA_INIT_LOCK:
        st = db_path.stat()
        if _INITIALIZED_SCHEMA_INODES.get(resolved) != st.st_ino or st.st_size == 0:
            conn.executescript(_MULTI_TIER_SCHEMA)
            _INITIALIZED_SCHEMA_INODES[resolved] = db_path.stat().st_ino
    return conn


def compute_exact_request_digest(
    *,
    model: str,
    effort: str,
    messages: Sequence[Mapping[str, Any]] | str,
    schema_hash: str = "",
) -> str:
    """Compute a deterministic SHA-256 digest over model, effort, canonical messages, and schema."""
    msg_blob = (
        messages
        if isinstance(messages, str)
        else json.dumps(list(messages), sort_keys=True, separators=(",", ":"))
    )
    canonical = f"{model.strip().lower()}|{effort.strip().lower()}|{schema_hash}|{msg_blob}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExactResponseDigestCache:
    """Content-addressable exact response cache eliminating duplicate LLM calls."""

    def __init__(self, db_path: Path, default_ttl_seconds: float = 86400.0) -> None:
        self.db_path = Path(db_path)
        self.default_ttl_seconds = float(default_ttl_seconds)

    def get(
        self,
        *,
        model: str,
        effort: str,
        messages: Sequence[Mapping[str, Any]] | str,
        schema_hash: str = "",
    ) -> dict[str, Any] | None:
        digest_key = compute_exact_request_digest(
            model=model, effort=effort, messages=messages, schema_hash=schema_hash
        )
        now = time.time()
        conn = _connect_cache_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT response_json, expires_epoch FROM exact_response_cache WHERE digest_key=?",
                (digest_key,),
            ).fetchone()
            if row is None or float(row["expires_epoch"]) < now:
                return None
            conn.execute(
                "UPDATE exact_response_cache SET hit_count = hit_count + 1 WHERE digest_key=?",
                (digest_key,),
            )
            conn.commit()
            return json.loads(row["response_json"])
        finally:
            conn.close()

    def put(
        self,
        *,
        model: str,
        effort: str,
        messages: Sequence[Mapping[str, Any]] | str,
        response: Mapping[str, Any],
        schema_hash: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ttl_seconds: float | None = None,
    ) -> str:
        digest_key = compute_exact_request_digest(
            model=model, effort=effort, messages=messages, schema_hash=schema_hash
        )
        now = time.time()
        expires = now + float(ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds)
        conn = _connect_cache_db(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO exact_response_cache
                   (digest_key, model, effort, response_json, saved_prompt_tokens,
                    saved_completion_tokens, created_epoch, expires_epoch, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    digest_key,
                    model,
                    effort,
                    json.dumps(dict(response), sort_keys=True),
                    int(prompt_tokens),
                    int(completion_tokens),
                    now,
                    expires,
                ),
            )
            conn.commit()
            return digest_key
        finally:
            conn.close()


class TurnWindowIncrementalCache:
    """25-turn dialogue window cache for incremental transcript normalization."""

    def __init__(self, db_path: Path, window_size: int = 25) -> None:
        self.db_path = Path(db_path)
        self.window_size = max(1, int(window_size))

    @staticmethod
    def _window_digest(meeting_id: str, window_idx: int, raw_turns: Sequence[Any]) -> str:
        serialized = json.dumps(list(raw_turns), sort_keys=True, separators=(",", ":"))
        payload = f"{meeting_id}|w{window_idx}|{serialized}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def partition_windows(self, turns: Sequence[Any]) -> list[tuple[int, list[Any]]]:
        windows: list[tuple[int, list[Any]]] = []
        for idx in range(0, len(turns), self.window_size):
            win_idx = idx // self.window_size
            windows.append((win_idx, list(turns[idx : idx + self.window_size])))
        return windows

    def resolve_cached_or_dirty_windows(
        self, meeting_id: str, turns: Sequence[Any]
    ) -> dict[str, Any]:
        windows = self.partition_windows(turns)
        cached_windows: dict[int, list[Any]] = {}
        dirty_windows: list[tuple[int, str, list[Any]]] = []
        conn = _connect_cache_db(self.db_path)
        try:
            for win_idx, raw_slice in windows:
                w_digest = self._window_digest(meeting_id, win_idx, raw_slice)
                row = conn.execute(
                    "SELECT normalized_turns_json FROM turn_window_cache WHERE window_digest=?",
                    (w_digest,),
                ).fetchone()
                if row is not None:
                    cached_windows[win_idx] = json.loads(row["normalized_turns_json"])
                else:
                    dirty_windows.append((win_idx, w_digest, raw_slice))
        finally:
            conn.close()
        return {
            "total_windows": len(windows),
            "cached_window_count": len(cached_windows),
            "dirty_window_count": len(dirty_windows),
            "cached_windows": cached_windows,
            "dirty_windows": dirty_windows,
        }

    def store_window(
        self, meeting_id: str, window_index: int, raw_turns: Sequence[Any], normalized_turns: Sequence[Any]
    ) -> str:
        w_digest = self._window_digest(meeting_id, window_index, raw_turns)
        conn = _connect_cache_db(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO turn_window_cache
                   (window_digest, meeting_id, window_index, normalized_turns_json, created_epoch)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    w_digest,
                    meeting_id,
                    int(window_index),
                    json.dumps(list(normalized_turns), sort_keys=True),
                    time.time(),
                ),
            )
            conn.commit()
            return w_digest
        finally:
            conn.close()


class PersistentNegativeFailureCache:
    """Cross-process SQLite failure digest quarantine preventing repeated token burn."""

    def __init__(
        self, db_path: Path, max_failures: int = 2, ttl_seconds: float = 3600.0
    ) -> None:
        self.db_path = Path(db_path)
        self.max_failures = max(1, int(max_failures))
        self.ttl_seconds = float(ttl_seconds)

    @staticmethod
    def compute_failure_digest(stage: str, payload_text: str) -> str:
        return hashlib.sha256(f"{stage.strip()}|{payload_text}".encode("utf-8")).hexdigest()

    def record_failure(
        self, *, stage: str, payload_text: str, defect_code: str, rationale: str
    ) -> int:
        f_digest = self.compute_failure_digest(stage, payload_text)
        now = time.time()
        expires = now + self.ttl_seconds
        conn = _connect_cache_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT fail_count, expires_epoch FROM negative_failure_cache WHERE failure_digest=?",
                (f_digest,),
            ).fetchone()
            new_count = 1
            if row is not None and float(row["expires_epoch"]) >= now:
                new_count = int(row["fail_count"]) + 1
            conn.execute(
                """INSERT OR REPLACE INTO negative_failure_cache
                   (failure_digest, stage, defect_code, rationale, fail_count, updated_epoch, expires_epoch)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f_digest, stage, defect_code, rationale, new_count, now, expires),
            )
            conn.commit()
            return new_count
        finally:
            conn.close()

    def is_quarantined(self, *, stage: str, payload_text: str) -> tuple[bool, dict[str, Any] | None]:
        f_digest = self.compute_failure_digest(stage, payload_text)
        now = time.time()
        conn = _connect_cache_db(self.db_path)
        try:
            row = conn.execute(
                """SELECT defect_code, rationale, fail_count, expires_epoch
                   FROM negative_failure_cache WHERE failure_digest=?""",
                (f_digest,),
            ).fetchone()
            if row is None or float(row["expires_epoch"]) < now:
                return False, None
            count = int(row["fail_count"])
            if count >= self.max_failures:
                return True, {
                    "failure_digest": f_digest,
                    "defect_code": row["defect_code"],
                    "rationale": row["rationale"],
                    "fail_count": count,
                }
            return False, None
        finally:
            conn.close()


class SubgraphReadMemoCache:
    """Generation-bound memoization for repeated graph/entity lookups within an active generation."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    @staticmethod
    def _memo_key(generation_id: str, query_signature: str) -> str:
        return hashlib.sha256(f"{generation_id}|{query_signature}".encode("utf-8")).hexdigest()

    def get(self, *, generation_id: str, query_signature: str) -> dict[str, Any] | None:
        m_key = self._memo_key(generation_id, query_signature)
        conn = _connect_cache_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM subgraph_memo_cache WHERE memo_key=? AND generation_id=?",
                (m_key, generation_id),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])
        finally:
            conn.close()

    def put(
        self, *, generation_id: str, query_signature: str, payload: Mapping[str, Any]
    ) -> str:
        m_key = self._memo_key(generation_id, query_signature)
        conn = _connect_cache_db(self.db_path)
        try:
            conn.execute(
                "DELETE FROM subgraph_memo_cache WHERE generation_id != ?",
                (generation_id,),
            )
            conn.execute(
                """INSERT OR REPLACE INTO subgraph_memo_cache
                   (memo_key, generation_id, payload_json, created_epoch)
                   VALUES (?, ?, ?, ?)""",
                (m_key, generation_id, json.dumps(dict(payload), sort_keys=True), time.time()),
            )
            conn.commit()
            return m_key
        finally:
            conn.close()
