#!/usr/bin/env python3
"""Portable entry point for the governed NEE runtime."""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional


def _is_record_start_intent(args: list[str]) -> bool:
    """Evaluates whether argv represents a genuine live recording start intent."""
    from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
        validate_pre_dispatch_intent,
    )

    is_valid, _ = validate_pre_dispatch_intent(args)
    if not is_valid:
        return False

    non_capture_flags = {
        "--no-instant",
        "--disable-fast-bootstrap",
        "--detach",
    }
    if any(arg in non_capture_flags for arg in args):
        return False

    if len(args) <= 1:
        return False

    return True


COPILOT_DEADLINE_SECONDS = 5.0


def _start_copilot_watchdog(args: list[str]) -> threading.Event | None:
    """Cap the whole CLI call, including imports and ledger writes."""
    if not args or args[0] != "copilot":
        return None
    stop = threading.Event()
    # Tests may tighten the cap; callers cannot relax the production ceiling.
    try:
        configured = float(os.environ.get("NEE_COPILOT_DEADLINE_SECONDS", COPILOT_DEADLINE_SECONDS))
    except ValueError:
        configured = COPILOT_DEADLINE_SECONDS
    timeout = max(0.1, min(COPILOT_DEADLINE_SECONDS, configured))

    def expire() -> None:
        if stop.wait(timeout):
            return
        if "--json" in args:
            output = b'{"status":"FAIL","error":"COPILOT_DEADLINE_EXCEEDED"}\n'
        else:
            output = b"```text\nCopilot took too long. Please try again.\n```\n"
        os.write(1, output)
        os._exit(124)

    threading.Thread(target=expire, name="copilot_deadline", daemon=True).start()
    return stop


def _normalize_args_if_copilot(args: list[str]) -> list[str]:
    for token in args[:3]:
        if "copilot" in token.lower():
            from dual_pipeline.cli.copilot_handlers import normalize_copilot_argv

            return normalize_copilot_argv(args)
    return args


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point dispatching fast pre-CLI bootstrap before heavy imports."""
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in ("speaker-hint", "hint"):
        from dual_pipeline.cli.speaker_hint_handlers import fast_handle_speaker_hint_cli

        return fast_handle_speaker_hint_cli(args[1:])

    if args and args[0] in ("executive-brief", "exec-brief"):
        from dual_pipeline.cli.executive_brief_handlers import handle_executive_brief_cli

        return handle_executive_brief_cli(args[1:])

    args = _normalize_args_if_copilot(args)
    watchdog = _start_copilot_watchdog(args)

    bootstrap_active = False
    if _is_record_start_intent(args):
        try:
            from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
                run_fast_bootstrap,
            )
            spool_dir = run_fast_bootstrap(args)
            if spool_dir:
                bootstrap_active = True
        except Exception:
            pass

    from dual_pipeline.cli import main as cli_main

    try:
        return cli_main(args)
    finally:
        if bootstrap_active:
            try:
                from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
                    clear_bootstrap_spooler,
                )
                clear_bootstrap_spooler(stop=True)
            except Exception:
                pass
        if watchdog is not None:
            watchdog.set()


def _has_lingering_non_daemon_threads() -> bool:
    """Return True if any non-daemon thread other than the main thread is still alive."""
    main_thread = threading.main_thread()
    for t in threading.enumerate():
        if t is not main_thread and t.is_alive() and not t.daemon:
            return True
    return False


def _terminate_cli_process(exit_code: int) -> None:
    """Flush telemetry and standard streams, then exit without hanging on background pools."""
    try:
        if "dual_pipeline.otel_engine" in sys.modules:
            from dual_pipeline.otel_engine import shutdown_telemetry

            shutdown_telemetry(timeout_millis=1500)
    except Exception:
        pass

    try:
        import logging

        logging.shutdown()
    except Exception:
        pass

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    first_arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if first_arg in ("copilot", "speaker-hint", "hint") or "copilot" in first_arg or _has_lingering_non_daemon_threads():
        os._exit(exit_code)

    def _exit_watchdog() -> None:
        import time

        time.sleep(1.5)
        os._exit(exit_code)

    threading.Thread(target=_exit_watchdog, name="cli_exit_watchdog", daemon=True).start()
    sys.exit(exit_code)


if __name__ == "__main__":
    code = main()
    resolved_code = code if isinstance(code, int) else (0 if code is None else 1)
    _terminate_cli_process(resolved_code)

