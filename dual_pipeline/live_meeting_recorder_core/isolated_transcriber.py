"""Live Meeting Recorder Core: Isolated MLX Whisper Worker Process on Apple Silicon Metal GPU."""
from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config.credentials import CredentialManager
from dual_pipeline.live_meeting_recorder_core.mlx_worker import (
    AirgapViolationError,
    AudioAnalysisError,
    MLXInitError,
    MLXReasonCode,
    MLXRuntimeLost,
    enforce_process_airgap,
    perform_mlx_readiness_probe,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.isolated_transcriber")
_enforce_process_airgap = enforce_process_airgap

__all__ = [
    "AirgapViolationError", "AudioAnalysisError", "IsolatedMLXTranscriberClient",
    "MLXInitError", "MLXReasonCode", "MLXRuntimeLost", "ReadinessFuture", "_enforce_process_airgap",
]


def _perform_worker_handshake(
    output_q: mp.Queue, default_model: Optional[str], expected_revision: Optional[str], mock_mode: bool,
) -> bool:
    """Configures environment and runs staged readiness probe. Returns True on success."""
    enforce_process_airgap()
    from config.credentials import ensure_ssot_env_loaded
    ensure_ssot_env_loaded()
    repo_root = Path(__file__).resolve().parent.parent.parent
    CredentialManager.prepend_path(str(repo_root / ".venv" / "bin"))
    try:
        receipt = perform_mlx_readiness_probe(model_name=default_model, expected_revision=expected_revision, mock_mode=mock_mode)
        from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import transcribe_meeting_chunk  # noqa: F401
        output_q.put(receipt)
        return True
    except MLXInitError as e:
        output_q.put(e.to_dict())
        return False
    except Exception as e:
        output_q.put({"type": "INIT_ERROR", "stage": "UNKNOWN", "reason_code": MLXReasonCode.MODEL_LOAD_FAILED, "detail": str(e)})
        return False


def _execute_chunk_transcription(
    msg: Dict[str, Any], output_q: mp.Queue, default_model: Optional[str], mock_mode: bool,
) -> None:
    """Invokes chunk transcriber inside worker process and enqueues result receipt."""
    task_id, wav_path_str = msg.get("task_id", ""), msg.get("wav_path", "")
    model_name = msg.get("model_name") or default_model
    t0 = time.perf_counter()
    try:
        from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import transcribe_meeting_chunk
        res = transcribe_meeting_chunk(
            wav_path=Path(wav_path_str), backend=msg.get("backend", "mlx"),
            whisper_prompt=msg.get("whisper_prompt"), model_name=model_name,
            mock_mode=msg.get("mock_mode", mock_mode),
            return_segments=True,
        )
        if isinstance(res, tuple):
            text, segments = res
        else:
            text, segments = res, []
        output_q.put({
            "type": "RESULT", "task_id": task_id, "wav_path": wav_path_str, "text": text,
            "segments": segments,
            "latency_ms": (time.perf_counter() - t0) * 1000.0, "worker_pid": os.getpid(), "status": "SUCCESS",
        })
    except Exception as e:
        output_q.put({
            "type": "RESULT", "task_id": task_id, "wav_path": wav_path_str, "text": "",
            "segments": [], "error": str(e),
            "latency_ms": (time.perf_counter() - t0) * 1000.0, "worker_pid": os.getpid(), "status": "ERROR",
        })


def _should_worker_exit(stop_event: mp.Event, orig_parent_pid: int) -> bool:
    """Checks stop event or parent death condition."""
    if stop_event.is_set():
        return True
    if os.getppid() != orig_parent_pid or os.getppid() <= 1:
        logger.warning("[ISOLATED_WORKER] Parent PID %s died; worker PID %s exiting.", orig_parent_pid, os.getpid())
        return True
    return False


def _process_worker_message(
    msg: Any, output_q: mp.Queue, default_model: Optional[str], mock_mode: bool,
) -> bool:
    """Processes a single worker message. Returns False if worker should stop."""
    if not isinstance(msg, dict):
        return True
    msg_type = msg.get("type")
    if msg_type == "STOP":
        return False
    if msg_type == "TRANSCRIBE":
        _execute_chunk_transcription(msg, output_q, default_model, mock_mode)
    return True


def _worker_entrypoint(
    input_q: mp.Queue, output_q: mp.Queue, stop_event: mp.Event, mock_mode: bool = False,
    default_model: Optional[str] = None, expected_revision: Optional[str] = None, parent_pid: Optional[int] = None,
) -> None:
    """Worker loop executing inside a dedicated OS process with staged readiness handshake."""
    import signal as _signal
    with contextlib.suppress(Exception):
        _signal.signal(_signal.SIGINT, _signal.SIG_IGN)
    if not _perform_worker_handshake(output_q, default_model, expected_revision, mock_mode):
        return
    orig_parent_pid = parent_pid if parent_pid is not None else os.getppid()
    while not _should_worker_exit(stop_event, orig_parent_pid):
        try:
            msg = input_q.get(timeout=0.2)
        except queue.Empty:
            continue
        except (KeyboardInterrupt, SystemExit):
            break
        if not _process_worker_message(msg, output_q, default_model, mock_mode):
            break
    output_q.put({"type": "STOPPED", "pid": os.getpid()})


class ReadinessFuture:
    """Future representing an asynchronous MLX proof-of-readiness probe."""

    def __init__(self, client: IsolatedMLXTranscriberClient, timeout: float = 60.0) -> None:
        self._client, self._timeout = client, timeout
        self._ready_event = threading.Event()
        self._receipt: Optional[Dict[str, Any]] = None
        self._error: Optional[Exception] = None
        self._thread = threading.Thread(target=self._waiter, daemon=True, name="MLXReadinessWaiter")
        self._thread.start()

    def _waiter(self) -> None:
        try:
            self._receipt = self._client._await_handshake(timeout=self._timeout)
        except Exception as e:
            self._error = e
        finally:
            self._ready_event.set()

    def is_done(self) -> bool:
        return self._ready_event.is_set()

    def result(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        wait_to = self._timeout if timeout is None else timeout
        if not self._ready_event.wait(timeout=wait_to):
            if self._client._process is not None and self._client._process.is_alive():
                logger.warning(
                    "MLX Whisper worker still compiling shaders after %ss; extending grace period by 60s...",
                    wait_to,
                )
                if self._ready_event.wait(timeout=60.0):
                    if self._receipt is not None:
                        return self._receipt
            raise TimeoutError(f"MLX Whisper worker failed to initialize within {wait_to}s. [{MLXReasonCode.MLX_READY_DEADLINE_EXCEEDED}]")
        if self._error is not None:
            raise self._error
        if self._receipt is None:
            raise RuntimeError("MLX Whisper readiness completed without producing a receipt.")
        return self._receipt


class IsolatedMLXTranscriberClient:
    """Client managing the lifecycle and IPC communication with the isolated MLX Whisper process."""

    def __init__(
        self,
        mock_mode: bool = False,
        default_model: Optional[str] = None,
        expected_revision: Optional[str] = None,
        backend: str = "mlx",
    ):
        self.mock_mode = mock_mode
        self.default_model = default_model
        self.expected_revision = expected_revision
        self.backend = backend
        self._ctx = mp.get_context("spawn")
        self._input_q: Optional[mp.Queue] = None
        self._output_q: Optional[mp.Queue] = None
        self._stop_event: Optional[mp.Event] = None
        self._process: Optional[mp.Process] = None
        self._worker_pid: Optional[int] = None
        self._task_counter: int = 0
        self._is_running: bool = False
        self._restart_count: int = 0
        self._terminal_failure: bool = False
        self._readiness_receipt: Optional[Dict[str, Any]] = None
        self._pending_results: Dict[str, Dict[str, Any]] = {}

    @property
    def is_running(self) -> bool:
        return self._is_running and self._process is not None and self._process.is_alive()

    @property
    def worker_pid(self) -> Optional[int]:
        return self._worker_pid

    @property
    def readiness_receipt(self) -> Optional[Dict[str, Any]]:
        return self._readiness_receipt

    def _process_handshake_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Processes a received handshake message or raises MLXInitError."""
        msg_type = msg.get("type")
        if msg_type == "READY" and msg.get("status") == "READY":
            self._worker_pid = msg.get("pid")
            self._readiness_receipt = msg
            self._is_running = True
            logger.info(
                "Isolated MLX Whisper worker active (PID: %s, parent PID: %s, load: %sms, golden: %sms)",
                self._worker_pid,
                os.getpid(),
                msg.get("load_ms"),
                msg.get("golden_ms"),
            )
            return msg
        if msg_type == "INIT_ERROR":
            self._terminal_failure = True
            self.stop()
            raise MLXInitError(
                stage=msg.get("stage", "UNKNOWN"), reason_code=msg.get("reason_code", MLXReasonCode.MODEL_LOAD_FAILED),
                detail=msg.get("detail", "Unknown worker initialization failure"),
            )
        self._terminal_failure = True
        self.stop()
        raise RuntimeError(f"Unexpected handshake from MLX worker: {msg}")

    def _await_handshake(self, timeout: float = 60.0) -> Dict[str, Any]:
        """Awaits and validates the READY handshake receipt or raises MLXInitError."""
        if self._output_q is None:
            raise RuntimeError("Cannot await handshake: output_q is uninitialized.")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, min(0.1, deadline - time.monotonic()))
            try:
                msg = self._output_q.get(timeout=remaining)
                return self._process_handshake_message(msg)
            except queue.Empty:
                if self._process is not None and not self._process.is_alive():
                    exitcode = self._process.exitcode
                    self._terminal_failure = True
                    self.stop()
                    raise MLXInitError(
                        stage="SPAWN", reason_code=MLXReasonCode.MLX_WORKER_DIED,
                        detail=f"MLX Whisper worker process terminated prematurely during startup handshake (exitcode={exitcode}).",
                    )

        self._terminal_failure = True
        self.stop()
        raise TimeoutError(f"MLX Whisper worker failed to initialize within {timeout}s. [{MLXReasonCode.MLX_READY_DEADLINE_EXCEEDED}]")

    @staticmethod
    def _ensure_valid_standard_streams() -> None:
        """Repairs closed or revoked standard file descriptors (0, 1, 2) to /dev/null before multiprocessing spawn."""
        for fd, flags in ((0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY)):
            try:
                os.fstat(fd)
            except OSError:
                devnull_fd = os.open(os.devnull, flags)
                if devnull_fd != fd:
                    os.dup2(devnull_fd, fd)
                    os.close(devnull_fd)

    def _spawn_process(self) -> None:
        """Initializes IPC queues and spawns the isolated worker process."""
        from config.credentials import ensure_ssot_env_loaded
        ensure_ssot_env_loaded()
        self._ensure_valid_standard_streams()
        self._input_q, self._output_q, self._stop_event = self._ctx.Queue(), self._ctx.Queue(), self._ctx.Event()
        self._process = self._ctx.Process(
            target=_worker_entrypoint, daemon=True,
            kwargs={
                "input_q": self._input_q, "output_q": self._output_q, "stop_event": self._stop_event,
                "mock_mode": self.mock_mode, "default_model": self.default_model,
                "expected_revision": self.expected_revision, "parent_pid": os.getpid(),
            },
        )
        self._process.start()

    def start(self, timeout: float = 60.0) -> Dict[str, Any]:
        """Spawns the isolated worker process and awaits the verified READY handshake."""
        if self.is_running:
            return self._readiness_receipt or {"status": "READY", "pid": self._worker_pid}
        self._spawn_process()
        return self._await_handshake(timeout=timeout)

    def start_async(self, timeout: float = 60.0) -> ReadinessFuture:
        """Spawns the isolated worker process asynchronously and returns a ReadinessFuture."""
        if self.is_running:
            fut = ReadinessFuture.__new__(ReadinessFuture)
            fut._client, fut._timeout, fut._ready_event = self, timeout, threading.Event()
            fut._ready_event.set()
            fut._receipt, fut._error = self._readiness_receipt, None
            return fut
        self._spawn_process()
        return ReadinessFuture(self, timeout=timeout)

    prewarm_async = start_async

    def _extract_task_result(self, res: Any, task_id: str, return_segments: bool = False) -> Any:
        """Validates result payload, stashes mismatches, and returns text (or text, segments) on match."""
        if not isinstance(res, dict):
            return None
        msg_task_id = res.get("task_id")
        if msg_task_id != task_id:
            if msg_task_id:
                self._pending_results[msg_task_id] = res
            logger.warning("Stashed mismatched MLX task result (expected %s, got %s)", task_id, msg_task_id)
            return None
        if res.get("status") == "ERROR":
            raise RuntimeError(
                f"Isolated MLX Whisper transcription failed: {res.get('error')}. Cloud fallback is prohibited."
            )
        text = res.get("text", "")
        if return_segments:
            return text, res.get("segments", [])
        return text

    def _poll_final_output(self, task_id: str, return_segments: bool) -> Any:
        if self._output_q is None:
            return None
        with contextlib.suppress(queue.Empty):
            return self._extract_task_result(self._output_q.get_nowait(), task_id, return_segments=return_segments)
        return None

    def _drain_and_await_result(self, task_id: str, timeout: float, return_segments: bool = False) -> Any:
        """Polls output queue for task result until deadline. Returns text or (text, segments) or None."""
        if task_id in self._pending_results:
            return self._extract_task_result(self._pending_results.pop(task_id), task_id, return_segments=return_segments)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._output_q is None:
                break
            if self._process is not None and not self._process.is_alive():
                return self._poll_final_output(task_id, return_segments)
            try:
                res = self._output_q.get(timeout=min(0.2, max(0.01, deadline - time.time())))
            except queue.Empty:
                continue
            matched = self._extract_task_result(res, task_id, return_segments=return_segments)
            if matched is not None:
                return matched
        return None

    def _handle_worker_death(
        self, wav_path: Path, whisper_prompt: Optional[str], model_name: Optional[str], timeout: float, return_segments: bool = False,
    ) -> Any:
        """Handles worker unexpected termination or timeout recovery."""
        if self._process is not None and not self._process.is_alive():
            exitcode = self._process.exitcode
            if exitcode == 12:
                self._terminal_failure = True
                self.stop()
                raise MLXRuntimeLost(
                    f"[{MLXReasonCode.MLX_RUNTIME_LOST}] MLX Whisper worker exited with quarantine status (exitcode=12); respawn prohibited."
                )
            if self._restart_count < 1:
                self._restart_count += 1
                logger.warning("MLX Whisper worker died; performing single governed restart (attempt %d/1)...", self._restart_count)
                self.stop()
                self.start(timeout=max(timeout, 60.0))
                return self.transcribe(wav_path, whisper_prompt=whisper_prompt, model_name=model_name, timeout=timeout, return_segments=return_segments)
            self._terminal_failure = True
            self.stop()
            raise MLXRuntimeLost(f"[{MLXReasonCode.MLX_RUNTIME_LOST}] MLX Whisper worker process terminated unexpectedly and exceeded maximum recovery attempts.")

        logger.warning("Isolated MLX Whisper transcription timed out after %.1fs. Resetting worker.", timeout)
        self.stop()
        raise TimeoutError(f"Isolated MLX Whisper transcription timed out after {timeout}s for {wav_path.name}.")

    def transcribe(
        self, wav_path: Path, whisper_prompt: Optional[str] = None, model_name: Optional[str] = None,
        timeout: float = 30.0, return_segments: bool = False,
    ) -> Union[str, Tuple[str, List[Dict[str, Any]]]]:
        """Synchronously submits a chunk to the isolated process and returns transcribed text or (text, segments)."""
        if getattr(self, "_terminal_failure", False):
            raise MLXRuntimeLost(f"[{MLXReasonCode.MLX_RUNTIME_LOST}] Isolated MLX transcriber in terminal failure state.")
        if not self.is_running:
            self.start(timeout=max(timeout, 60.0))

        self._task_counter += 1
        task_id = f"task_{self._task_counter:06d}_{int(time.time()*1000)}"

        payload = {
            "type": "TRANSCRIBE", "task_id": task_id, "wav_path": str(wav_path),
            "whisper_prompt": whisper_prompt, "model_name": model_name or self.default_model,
            "mock_mode": self.mock_mode, "backend": self.backend, "return_segments": return_segments,
        }
        if self._input_q is not None:
            self._input_q.put(payload, timeout=5.0)

        res = self._drain_and_await_result(task_id, timeout, return_segments=return_segments)
        if res is not None:
            return res

        return self._handle_worker_death(wav_path, whisper_prompt, model_name, timeout, return_segments=return_segments)

    transcribe_chunk_isolated = transcribe

    def stop(self) -> None:
        """Gracefully halts the isolated worker process."""
        if not self._is_running and self._process is None:
            return
        self._is_running = False
        if self._stop_event is not None:
            self._stop_event.set()
        if self._input_q is not None:
            with contextlib.suppress(Exception):
                self._input_q.put({"type": "STOP"}, timeout=2.0)
        if self._process is not None:
            self._process.join(timeout=3.0)
            if self._process.is_alive():
                logger.warning("MLX Whisper worker did not terminate gracefully; forcing terminate()")
                self._process.terminate()
                self._process.join(timeout=2.0)
        self._worker_pid = self._process = self._input_q = self._output_q = self._stop_event = None
        self._pending_results.clear()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.stop()
