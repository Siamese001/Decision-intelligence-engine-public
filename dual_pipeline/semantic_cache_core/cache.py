"""Core SemanticCache implementation."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..core import assert_safe_mutable_path, digest, is_link_or_reparse, read_json, stable_id, utc_now
from .chain_cache import get_global_chain_cache
from .config import _load_or_initialize_config, load_semantic_cache_policy, semantic_cache_config_path
from .constants import _SCHEMA, BGE_M3_BACKEND_IDS, QUERY_NAMESPACE_FIELDS, SUPPORTED_SEMANTIC_NAMESPACES
from .helpers import _pack_vector
from .merkle_cache import extract_earliest_stale_after, is_temporally_stale, sanitize_output_for_cache_replay
from .models import PendingSemanticCacheEntry, SemanticCacheLookup, SemanticCachePolicy, SemanticCacheWriteResult

_CACHE_WRITE_LOCK = threading.RLock()


class SemanticCache:
    """Runtime cache whose availability never changes pipeline correctness."""

    def __init__(self, runtime_root: Path) -> None:
        supplied = Path(runtime_root).expanduser().absolute()
        self.runtime_root = supplied.resolve()
        self.cache_root = self.runtime_root / "cache"
        self.path = self.cache_root / "semantic_cache.sqlite"
        self.config_path = semantic_cache_config_path(self.runtime_root)
        self.policy: SemanticCachePolicy | None = None
        self.mode = "off"
        self.config_source = "unavailable"
        self.health_reason = "CACHE_UNINITIALIZED"
        self.embedding_binding: dict[str, str] | None = None
        self.last_binding_result: dict[str, Any] = {
            "status": "UNBOUND",
            "purged_entry_count": 0,
        }
        try:
            if is_link_or_reparse(supplied):
                raise RuntimeError("runtime root cannot be a link or reparse point")
            self.policy = load_semantic_cache_policy()
            config, self.config_source = _load_or_initialize_config(self.runtime_root, self.policy)
            self.mode = config["mode"]
            self.health_reason = "CACHE_READY"
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self.health_reason = f"CACHE_CONFIG_UNAVAILABLE:{type(exc).__name__}"

    @property
    def can_read(self) -> bool:
        return (
            self.health_reason == "CACHE_READY"
            and self.embedding_binding is not None
            and self.mode in {"read", "read_write"}
        )

    @property
    def can_write(self) -> bool:
        return (
            self.health_reason == "CACHE_READY"
            and self.embedding_binding is not None
            and self.mode in {"write", "read_write"}
        )

    @property
    def policy_digest(self) -> str | None:
        return self.policy.policy_digest if self.policy is not None else None

    def _disabled_reason(self, operation: str) -> str:
        if self.health_reason != "CACHE_READY":
            return self.health_reason
        if self.embedding_binding is None:
            return "CACHE_UNBOUND_EMBEDDING"
        return f"CACHE_{operation}_DISABLED"

    @staticmethod
    def _validate_namespace_requirements(context: Mapping[str, Any]) -> None:
        use_case = context.get("use_case")
        if use_case == "query" and context.get("cache_payload_kind"):
            missing = sorted(QUERY_NAMESPACE_FIELDS - set(context))
            if missing:
                raise ValueError(f"semantic cache query namespace is incomplete: {missing}")
            if ("user_id" in context or "subject" in context) and not context.get("subject_scope_digest"):
                raise ValueError("semantic cache query namespace requires subject_scope_digest when subject is specified")
        elif use_case in {"l2_extraction", "micro_judge", "l2_healer"}:
            for req_key in ("graph_generation_id", "merkle_root_hash"):
                if not context.get(req_key):
                    raise ValueError(f"semantic cache {use_case} namespace requires {req_key}")

    def compatibility(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Bind the packaged cache policy into every lookup namespace."""
        self._validate_namespace_requirements(context)
        subject_scope_digest = context.get("subject_scope_digest") or digest({
            "subject": context.get("user_id") or context.get("subject") or "anonymous",
            "classification": context.get("classification") or "CLIENT_SAFE",
            "audiences": sorted(context.get("audiences") or []),
        })
        return dict(context) | {
            "classification": context.get("classification") or "CLIENT_SAFE",
            "subject_scope_digest": subject_scope_digest,
            "authorization_context_digest": context.get("authorization_context_digest"),
            "semantic_cache_policy_id": self.policy.policy_id if self.policy is not None else None,
            "semantic_cache_policy_version": self.policy.policy_version if self.policy is not None else None,
            "semantic_cache_policy_digest": self.policy_digest,
        }

    def _connection(self) -> sqlite3.Connection:
        assert_safe_mutable_path(self.runtime_root, self.cache_root, label="semantic cache directory")
        assert_safe_mutable_path(self.runtime_root, self.path, label="semantic cache database")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        db_exists = self.path.is_file() and self.path.stat().st_size > 0
        from ..storage.governed_context import GovernedStorageContext

        connection = GovernedStorageContext.connect(self.path)
        connection.row_factory = sqlite3.Row
        if not db_exists:
            connection.executescript(_SCHEMA)
        return connection

    @staticmethod
    def _valid_bge_m3_binding(binding: Mapping[str, Any]) -> bool:
        return (
            binding.get("embedding_backend") in BGE_M3_BACKEND_IDS
            and binding.get("model_id") == "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
            and isinstance(binding.get("model_revision"), str)
            and re.fullmatch(r"[0-9a-f]{40}", binding["model_revision"]) is not None
            and isinstance(binding.get("model_manifest_digest"), str)
            and re.fullmatch(r"[0-9a-f]{64}", binding["model_manifest_digest"]) is not None
        )

    def _bind_embedding_identity(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a verified BGE identity and purge vectors from any other encoder."""
        normalized = {k: binding.get(k) for k in ("embedding_backend", "model_id", "model_revision", "model_manifest_digest")}
        if not self._valid_bge_m3_binding(normalized):
            self.embedding_binding = None
            self.health_reason = "CACHE_BINDING_REJECTED_NON_BGE_ENCODER"
            self.last_binding_result = {"status": "REJECTED", "purged_entry_count": 0}
            return dict(self.last_binding_result)
        if self.health_reason == "CACHE_BINDING_REJECTED_NON_BGE_ENCODER":
            self.health_reason = "CACHE_READY"
        if self.health_reason != "CACHE_READY":
            self.last_binding_result = {"status": "SKIPPED", "purged_entry_count": 0}
            return dict(self.last_binding_result)
        try:
            connection = self._connection()
            try:
                row = connection.execute(
                    "SELECT embedding_backend,model_id,model_revision,model_manifest_digest FROM semantic_cache_embedding_binding WHERE binding_id=1"
                ).fetchone()
                existing = dict(row) if row is not None else None
                entry_count = int(connection.execute("SELECT count(*) FROM semantic_cache_entry").fetchone()[0])
                if existing != normalized:
                    connection.execute("DELETE FROM semantic_cache_entry")
                    get_global_chain_cache().clear()
                    connection.execute("DELETE FROM semantic_cache_embedding_binding")
                    connection.execute(
                        "INSERT INTO semantic_cache_embedding_binding (binding_id,embedding_backend,model_id,model_revision,model_manifest_digest,bound_at) VALUES (1,?,?,?,?,?)",
                        (normalized["embedding_backend"], normalized["model_id"], normalized["model_revision"], normalized["model_manifest_digest"], utc_now()),
                    )
                    connection.commit()
                    result = {"status": "REBUILT" if entry_count else "BOUND", "purged_entry_count": entry_count, "binding": normalized}
                else:
                    result = {"status": "BOUND", "purged_entry_count": 0, "binding": normalized}
                self.embedding_binding = dict(normalized)
                self.last_binding_result = result
                return dict(result)
            finally:
                connection.close()
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            self.health_reason = f"CACHE_BINDING_UNAVAILABLE:{type(exc).__name__}"
            self.embedding_binding = None
            self.last_binding_result = {"status": "ERROR", "purged_entry_count": 0}
            return dict(self.last_binding_result)

    def bind_embedding_backend(self, backend: Any) -> dict[str, Any]:
        """Bind cache vectors to a real BGE-M3 encoder; no substitute is accepted."""
        if getattr(backend, "retrieval_encoder", None) != "real-bge-m3":
            return self._bind_embedding_identity({})
        return self._bind_embedding_identity({
            "embedding_backend": getattr(backend, "backend_id", None),
            "model_id": getattr(backend, "model_id", None),
            "model_revision": getattr(backend, "model_revision", None),
            "model_manifest_digest": getattr(backend, "model_manifest_digest", None),
        })

    def rebuild_from_validated_active_pair(self, pair: Mapping[str, Any]) -> dict[str, Any]:
        """Rebuild retained cache state after the caller has validated an active BGE pair."""
        with _CACHE_WRITE_LOCK:
            result = self._bind_embedding_identity(pair)
            if result.get("status") not in {"BOUND", "REBUILT"}:
                return result
            pair_identity = {k: pair.get(k) for k in ("generation_id", "graph_sha256", "sidecar_sha256") if pair.get(k) is not None}
            if not pair_identity:
                return result
            try:
                self.cache_root.mkdir(parents=True, exist_ok=True)
                binding_file = self.cache_root / "active_pair_binding.json"
                prev_identity = json.loads(binding_file.read_text(encoding="utf-8")) if binding_file.is_file() else None
                if prev_identity != pair_identity:
                    connection = self._connection()
                    try:
                        entry_count = int(connection.execute("SELECT count(*) FROM semantic_cache_entry").fetchone()[0])
                        if entry_count > 0:
                            purge_sql = "DELETE FROM semantic_cache_entry"
                            connection.execute(purge_sql)
                            get_global_chain_cache().clear()
                            connection.commit()
                            result["status"] = "REBUILT"
                            result["purged_entry_count"] = int(result.get("purged_entry_count", 0)) + entry_count
                    finally:
                        connection.close()
                    binding_file.write_text(json.dumps(pair_identity, sort_keys=True), encoding="utf-8")
                result["active_pair_binding"] = pair_identity
                self.last_binding_result = dict(result)
            except (OSError, sqlite3.Error, TypeError, ValueError):
                pass
            return dict(result)

    def _source_prefix_is_valid(self, row: sqlite3.Row) -> bool:
        run_id = row["source_run_id"]
        trace_id = row["source_trace_id"]
        count = row["source_event_count"]
        expected_chain = row["source_event_chain_digest"]
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(trace_id, str)
            or not trace_id
            or not isinstance(count, int)
            or count < 1
            or not isinstance(expected_chain, str)
            or len(expected_chain) != 64
        ):
            return False
        chain_cache = get_global_chain_cache()
        cached = chain_cache.get(run_id, expected_chain)
        if cached is not None:
            return cached
        run_dir = self.runtime_root / "runs" / run_id
        event_dir, payload_dir = run_dir / "learning_events", run_dir / "ledger_payloads"
        if any(is_link_or_reparse(path) for path in (run_dir, event_dir, payload_dir)):
            chain_cache.set(run_id, expected_chain, False)
            return False
        paths = sorted(event_dir.glob("*.json"), key=lambda p: (int(p.name.split("-", 1)[0]) if p.name.split("-", 1)[0].isdigit() else 0, p.name))
        if len(paths) < count:
            chain_cache.set(run_id, expected_chain, False)
            return False
        events: list[dict[str, Any]] = []
        previous_span: str | None = None
        try:
            for path in paths[:count]:
                event = read_json(path)
                if (
                    event.get("run_id") != run_id
                    or event.get("trace_id") != trace_id
                    or event.get("parent_span_id") != previous_span
                ):
                    chain_cache.set(run_id, expected_chain, False)
                    return False
                payload_path = payload_dir / event["payload_reference"].rsplit("/", 1)[-1]
                if digest(read_json(payload_path)) != event.get("payload_digest"):
                    chain_cache.set(run_id, expected_chain, False)
                    return False
                events.append(event)
                previous_span = event.get("span_id")
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            chain_cache.set(run_id, expected_chain, False)
            return False
        chain = digest(
            [
                {
                    "event_id": event["event_id"],
                    "canonical_digest": event["canonical_digest"]["value"],
                    "payload_digest": event["payload_digest"],
                }
                for event in events
            ]
        )
        is_valid = chain == expected_chain
        chain_cache.set(run_id, expected_chain, is_valid)
        return is_valid

    def _find_best_match(
        self, vector: list[float], candidates: list[sqlite3.Row]
    ) -> tuple[float, sqlite3.Row] | None:
        best: tuple[float, sqlite3.Row] | None = None
        valid_rows = [row for row in candidates if self._source_prefix_is_valid(row)]
        if not valid_rows:
            return None

        query_dim = len(vector)
        dim_matched_rows = [
            r
            for r in valid_rows
            if r["dimensions"] == query_dim and len(r["vector"]) == query_dim * 4
        ]
        if not dim_matched_rows:
            return None

        all_bytes = b"".join(r["vector"] for r in dim_matched_rows)
        matrix = np.frombuffer(all_bytes, dtype=np.dtype("<f4")).reshape(
            (len(dim_matched_rows), query_dim)
        )
        if not np.all(np.isfinite(matrix)):
            raise ValueError("semantic cache candidate vector is non-finite")
        q_arr = np.array(vector, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_arr))
        if q_norm > 0:
            row_norms = np.linalg.norm(matrix, axis=1)
            valid_norm_mask = row_norms > 0
            dots = matrix @ q_arr
            sims = np.where(
                valid_norm_mask,
                dots / (row_norms * q_norm),
                -1.0,
            )
            for idx, sim_val in enumerate(sims):
                sim_float = float(sim_val)
                row_item = dim_matched_rows[idx]
                if (
                    best is None
                    or sim_float > best[0]
                    or (sim_float == best[0] and row_item["entry_id"] < best[1]["entry_id"])
                ):
                    best = (sim_float, row_item)
        return best

    def lookup(
        self,
        *,
        context: Mapping[str, Any],
        intent_vector: list[float],
        current_run_id: str | None = None,
        as_of: Any = None,
    ) -> SemanticCacheLookup:
        if not self.can_read:
            reason = self._disabled_reason("READ")
            return SemanticCacheLookup(mode=self.mode, hit=False, reason=reason)
        if context.get("use_case") not in SUPPORTED_SEMANTIC_NAMESPACES:
            return SemanticCacheLookup(mode=self.mode, hit=False, reason="CACHE_CONTEXT_INVALID")
        try:
            vector = [float(value) for value in intent_vector]
            _pack_vector(vector)
            compatible = self.compatibility(context)
            compatibility_digest = digest(compatible)
            now = utc_now()
            policy = self.policy
            assert policy is not None
            connection = self._connection()
            rows = connection.execute(
                """SELECT entry_id,dimensions,vector,output_json,output_digest,outcome,
                          source_run_id,source_trace_id,source_event_count,
                          source_event_chain_digest
                   FROM semantic_cache_entry
                   WHERE state='ACTIVE' AND use_case=? AND compatibility_digest=?
                     AND replay_eligible=1 AND expires_at>=?
                   ORDER BY created_at DESC,entry_id ASC LIMIT ?""",
                (context["use_case"], compatibility_digest, now, policy.max_candidates),
            ).fetchall()

            best = self._find_best_match(vector, rows)

            if best is None or best[0] < policy.similarity_threshold:
                return SemanticCacheLookup(
                    mode=self.mode,
                    hit=False,
                    reason="CACHE_MISS_NO_COMPATIBLE_INTENT",
                    similarity=(round(best[0], 12) if best is not None else None),
                )
            similarity, row = best
            output = json.loads(row["output_json"])
            if not isinstance(output, dict) or digest(output) != row["output_digest"]:
                return SemanticCacheLookup(
                    mode=self.mode,
                    hit=False,
                    reason="CACHE_MISS_INVALID_OUTPUT",
                    entry_id=row["entry_id"],
                    similarity=round(similarity, 12),
                )

            # W3 temporal staleness validation
            earliest_stale = extract_earliest_stale_after(output)
            if earliest_stale and is_temporally_stale(earliest_stale, as_of):
                return SemanticCacheLookup(
                    mode=self.mode,
                    hit=False,
                    reason="CACHE_MISS_W3_TEMPORAL_EXPIRED",
                    entry_id=row["entry_id"],
                    similarity=round(similarity, 12),
                )

            connection.execute(
                "UPDATE semantic_cache_entry SET accessed_at=? WHERE entry_id=?",
                (now, row["entry_id"]),
            )
            connection.commit()

            if current_run_id:
                output = sanitize_output_for_cache_replay(
                    output, current_run_id, source_run_id=row["source_run_id"]
                )

            return SemanticCacheLookup(
                mode=self.mode,
                hit=True,
                reason="CACHE_HIT",
                entry_id=row["entry_id"],
                similarity=round(similarity, 12),
                output=output,
                outcome=row["outcome"],
                source_run_id=row["source_run_id"],
                source_trace_id=row["source_trace_id"],
                source_output_digest=row["output_digest"],
            )
        except (
            AssertionError,
            KeyError,
            OSError,
            OverflowError,
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            return SemanticCacheLookup(
                mode=self.mode,
                hit=False,
                reason=f"CACHE_LOOKUP_ERROR:{type(exc).__name__}",
            )
        finally:
            if "connection" in locals():
                connection.close()

    def stage(
        self, *, context: Mapping[str, Any], intent_digest: str, intent_vector: list[float],
        output: Mapping[str, Any], outcome: str, source_run_id: str,
        source_trace_id: str, replay_eligible: bool, stale_after: str | None = None,
    ) -> SemanticCacheWriteResult:
        if not self.can_write:
            return SemanticCacheWriteResult(status="SKIPPED", reason=self._disabled_reason("WRITE"))
        policy = self.policy
        assert policy is not None
        if outcome not in policy.cacheable_outcomes:
            return SemanticCacheWriteResult(status="SKIPPED", reason="CACHE_OUTCOME_NOT_CACHEABLE")
        if context.get("use_case") not in SUPPORTED_SEMANTIC_NAMESPACES:
            return SemanticCacheWriteResult(status="SKIPPED", reason="CACHE_CONTEXT_INVALID")
        try:
            compatible = self.compatibility(context)
            serialized_output = dict(output)
            output_digest, compatibility_digest = digest(serialized_output), digest(compatible)
            packed = _pack_vector(intent_vector)
            entry_id = stable_id(
                "semantic-cache", context["use_case"], compatibility_digest,
                intent_digest, output_digest, outcome, length=28,
            )
            created = utc_now()
            expires = (
                dt.datetime.fromisoformat(created.replace("Z", "+00:00")) + dt.timedelta(seconds=policy.ttl_seconds)
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            effective_stale = stale_after or extract_earliest_stale_after(output)
            if effective_stale and effective_stale < expires:
                expires = effective_stale
            with _CACHE_WRITE_LOCK:
                connection = self._connection()
                expire_sql = "DELETE FROM semantic_cache_entry WHERE expires_at < ?"
                connection.execute(expire_sql, (created,))
                connection.execute(
                    """INSERT INTO semantic_cache_entry
                       (entry_id,state,use_case,compatibility_digest,intent_digest,dimensions,vector,
                        output_json,output_digest,outcome,replay_eligible,source_run_id,source_trace_id,
                        source_event_count,source_event_chain_digest,created_at,activated_at,expires_at,accessed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(entry_id) DO UPDATE SET state='PENDING',
                        source_run_id=excluded.source_run_id,source_trace_id=excluded.source_trace_id,
                        source_event_count=NULL,source_event_chain_digest=NULL,
                        created_at=excluded.created_at,activated_at=NULL,expires_at=excluded.expires_at""",
                    (
                        entry_id, "PENDING", context["use_case"], compatibility_digest, intent_digest,
                        len(intent_vector), packed, json.dumps(serialized_output, sort_keys=True, ensure_ascii=False),
                        output_digest, outcome, int(replay_eligible), source_run_id, source_trace_id,
                        None, None, created, None, expires, None,
                    ),
                )
                connection.commit()
            return SemanticCacheWriteResult(
                status="PENDING", reason="CACHE_WRITE_STAGED",
                entry=PendingSemanticCacheEntry(entry_id, outcome, replay_eligible),
            )
        except (OSError, OverflowError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            return SemanticCacheWriteResult(status="ERROR", reason=f"CACHE_WRITE_ERROR:{type(exc).__name__}")
        finally:
            if "connection" in locals():
                connection.close()

    def activate(
        self,
        entry: PendingSemanticCacheEntry,
        *,
        source_event_count: int,
        source_event_chain_digest: str,
    ) -> SemanticCacheWriteResult:
        if not self.can_write:
            return SemanticCacheWriteResult(status="SKIPPED", reason=self._disabled_reason("WRITE"))
        try:
            if (
                source_event_count < 1
                or len(source_event_chain_digest) != 64
                or any(char not in "0123456789abcdef" for char in source_event_chain_digest)
            ):
                raise ValueError("cache activation requires a valid sealed event prefix")
            with _CACHE_WRITE_LOCK:
                connection = self._connection()
                row = connection.execute(
                    "SELECT state FROM semantic_cache_entry WHERE entry_id=?",
                    (entry.entry_id,),
                ).fetchone()
                if row is None or row["state"] != "PENDING":
                    return SemanticCacheWriteResult(
                        status="ERROR", reason="CACHE_PENDING_ENTRY_MISSING"
                    )
                cur = connection.execute(
                    """UPDATE semantic_cache_entry
                       SET state='ACTIVE',source_event_count=?,source_event_chain_digest=?,
                           activated_at=?
                       WHERE entry_id=? AND state='PENDING'""",
                    (
                        source_event_count,
                        source_event_chain_digest,
                        utc_now(),
                        entry.entry_id,
                    ),
                )
                if cur.rowcount == 0:
                    return SemanticCacheWriteResult(
                        status="ERROR", reason="CACHE_PENDING_ENTRY_MISSING"
                    )
                connection.commit()
            return SemanticCacheWriteResult(
                status="ACTIVE", reason="CACHE_WRITE_ACTIVATED", entry=entry
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            return SemanticCacheWriteResult(
                status="ERROR", reason=f"CACHE_ACTIVATION_ERROR:{type(exc).__name__}"
            )
        finally:
            if "connection" in locals():
                connection.close()

    def prune_expired(self, now: str | None = None) -> int:
        """Purge entries whose TTL has expired to prevent unbounded database bloat."""
        if not self.path.is_file():
            return 0
        timestamp = now or utc_now()
        connection = self._connection()
        try:
            cursor = connection.execute(
                "DELETE FROM semantic_cache_entry WHERE expires_at < ?",
                (timestamp,),
            )
            deleted_count = cursor.rowcount if cursor.rowcount >= 0 else 0
            connection.commit()
            return deleted_count
        except (OSError, sqlite3.Error):
            return 0
        finally:
            connection.close()

    def status(self) -> dict[str, Any]:
        counts = {"PENDING": 0, "ACTIVE": 0}
        error: str | None = None
        if self.path.is_file():
            try:
                connection = self._connection()
                counts.update(
                    {
                        row["state"]: row["entry_count"]
                        for row in connection.execute(
                            """SELECT state,COUNT(*) AS entry_count
                               FROM semantic_cache_entry GROUP BY state"""
                        )
                    }
                )
            except (OSError, sqlite3.Error, ValueError) as exc:
                error = f"CACHE_STATUS_ERROR:{type(exc).__name__}"
            finally:
                if "connection" in locals():
                    connection.close()
        return {
            "status": "PASS" if error is None else "DEGRADED",
            "mode": self.mode,
            "config_source": self.config_source,
            "config_path": str(self.config_path),
            "policy_id": self.policy.policy_id if self.policy else None,
            "policy_version": self.policy.policy_version if self.policy else None,
            "policy_digest": self.policy_digest,
            "health_reason": error or self.health_reason,
            "embedding_binding": self.embedding_binding,
            "last_binding_result": self.last_binding_result,
            "entry_counts": counts,
            "database_path": str(self.path),
        }
