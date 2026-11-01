#!/usr/bin/env python3
# ruff: noqa: E402
"""Run a local routed retrieval smoke check through a reusable worker.

This is deliberately *not* a Decision Intelligence end-to-end entry point.
It exercises routing plus approved-lane retrieval only; it does not invoke
``QueryEngine`` or the U0/L1/C0/L2D/PA/L2/X stage chain.  The client owns the
request deadline and terminates an unresponsive BGE worker instead of leaving
a stalled process behind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.index import HybridRetriever, get_backend
from dual_pipeline.query_router import (
    DestinationDecision,
    QueryDestination,
    route_query,
)

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
TIMEOUT_EXIT_CODE = 124
WORKER_PROTOCOL = "dev-retrieval-smoke-worker-v1"
RETRIEVAL_SMOKE_STATUS = "RETRIEVAL_SMOKE_COMPLETED"
RETRIEVAL_SMOKE_SCOPE = "RETRIEVAL_SMOKE_ONLY"
WORKER_HOST = os.environ.get("NEE_DEFAULT_HOST", "127.0.0.1")
WORKER_PORT_BASE = 35000
WORKER_PORT_SPAN = 20000
WORKER_START_POLL_SECONDS = 0.05


class WorkerTimeout(RuntimeError):
    """The client deadline expired while starting or awaiting its local worker."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"local worker timed out during {phase}")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--sidecar",
        type=Path,
        help="retrieval SQLite sidecar; required only for graph or DI routes",
    )
    command.add_argument(
        "--model-path",
        type=Path,
        help="pinned BGE-M3 snapshot; required only for graph or DI routes",
    )
    command.add_argument(
        "--model-manifest",
        type=Path,
        help="BGE-M3 snapshot manifest; required only for graph or DI routes",
    )
    command.add_argument(
        "--model-revision",
        help="BGE-M3 revision; required only for graph or DI routes",
    )
    command.add_argument("--question")
    command.add_argument("--audience", default="internal_consulting")
    command.add_argument("--classification", default="internal")
    command.add_argument("--top-k", type=_positive_int, default=10)
    command.add_argument(
        "--timeout-seconds",
        type=_timeout_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "hard wall-clock limit covering local-worker startup and the request "
            f"(1-{MAX_TIMEOUT_SECONDS}; default: {DEFAULT_TIMEOUT_SECONDS})"
        ),
    )
    command.add_argument(
        "--stop-worker",
        action="store_true",
        help="stop the reusable worker for this exact model and sidecar",
    )
    command.add_argument("--_serve", action="store_true", help=argparse.SUPPRESS)
    return command


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _timeout_seconds(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must not exceed {MAX_TIMEOUT_SECONDS} seconds"
        )
    return parsed


def _require_retrieval_arguments(
    command: argparse.ArgumentParser, args: argparse.Namespace, destination: QueryDestination
) -> None:
    missing = [
        option
        for option, value in (
            ("--sidecar", args.sidecar),
            ("--model-path", args.model_path),
            ("--model-manifest", args.model_manifest),
            ("--model-revision", args.model_revision),
        )
        if value is None
    ]
    if missing:
        command.error(f"{' '.join(missing)} required for {destination.value} retrieval route")


def _route_output(question: str) -> tuple[DestinationDecision, dict[str, object]]:
    route = route_query(question)
    return route, {
        "destination": route.destination.value,
        "query_type": route.query_type,
        "materiality": route.materiality,
        "reason_codes": list(route.reason_codes),
    }


def _worker_identity(args: argparse.Namespace) -> str:
    """Bind a reusable process to exactly one model snapshot and sidecar."""

    binding = {
        "protocol": WORKER_PROTOCOL,
        "sidecar": str(args.sidecar.resolve()),
        "model_path": str(args.model_path.resolve()),
        "model_manifest": str(args.model_manifest.resolve()),
        "model_revision": args.model_revision,
    }
    encoded = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _worker_address(identity: str) -> tuple[str, int]:
    return WORKER_HOST, WORKER_PORT_BASE + (int(identity[:8], 16) % WORKER_PORT_SPAN)


def _worker_authkey(identity: str) -> bytes:
    return hashlib.sha256(f"{WORKER_PROTOCOL}:{identity}".encode("utf-8")).digest()


def _worker_state_path(identity: str) -> Path:
    return Path(tempfile.gettempdir()) / "nee-dev-retrieval-smoke" / f"{identity}.json"


def _worker_log_path(identity: str) -> Path:
    return Path(tempfile.gettempdir()) / "nee-dev-retrieval-smoke" / f"{identity}.log"


def _read_worker_state(identity: str) -> dict[str, object] | None:
    path = _worker_state_path(identity)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("identity") != identity:
        return None
    return value


def _write_worker_state(identity: str, *, pid: int) -> None:
    path = _worker_state_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = {
        "protocol": WORKER_PROTOCOL,
        "identity": identity,
        "pid": pid,
        "address": list(_worker_address(identity)),
    }
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _connect_worker(identity: str):
    return Client(_worker_address(identity), authkey=_worker_authkey(identity))


def _worker_ready(identity: str) -> bool:
    try:
        connection = _connect_worker(identity)
    except (ConnectionRefusedError, OSError, EOFError):
        return False
    try:
        connection.send({"operation": "ping"})
        if not connection.poll(1):
            return False
        response = connection.recv()
        return bool(
            isinstance(response, dict)
            and response.get("status") == "READY"
            and response.get("identity") == identity
        )
    except (EOFError, OSError):
        return False
    finally:
        connection.close()


def _server_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-E",
        str(Path(__file__).resolve()),
        "--_serve",
        "--sidecar",
        str(args.sidecar),
        "--model-path",
        str(args.model_path),
        "--model-manifest",
        str(args.model_manifest),
        "--model-revision",
        str(args.model_revision),
    ]


def _start_worker(args: argparse.Namespace, identity: str) -> None:
    """Start a background worker; races safely converge on one bound listener."""

    log_path = _worker_log_path(identity)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("ab") as log_file:
        subprocess.Popen(
            _server_command(args),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )


def _terminate_worker(state: dict[str, object] | None, identity: str) -> None:
    """Terminate only the worker recorded for this exact local binding."""

    if state is None or state.get("identity") != identity:
        return
    pid = state.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return


def _wait_for_worker(args: argparse.Namespace, deadline: float) -> dict[str, object]:
    identity = _worker_identity(args)
    state = _read_worker_state(identity)
    if _worker_ready(identity):
        return state or {"identity": identity}

    _start_worker(args, identity)
    while time.monotonic() < deadline:
        if _worker_ready(identity):
            return _read_worker_state(identity) or {"identity": identity}
        time.sleep(WORKER_START_POLL_SECONDS)
    state = _read_worker_state(identity)
    _terminate_worker(state, identity)
    raise WorkerTimeout("worker_start")


def _execute_retrieval(
    *,
    question: str,
    audience: str,
    classification: str,
    top_k: int,
    backend: object,
    retriever: HybridRetriever,
) -> dict[str, object]:
    route, route_output = _route_output(question)
    if not route.enters_query_runtime:
        return {"status": "ROUTED", "route": route_output}
    retrieval = retriever.retrieve(
        question,
        ["approved"],
        classification=classification,
        audiences=[audience],
        retrieval_mode="factual",
    )
    results = [
        {
            "record_id": item["record_id"],
            "lane": item["lane"],
            "rank": item["fused_rank"],
            "score": item["fused_score"],
            "text": item["record"]["retrieval_text"],
        }
        for item in retrieval["lanes"]["approved"]["results"][:top_k]
    ]
    result: dict[str, object] = {
        "status": RETRIEVAL_SMOKE_STATUS,
        "tool_scope": RETRIEVAL_SMOKE_SCOPE,
        "query": question,
        "route": route_output,
        "backend": backend.backend_id,
        "result_count": len(results),
        "results": results,
    }
    return result


def _serve_request(
    request: object,
    *,
    backend: object,
    retriever: HybridRetriever,
) -> dict[str, object]:
    if not isinstance(request, dict):
        return {"status": "ERROR", "reason_code": "WORKER_REQUEST_INVALID"}
    if request.get("operation") == "ping":
        return {"status": "READY"}
    if request.get("operation") != "query":
        return {"status": "ERROR", "reason_code": "WORKER_OPERATION_INVALID"}
    question = request.get("question")
    audience = request.get("audience")
    classification = request.get("classification")
    top_k = request.get("top_k")
    if (
        not isinstance(question, str)
        or not isinstance(audience, str)
        or not isinstance(classification, str)
        or isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or top_k < 1
    ):
        return {"status": "ERROR", "reason_code": "WORKER_REQUEST_INVALID"}
    return _execute_retrieval(
        question=question,
        audience=audience,
        classification=classification,
        top_k=top_k,
        backend=backend,
        retriever=retriever,
    )


def _serve(args: argparse.Namespace) -> int:
    """Own one BGE-M3 instance and answer local client requests serially."""

    identity = _worker_identity(args)
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path=args.model_path,
        model_revision=args.model_revision,
        model_manifest=args.model_manifest,
    )
    retriever = HybridRetriever(args.sidecar, backend)
    try:
        listener = Listener(_worker_address(identity), authkey=_worker_authkey(identity))
    except OSError:
        # Another simultaneous client won the worker-start race.
        return 0
    _write_worker_state(identity, pid=os.getpid())
    try:
        while True:
            connection = listener.accept()
            try:
                request = connection.recv()
                response = _serve_request(request, backend=backend, retriever=retriever)
                if response.get("status") == "READY":
                    response["identity"] = identity
                connection.send(response)
            except (EOFError, OSError):
                pass
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                try:
                    connection.send(
                        {
                            "status": "ERROR",
                            "reason_code": "WORKER_EXECUTION_ERROR",
                            "error_type": type(exc).__name__,
                        }
                    )
                except (EOFError, OSError):
                    pass
            finally:
                connection.close()
    finally:
        listener.close()


def _submit_to_worker(
    args: argparse.Namespace,
    *,
    deadline: float,
) -> dict[str, object]:
    identity = _worker_identity(args)
    state = _wait_for_worker(args, deadline)
    try:
        connection = _connect_worker(identity)
        connection.send(
            {
                "operation": "query",
                "question": args.question,
                "audience": args.audience,
                "classification": args.classification,
                "top_k": args.top_k,
            }
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not connection.poll(remaining):
            _terminate_worker(state, identity)
            raise WorkerTimeout("request")
        response = connection.recv()
    except (ConnectionRefusedError, EOFError, OSError) as exc:
        raise RuntimeError("local worker disconnected before returning a response") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    if not isinstance(response, dict):
        raise RuntimeError("local worker returned an invalid response")
    return response


def _run_with_timeout(args: argparse.Namespace, route_output: dict[str, object]) -> int:
    deadline = time.monotonic() + args.timeout_seconds
    try:
        response = _submit_to_worker(args, deadline=deadline)
    except WorkerTimeout as exc:
        print(
            json.dumps(
                {
                    "status": "TIMEOUT",
                    "reason_code": "QUERY_EXECUTION_TIMEOUT",
                    "timeout_seconds": args.timeout_seconds,
                    "timeout_phase": exc.phase,
                    "route": route_output,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return TIMEOUT_EXIT_CODE
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "reason_code": "QUERY_WORKER_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "route": route_output,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    if (
        response.get("status") != RETRIEVAL_SMOKE_STATUS
        or response.get("tool_scope") != RETRIEVAL_SMOKE_SCOPE
        or "decision" in response
    ):
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "reason_code": "RETRIEVAL_SMOKE_CONTRACT_VIOLATION",
                    "route": route_output,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _stop_worker(args: argparse.Namespace) -> int:
    identity = _worker_identity(args)
    state = _read_worker_state(identity)
    _terminate_worker(state, identity)
    print(
        json.dumps(
            {
                "status": "STOPPED" if state is not None else "NOT_RUNNING",
                "worker_identity": identity,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    command = parser()
    args = command.parse_args()
    if args._serve or args.stop_worker:
        _require_retrieval_arguments(command, args, QueryDestination.NEE_DECISION_INTELLIGENCE)
        return _serve(args) if args._serve else _stop_worker(args)
    if not args.question:
        command.error("--question is required unless --stop-worker is selected")

    route, route_output = _route_output(args.question)
    if not route.enters_query_runtime:
        print(json.dumps({"status": "ROUTED", "route": route_output}, indent=2, sort_keys=True))
        return 0
    _require_retrieval_arguments(command, args, route.destination)
    return _run_with_timeout(args, route_output)


if __name__ == "__main__":
    raise SystemExit(main())
