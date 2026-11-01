"""Safe process termination, stream pipe closure, and descendant reaping utilities."""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.process_cleanup")


def reap_descendants(pid: int) -> None:
    """Finds and forcefully terminates any remaining descendant processes for a given PID."""
    if not pid or pid <= 0:
        return
    try:
        res = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=1.0)
        if res.returncode != 0:
            return
        for line in res.stdout.strip().splitlines():
            try:
                child_pid = int(line.strip())
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.kill(child_pid, signal.SIGKILL)
            except ValueError:
                continue
    except Exception:
        pass


def close_process_pipes(p: Optional[subprocess.Popen]) -> None:
    """Safely closes standard stream pipes AFTER process termination and reader draining."""
    if p is None:
        return
    for pipe in (getattr(p, "stdin", None), getattr(p, "stdout", None), getattr(p, "stderr", None)):
        if pipe and not getattr(pipe, "closed", True):
            with contextlib.suppress(Exception):
                pipe.close()


def _resolve_target_pgid(pid: Optional[int]) -> Optional[int]:
    if not pid:
        return None
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError, TypeError):
        return None
    my_pgrp = os.getpgrp() if hasattr(os, "getpgrp") else None
    return pgid if (pgid and pgid != my_pgrp and pgid != 0) else None


def _send_signal(p: subprocess.Popen, sig: signal.Signals, target_pgid: Optional[int]) -> bool:
    try:
        if target_pgid is not None:
            os.killpg(target_pgid, sig)
        else:
            p.send_signal(sig)
        return True
    except (ProcessLookupError, OSError):
        return False
    except Exception:
        return True


def _escalate_signals(
    p: subprocess.Popen,
    sig_sequence: Sequence[signal.Signals],
    wait_timeout: float,
    target_pgid: Optional[int],
) -> None:
    for sig in sig_sequence:
        alive = _send_signal(p, sig, target_pgid)
        if not alive:
            break
        try:
            if hasattr(p, "wait"):
                p.wait(timeout=wait_timeout)
                break
        except (subprocess.TimeoutExpired, Exception):
            continue


def terminate_process(p: Optional[subprocess.Popen], fast: bool = False) -> None:
    """Signals and terminates child process with bounded waits. NEVER closes pipes before signaling."""
    if p is None:
        return
    pid = getattr(p, "pid", None)
    if hasattr(p, "poll") and p.poll() is not None:
        if pid:
            reap_descendants(pid)
        return

    wait_timeout = 0.05 if fast else 0.3
    sig_sequence = (signal.SIGKILL,) if fast else (signal.SIGINT, signal.SIGTERM, signal.SIGKILL)
    target_pgid = _resolve_target_pgid(pid)
    _escalate_signals(p, sig_sequence, wait_timeout, target_pgid)

    try:
        if hasattr(p, "poll") and p.poll() is None:
            p.kill()
            p.wait(timeout=0.1)
    except Exception:
        pass

    if pid:
        reap_descendants(pid)


def send_sigint_flush(p: Optional[subprocess.Popen]) -> None:
    """Sends SIGINT to a subprocess to flush trailers without abrupt termination."""
    if p is None or (hasattr(p, "poll") and p.poll() is not None):
        return
    pid = getattr(p, "pid", None)
    target_pgid = _resolve_target_pgid(pid)
    _send_signal(p, signal.SIGINT, target_pgid)


def start_pipe_drain_thread(
    proc: Optional[subprocess.Popen],
    stop_event: Optional[threading.Event],
    callback: Callable[[str], None],
    name: str = "pipe-drain",
) -> Optional[threading.Thread]:
    """Starts a daemon thread to drain process stderr safely avoiding OS pipe deadlock."""
    pipe = getattr(proc, "stderr", None)
    if not pipe:
        return None

    def _drain() -> None:
        try:
            for line in iter(pipe.readline, b""):
                if stop_event and stop_event.is_set():
                    break
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    callback(decoded)
        except Exception:
            pass

    t = threading.Thread(target=_drain, name=name, daemon=True)
    t.start()
    return t


def stop_capture_session(
    watchdog: Any,
    drain_stop_event: Optional[threading.Event],
    drain_threads: Optional[List[threading.Thread]],
    procs: Sequence[Optional[subprocess.Popen]],
    fast: bool = False,
) -> None:
    """Bounded, ordered shutdown: stop watchdog -> signal drains -> terminate procs -> join drains -> close pipes."""
    if watchdog:
        with contextlib.suppress(Exception):
            watchdog.stop()

    if drain_stop_event:
        drain_stop_event.set()

    procs_to_stop = [p for p in procs if p is not None]
    for p in procs_to_stop:
        try:
            terminate_process(p, fast=fast)
        except Exception as exc:
            logger.warning("Error terminating capture process %s: %s", getattr(p, "pid", None), exc)

    if drain_threads:
        for t in drain_threads:
            if t.is_alive():
                t.join(timeout=0.2)
        drain_threads.clear()

    for p in procs_to_stop:
        close_process_pipes(p)


def _reap_single_pid(foreign_pid: int, device_index: str) -> None:
    try:
        logger.warning(
            "[DEVICE-CONTENTION-REAP] Terminating orphaned foreign FFmpeg PID %s accessing %s",
            foreign_pid, device_index
        )
        os.kill(foreign_pid, signal.SIGTERM)
        time.sleep(0.15)
        try:
            os.kill(foreign_pid, 0)
            os.kill(foreign_pid, signal.SIGKILL)
        except OSError:
            pass
    except Exception as reap_err:
        logger.warning("Failed to reap foreign FFmpeg PID %s: %s", foreign_pid, reap_err)


_NEE_CAPTURE_CMD_MARKERS = (
    "chunk_",
    "_master_",
    "spool_",
    "dual_pipeline",
    "/output/",
    ".session_registry",
    ".worktrees/",
)


def _is_nee_owned_ffmpeg_cmd(cmd: str, foreign_pid: int, owned_pids: Optional[Sequence[int]] = None) -> bool:
    """Returns True only if the FFmpeg process belongs to an NEE recording/spooler session."""
    if owned_pids is not None:
        return foreign_pid in set(owned_pids)
    cmd_lower = cmd.lower()
    return any(marker in cmd_lower for marker in _NEE_CAPTURE_CMD_MARKERS)


def _find_foreign_pid(
    line: str,
    my_pid: int,
    device_index: str,
    owned_pids: Optional[Sequence[int]] = None,
) -> Tuple[Optional[int], bool]:
    parts = line.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None, False
    try:
        foreign_pid = int(parts[0])
    except ValueError:
        return None, False
    if foreign_pid == my_pid:
        return None, False
    cmd = parts[1]
    if "avfoundation" in cmd and (device_index in cmd or "-i" in cmd):
        is_nee_owned = _is_nee_owned_ffmpeg_cmd(cmd, foreign_pid, owned_pids=owned_pids)
        logger.warning(
            "[DEVICE-CONTENTION-WARNING] Competing FFmpeg PID %s (nee_owned=%s) detected accessing %s: %s",
            foreign_pid, is_nee_owned, device_index, cmd
        )
        return foreign_pid, is_nee_owned
    return None, False


def detect_and_reap_device_contention(
    device_index: str = ":0",
    auto_reap: bool = True,
    owned_pids: Optional[Sequence[int]] = None,
) -> bool:
    """Detects whether competing FFmpeg processes are actively capturing from avfoundation,
    and safely terminates orphaned NEE-spawned FFmpeg processes without killing unrelated user jobs."""
    try:
        res = subprocess.run(["pgrep", "-fl", "ffmpeg"], capture_output=True, text=True, timeout=2.0)
        if res.returncode != 0:
            return False
        my_pid = os.getpid()
        contention_found = False
        for line in res.stdout.strip().splitlines():
            foreign_pid, is_nee_owned = _find_foreign_pid(line, my_pid, device_index, owned_pids=owned_pids)
            if foreign_pid is not None:
                contention_found = True
                if auto_reap and is_nee_owned:
                    _reap_single_pid(foreign_pid, device_index)
        return contention_found
    except Exception:
        return False


def finalize_wav_riff_header(wav_path: Any) -> None:
    """Patches unfinalized 0xFFFFFFFF RIFF/data subchunk sizes to actual file byte lengths."""
    import struct
    from pathlib import Path
    p = Path(wav_path)
    if not p.is_file():
        return
    with contextlib.suppress(Exception):
        sz = p.stat().st_size
        if sz < 44:
            return
        with open(p, "r+b") as fh:
            head = fh.read(min(4096, sz))
            if head[:4] == b"RIFF" and struct.unpack("<I", head[4:8])[0] in (0, 0xFFFFFFFF):
                fh.seek(4)
                fh.write(struct.pack("<I", max(0, sz - 8)))
            idx = 12
            while idx + 8 <= len(head):
                cid = head[idx : idx + 4]
                csz = struct.unpack("<I", head[idx + 4 : idx + 8])[0]
                if cid == b"data":
                    if csz in (0, 0xFFFFFFFF):
                        fh.seek(idx + 4)
                        fh.write(struct.pack("<I", max(0, sz - (idx + 8))))
                    break
                step = 8 + csz + (csz & 1)
                if step <= 0:
                    break
                idx += step
            fh.flush()
            os.fsync(fh.fileno())

