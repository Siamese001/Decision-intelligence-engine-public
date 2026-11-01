#!/usr/bin/env python3
"""Gate 31: Copilot Active Graph Black-Box Smoke Test.

Executes `nee copilot` via CLI subprocess against the authoritative active graph.
Verifies that:
1. Subprocess terminates with exit code 0.
2. Output contains grounded content (e.g. chronological commitments or assertions).
3. Output is non-empty and does not fail with session discovery errors.

Fails closed with exit code 1 if any condition is not met.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


def verify_copilot_active_graph_smoke(
    repo_root: Path | None = None,
    runner: Optional[Callable[..., Any]] = None,
) -> Tuple[bool, str]:
    """Execute black-box smoke test for nee copilot against active graph."""
    root = repo_root or ROOT
    py = sys.executable

    cmd = [
        py,
        "-B",
        "nee.py",
        "copilot",
        "--project",
        "project_lens",
        "--intent",
        "gaps",
        "--request",
        "open design decisions to close out LENS",
    ]

    env = os.environ.copy()
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env.setdefault("PYTEST_CURRENT_TEST", "verify_copilot_smoke")
    exec_runner = runner or subprocess.run

    import tempfile

    with tempfile.TemporaryDirectory(prefix="copilot_smoke_") as temp_dir:
        temp_sandbox = Path(temp_dir)
        meeting_dir = temp_sandbox / "meeting"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--meeting-dir", str(meeting_dir)])

        attempt_detail_str = ""
        try:
            proc = exec_runner(cmd, timeout=60, cwd=root, env=env, capture_output=True, text=True, check=False)
        except Exception as exc:
            return False, f"Failed executing copilot subprocess: {exc}"
        finally:
            # Extract diagnostic attempt details before cleaning up
            attempt_files = [
                meeting_dir / "copilot_attempts.jsonl",
                root / ".local" / "copilot" / "project_lens" / "copilot_attempts.jsonl",
                root / "copilot_attempts.jsonl",
                root / ".local" / "dual_pipeline" / "learning" / "copilot_attempts.jsonl",
            ]
            import json as _json

            for af in attempt_files:
                if af.is_file():
                    try:
                        lines = af.read_text(encoding="utf-8").strip().splitlines()
                        if lines:
                            last_rec = _json.loads(lines[-1])
                            attempt_detail_str = (
                                f"\n[Recorded Attempt Detail] reason={last_rec.get('terminal_reason')} "
                                f"details={last_rec.get('details')} "
                                f"roots={last_rec.get('resolved_roots')}"
                            )
                            break
                    except Exception:
                        pass

            # Defense-in-depth: clean up root-level attempt log if written directly in cwd
            root_attempt = root / "copilot_attempts.jsonl"
            if root_attempt.is_file():
                root_attempt.unlink(missing_ok=True)

    if proc.returncode != 0:
        err = (proc.stdout + "\n" + proc.stderr).strip()
        return False, f"Copilot subprocess exited with code {proc.returncode}:{attempt_detail_str}\n{err}"

    stdout = proc.stdout.strip()
    if not stdout:
        return False, "Copilot subprocess produced empty stdout"

    if "NO_LIVE_CONTEXT" in stdout or "No active recording session found" in stdout:
        return False, f"Copilot failed with session discovery error:\n{stdout}"

    low_out = stdout.lower()
    for forbidden in (
        "candidate ai-sdlc control",
        "candidate enterprise-retrieval control",
        "candidate gemini/",
        "person-specific attribution from the spelling",
        "malformed_representation",
    ):
        if forbidden in low_out:
            return False, f"Copilot output contained forbidden jargon or internal metadata ({forbidden}):\n{stdout}"

    has_grounding = any(
        marker in stdout
        for marker in (
            "What is the gap here?",
            "Could we clarify",
            "Which status is current?",
            "We should confirm whether",
            "Chronological Commitments:",
            "Open Design & Delivery Gaps",
            "HAS_CLAIM_TEXT",
            "asrt_",
        )
    )
    if not has_grounding:
        return False, f"Copilot output lacked grounded citations or commitments:\n{stdout}"

    return True, f"Copilot active graph smoke test passed:\n{stdout[:200]}..."


def main() -> int:
    success, message = verify_copilot_active_graph_smoke(ROOT)
    if success:
        print(f"✅ {message}")
        return 0
    else:
        print(f"❌ {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
