"""Bounded HTTP transport for the canonical Decision Intelligence service."""

from __future__ import annotations

import json
import os
import signal
import threading
from concurrent.futures import Future, TimeoutError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import unquote, urlsplit

from storage.connection import create_sqlite_conn

from .core import read_json
from .credentials import CredentialManager
from .decision_service_core.request_queue import (
    BoundedRequestQueue, resolve_max_concurrency, resolve_max_queue_size,
)
from .hash_cache import cached_sha256_file
from .parameters import get_decision_service_parameters

MAX_REQUEST_BYTES = int(get_decision_service_parameters()["max_request_bytes"])
ROUTE_FIELDS = frozenset({
    "question", "query_type_override", "destination_override",
    "requested_materiality", "delivery_profile", "artifact_type",
})
QUERY_FIELDS = frozenset(
    {
        "question",
        "options",
        "criteria",
        "constraints",
        "assumptions",
        "time_horizon",
        "query_type",
        "destination",
        "materiality",
        "delivery_profile",
        "artifact_type",
        "user_id",
        "classification",
        "audience",
        "provider_egress_allowed",
        "card_id",
        "card_view_version",
        "card_candidate_review",
    }
)


class DecisionServiceProtocol(Protocol):
    def route(self, question: str, **kwargs: Any) -> dict[str, Any]: ...

    def query(self, question: str, **kwargs: Any) -> dict[str, Any]: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...


class DecisionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: DecisionServiceProtocol,
        *,
        max_concurrency: int | None = None,
        max_queue_size: int | None = None,
        request_timeout_seconds: float = 60.0,
        readiness_check: Callable[[], Mapping[str, Any] | bool] | None = None,
    ) -> None:
        if max_concurrency is None:
            max_concurrency = resolve_max_concurrency()
        if max_queue_size is None:
            max_queue_size = resolve_max_queue_size(max_concurrency)
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be non-negative")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        super().__init__(server_address, DecisionRequestHandler)
        self.service = service
        self.max_concurrency = max_concurrency
        self.max_queue_size = max_queue_size
        self.request_timeout_seconds = request_timeout_seconds
        self.readiness_check = readiness_check
        self.queue = BoundedRequestQueue(
            max_workers=max_concurrency,
            max_queue_size=max_queue_size,
            thread_name_prefix="nee-decision-api",
        )
        self.executor = self.queue._executor
        self.capacity = self.queue._capacity_sem
        self.is_shutting_down = threading.Event()

    def submit(self, action: Callable[[], dict[str, Any]]) -> Future[dict[str, Any]] | None:
        if self.is_shutting_down.is_set():
            return None
        return self.queue.submit(action)

    def readiness(self) -> tuple[bool, dict[str, Any]]:
        if self.readiness_check is None:
            return True, {"status": "READY"}
        result = self.readiness_check()
        if isinstance(result, Mapping):
            payload = dict(result)
            ready = payload.get("status") == "READY"
            return ready, payload
        return bool(result), {"status": "READY" if result else "NOT_READY"}

    def _resolve_health_db_target(self) -> str:
        svc_root = getattr(self.service, "runtime_root", None) or getattr(getattr(self.service, "_light_service", None), "runtime_root", None)
        if svc_root:
            root_path = Path(svc_root)
            for ap_file in (root_path / "active_pair.json", root_path.parent / "active_pair.json"):
                if ap_file.is_file():
                    ap_data = json.loads(ap_file.read_text(encoding="utf-8"))
                    return str((ap_file.parent / str(ap_data.get("graph_path", ""))).resolve())
        return ":memory:"

    def deep_health_check(self) -> tuple[bool, dict[str, Any]]:
        """Deep operational health check: thread pool, SQLite connectivity, and memory."""
        checks: dict[str, Any] = {}
        queue_stats = self.queue.stats()
        executor_active = not queue_stats["is_shutting_down"]
        checks["thread_pool"] = "OK" if executor_active else "SHUTDOWN"
        checks["queue"] = queue_stats

        sqlite_ok = False
        try:
            db_target = self._resolve_health_db_target()
            with create_sqlite_conn(db_target, readonly=(db_target != ":memory:")) as conn:
                res = conn.execute("SELECT 1").fetchone()
                if res and res[0] == 1:
                    sqlite_ok = True
            checks["sqlite"] = "OK"
            checks["sqlite_target"] = "active_graph" if db_target != ":memory:" else "memory"
        except Exception as exc:
            checks["sqlite"] = f"FAIL: {exc}"

        memory_ok = True
        try:
            import psutil
            vm = psutil.virtual_memory()
            checks["memory_percent"] = vm.percent
            checks["memory_available_mb"] = round(vm.available / (1024 * 1024), 2)
            memory_ok = vm.percent <= 95.0
            checks["memory"] = "OK" if memory_ok else "DEGRADED"
        except Exception:
            checks["memory"] = "UNKNOWN"

        all_ok = executor_active and sqlite_ok and memory_ok
        return all_ok, {"status": "HEALTHY" if all_ok else "DEGRADED", "checks": checks}

    def shutdown_gracefully(self, timeout: float = 5.0) -> None:
        """Signal shutdown, reject new requests, drain active requests, and close cleanly."""
        self.is_shutting_down.set()
        self.queue.shutdown(wait=True, cancel_futures=False)
        super().server_close()

    def server_close(self) -> None:
        super().server_close()
        self.queue.shutdown(wait=False, cancel_futures=True)


class DecisionRequestHandler(BaseHTTPRequestHandler):
    server: DecisionHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if content_length <= 0:
            raise ValueError("a JSON request body is required")
        if content_length > MAX_REQUEST_BYTES:
            raise OverflowError("request body exceeds the 1 MiB limit")
        try:
            decoded = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _validate_fields(payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"unsupported request fields: {unknown}")
        if not isinstance(payload.get("question"), str) or not payload["question"].strip():
            raise ValueError("question must be nonblank text")

    def _execute(self, action: Callable[[], dict[str, Any]]) -> None:
        future = self.server.submit(action)
        if future is None:
            self._write_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"status": "BUSY", "error": "maximum request concurrency reached"},
            )
            return
        try:
            result = future.result(timeout=self.server.request_timeout_seconds)
        except TimeoutError:
            cancellation_requested = future.cancel()
            self._write_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                {
                    "status": "TIMEOUT",
                    "error": "request deadline exceeded",
                    "cancellation_requested": cancellation_requested,
                },
            )
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "INVALID_REQUEST", "error": str(exc)},
            )
        except Exception as exc:
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": "internal request failure",
                },
            )
        else:
            self._write_json(HTTPStatus.OK, result)

    def do_GET(self) -> None:  # noqa: N802
        if self.server.is_shutting_down.is_set():
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "SHUTTING_DOWN", "error": "server is gracefully terminating"},
            )
            return
        path = urlsplit(self.path).path
        if path == "/health":
            healthy, payload = self.server.deep_health_check()
            self._write_json(HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE, payload)
            return
        if path == "/metrics":
            from dual_pipeline.telemetry_core.metrics import format_prometheus_metrics
            body = format_prometheus_metrics().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/ready":
            ready, payload = self.server.readiness()
            self._write_json(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE, payload)
            return
        prefix = "/v1/runs/"
        if path.startswith(prefix) and len(path) > len(prefix):
            run_id = unquote(path[len(prefix) :])
            if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "INVALID_REQUEST", "error": "run_id is invalid"},
                )
                return

            def get_run() -> dict[str, Any]:
                result = self.server.service.get_run(run_id)
                if result is None:
                    raise LookupError("run not found")
                return result

            future = self.server.submit(get_run)
            if future is None:
                self._write_json(HTTPStatus.TOO_MANY_REQUESTS, {"status": "BUSY"})
                return
            try:
                result = future.result(timeout=self.server.request_timeout_seconds)
            except LookupError:
                self._write_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"})
            except TimeoutError:
                self._write_json(HTTPStatus.GATEWAY_TIMEOUT, {"status": "TIMEOUT"})
            except Exception as exc:
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"status": "FAIL", "error_type": type(exc).__name__},
                )
            else:
                self._write_json(HTTPStatus.OK, result)
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.server.is_shutting_down.is_set():
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "SHUTTING_DOWN", "error": "server is gracefully terminating"},
            )
            return
        path = urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/v1/route":
                self._validate_fields(payload, ROUTE_FIELDS)
                question = payload.pop("question")
                self._execute(lambda: self.server.service.route(question, **payload))
                return
            if path == "/v1/query":
                self._validate_fields(payload, QUERY_FIELDS)
                question = payload.pop("question")

                def run_query_and_lint() -> dict[str, Any]:
                    res = self.server.service.query(question, **payload)
                    from .cli.engine import _apply_seamless_lineage_formatting
                    from .presentation_linter import PresentationLinter
                    _apply_seamless_lineage_formatting(res)
                    linter = PresentationLinter()
                    for val in (v for v in res.values() if isinstance(v, str)):
                        linter.assert_clean_presentation(val)
                    return res

                self._execute(run_query_and_lint)
                return
        except OverflowError as exc:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"status": "INVALID_REQUEST", "error": str(exc)},
            )
            return
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "INVALID_REQUEST", "error": str(exc)},
            )
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"status": "NOT_FOUND"})


def serve_decision_api(
    service: DecisionServiceProtocol,
    *,
    host: str | None = None,
    port: int = 8765,
    max_concurrency: int | None = None,
    max_queue_size: int | None = None,
    request_timeout_seconds: float = 60.0,
    readiness_check: Callable[[], Mapping[str, Any] | bool] | None = None,
) -> None:
    host_val = host or CredentialManager.get_credential("NEE_DEFAULT_HOST", "127.0.0.1")
    server = DecisionHTTPServer(
        (host_val, port),
        service,
        max_concurrency=max_concurrency,
        max_queue_size=max_queue_size,
        request_timeout_seconds=request_timeout_seconds,
        readiness_check=readiness_check,
    )

    def _sig_handler(signum: int, _frame: Any) -> None:
        server.shutdown_gracefully(timeout=5.0)

    try:
        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)
    except (ValueError, AttributeError):
        pass

    try:
        server.serve_forever()
    finally:
        server.server_close()


def runtime_readiness(runtime_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Report exact local pair bindings without initializing a model or provider."""

    root = Path(runtime_root).expanduser().resolve()
    active_path = root / "active_pair.json"
    pair: dict[str, Any] | None = None
    pair_error: str | None = None
    if not active_path.is_file():
        pair_error = "active_pair.json is missing"
    else:
        try:
            pair = read_json(active_path)
            graph_path = (root / str(pair["graph_path"])).resolve()
            sidecar_path = (root / str(pair["sidecar_path"])).resolve()
            graph_path.relative_to(root)
            sidecar_path.relative_to(root)
            if not graph_path.is_file() or not sidecar_path.is_file():
                pair_error = "active graph or sidecar is missing"
            elif cached_sha256_file(graph_path) != pair.get("graph_sha256"):
                pair_error = "active graph digest does not match"
            elif cached_sha256_file(sidecar_path) != pair.get("sidecar_sha256"):
                pair_error = "active sidecar digest does not match"
        except (KeyError, TypeError, ValueError) as exc:
            pair_error = f"active pair is invalid: {type(exc).__name__}"

    pair_ready = pair is not None and pair_error is None
    embedding_ready = bool(
        pair_ready
        and pair.get("embedding_backend")
        and pair.get("model_id")
        and pair.get("model_revision")
    )
    from dual_pipeline.credentials import CredentialManager
    provider_configured = CredentialManager.has_live_openai_key()
    return {
        "status": "READY" if pair_ready and embedding_ready else "NOT_READY",
        "service": {"status": "READY"},
        "runtime_pair": {
            "status": "READY" if pair_ready else "NOT_READY",
            "runtime_root": str(root),
            "generation_id": pair.get("generation_id") if pair else None,
            "graph_sha256": pair.get("graph_sha256") if pair else None,
            "sidecar_sha256": pair.get("sidecar_sha256") if pair else None,
            "production_qualified": pair.get("production_qualified") if pair else None,
            "error": pair_error,
        },
        "embedding": {
            "status": "READY" if embedding_ready else "NOT_READY",
            "backend": pair.get("embedding_backend") if pair else None,
            "model_id": pair.get("model_id") if pair else None,
            "model_revision": pair.get("model_revision") if pair else None,
        },
        "provider": {
            "status": (
                "CONFIGURED_UNVERIFIED" if provider_configured else "NOT_CONFIGURED"
            ),
            "provider": "openai",
        },
    }


__all__ = ["DecisionHTTPServer", "runtime_readiness", "serve_decision_api"]
