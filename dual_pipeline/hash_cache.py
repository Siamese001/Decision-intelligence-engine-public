"""Generational hash cache for file digests with persistent HMAC-signed verdicts.

Provides thread-safe, metadata-bound (inode + mtime_ns + size + manifest_digest)
caching of file SHA-256 digests. Persists signed verdicts under .local/cache/ to eliminate
repeated synchronous disk hashing across CLI processes while detecting any adversarial tampering.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from pathlib import Path
from typing import NamedTuple

from config.credentials import CredentialManager
from dual_pipeline.core import sha256_file
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root

logger = logging.getLogger("dual_pipeline.hash_cache")


class _CacheKey(NamedTuple):
    resolved_path: str
    inode: int
    mtime_ns: int
    size_bytes: int
    manifest_digest: str = ""


def _compute_verdict_hmac(
    key: bytes,
    path: str,
    inode: int,
    mtime_ns: int,
    size_bytes: int,
    manifest_digest: str,
    sha256_val: str,
) -> str:
    msg = f"{path}:{inode}:{mtime_ns}:{size_bytes}:{manifest_digest}:{sha256_val}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


class GenerationalHashCache:
    """Thread-safe mtime/inode/size-bound hash cache with persistent signed verdicts."""

    def __init__(
        self, cache_file: Path | str | None = None, key_file: Path | str | None = None
    ) -> None:
        self._lock = threading.Lock()
        self._cache: dict[_CacheKey, str] = {}
        self._hits = 0
        self._misses = 0
        self._tamper_detected = False

        if cache_file is not None:
            self._cache_file: Path | None = Path(cache_file).resolve()
        elif CredentialManager.has_credential("PYTEST_CURRENT_TEST") or CredentialManager.get_credential("MOCK_ENV") == "true":
            self._cache_file = None
        else:
            try:
                rt = resolve_authoritative_runtime_root()
                self._cache_file = (rt.parent / "cache" / "bge_m3_hash_verdicts.json").resolve()
            except Exception:
                self._cache_file = None

        if key_file is not None:
            self._key_file: Path | None = Path(key_file).resolve()
        elif self._cache_file is not None:
            self._key_file = (self._cache_file.parent / ".hash_verdict_hmac_key").resolve()
        else:
            self._key_file = None

        self._hmac_key = self._load_or_create_key()
        self._load_persistent()

    def _load_or_create_key(self) -> bytes:
        if self._key_file is None:
            return os.urandom(32)
        try:
            if self._key_file.is_file():
                return self._key_file.read_bytes()
            self._key_file.parent.mkdir(parents=True, exist_ok=True)
            key = os.urandom(32)
            self._key_file.write_bytes(key)
            with os.fdopen(os.open(self._key_file, os.O_WRONLY, 0o600)):
                pass
            return key
        except Exception:
            return os.urandom(32)

    def _load_persistent(self) -> None:
        if self._cache_file is None or not self._cache_file.is_file():
            return
        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
            entries = data.get("verdicts", [])
            for entry in entries:
                path = entry.get("path")
                inode = entry.get("inode")
                mtime_ns = entry.get("mtime_ns")
                size_bytes = entry.get("size_bytes")
                manifest_digest = entry.get("manifest_digest", "")
                sha256_val = entry.get("sha256")
                sig = entry.get("hmac")
                if not all(
                    [
                        path,
                        isinstance(inode, int),
                        isinstance(mtime_ns, int),
                        isinstance(size_bytes, int),
                        sha256_val,
                        sig,
                    ]
                ):
                    self._tamper_detected = True
                    continue
                expected_sig = _compute_verdict_hmac(
                    self._hmac_key, path, inode, mtime_ns, size_bytes, manifest_digest, sha256_val
                )
                if hmac.compare_digest(sig, expected_sig):
                    key = _CacheKey(path, inode, mtime_ns, size_bytes, manifest_digest)
                    self._cache[key] = sha256_val
                else:
                    self._tamper_detected = True
        except Exception:
            self._tamper_detected = True

    def _flush_persistent(self) -> None:
        if self._cache_file is None:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            verdicts = []
            for key, sha256_val in self._cache.items():
                sig = _compute_verdict_hmac(
                    self._hmac_key,
                    key.resolved_path,
                    key.inode,
                    key.mtime_ns,
                    key.size_bytes,
                    key.manifest_digest,
                    sha256_val,
                )
                verdicts.append(
                    {
                        "path": key.resolved_path,
                        "inode": key.inode,
                        "mtime_ns": key.mtime_ns,
                        "size_bytes": key.size_bytes,
                        "manifest_digest": key.manifest_digest,
                        "sha256": sha256_val,
                        "hmac": sig,
                    }
                )
            payload = {"schema_version": "1.0.0", "verdicts": verdicts}
            tmp = self._cache_file.with_name(f".{self._cache_file.name}.tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self._cache_file)
        except Exception as exc:
            logger.debug("Failed to flush hash cache to %s: %s", self._cache_file, exc)

    def get(self, path: Path | str, manifest_digest: str | None = None) -> str:
        """Get or compute the SHA-256 digest of a file with persistent HMAC validation."""
        resolved = Path(path).resolve()
        stat = resolved.stat()
        key = _CacheKey(
            resolved_path=str(resolved),
            inode=stat.st_ino,
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
            manifest_digest=manifest_digest or "",
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._hits += 1
                return cached

        # Compute digest outside lock to avoid blocking other threads during I/O
        digest_val = sha256_file(resolved)

        with self._lock:
            self._cache[key] = digest_val
            self._misses += 1
            self._flush_persistent()
            return digest_val

    def invalidate(self, path: Path | str | None = None) -> None:
        """Evict cached hashes for a specific path, or all cached hashes."""
        with self._lock:
            if path is None:
                self._cache.clear()
            else:
                target = str(Path(path).resolve())
                keys_to_remove = [k for k in self._cache if k.resolved_path == target]
                for k in keys_to_remove:
                    self._cache.pop(k, None)
            self._flush_persistent()

    def size(self) -> int:
        """Return number of cached entries."""
        with self._lock:
            return len(self._cache)

    @property
    def hits(self) -> int:
        """Return number of cache hits."""
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        """Return number of cache misses."""
        with self._lock:
            return self._misses

    @property
    def tamper_detected(self) -> bool:
        """Return True if invalid or tampered signatures were encountered during load."""
        return self._tamper_detected

    def reset_metrics(self) -> None:
        """Reset hit and miss counters."""
        with self._lock:
            self._hits = 0
            self._misses = 0


_GLOBAL_HASH_CACHE = GenerationalHashCache()
HashCache = GenerationalHashCache


def get_global_hash_cache() -> GenerationalHashCache:
    """Return the global GenerationalHashCache singleton."""
    return _GLOBAL_HASH_CACHE


def cached_sha256_file(path: Path | str, manifest_digest: str | None = None) -> str:
    """Convenience function: return SHA-256 digest using the global hash cache."""
    return _GLOBAL_HASH_CACHE.get(path, manifest_digest=manifest_digest)


__all__ = [
    "GenerationalHashCache",
    "HashCache",
    "cached_sha256_file",
    "get_global_hash_cache",
]
