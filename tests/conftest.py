"""Root pytest configuration and hermetic worker sandboxing fixtures.

Enforces:
1. AGENTS.md Law 2: Zero-Contention Storage & Concurrency Invariant.
   Each parallel worker process receives an isolated storage and sandbox directory
   keyed by its xdist worker ID, preventing SQLite WAL collisions and file contention.
2. HuggingFace / Tokenizer multiprocessing safety flags.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Generator

import pytest

pytest_plugins = ["dual_pipeline.testing.pytest_progress"]


def pytest_configure(config: pytest.Config) -> None:
    """Configure runtime environment variables and custom markers before test collection and execution."""
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # Register custom markers
    config.addinivalue_line(
        "markers",
        "heavy_model: tests that load heavy neural models (e.g. BGE-M3, Whisper) and require process/scope isolation",
    )
    config.addinivalue_line(
        "markers",
        "storage_stress: high concurrency and stress tests for storage engines",
    )
    config.addinivalue_line(
        "markers",
        "audio_soak: audio processing and streaming soak tests",
    )
    config.addinivalue_line(
        "markers",
        "live_network: explicit live external network API test with real credentials",
    )

    # Configure default --dist=loadscope for xdist parallel runs
    if hasattr(config.option, "dist") and getattr(config.option, "numprocesses", None):
        if config.option.dist in (None, "load", "no"):
            config.option.dist = "loadscope"


@pytest.fixture(scope="session", autouse=True)
def worker_sandbox(request: pytest.FixtureRequest) -> Generator[str | None, None, None]:
    """Hermetic worker sandbox isolating storage databases and temporary files per xdist worker process."""
    # Determine worker identifier: gw0, gw1, ... or 'master'
    worker_id = "master"
    if hasattr(request.config, "workerinput") and request.config.workerinput is not None:
        worker_id = request.config.workerinput.get("workerid", "master")
    elif "worker_id" in getattr(request, "fixturenames", []):
        try:
            worker_id = request.getfixturevalue("worker_id")
        except Exception:
            worker_id = "master"
    else:
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")

    # If running sequentially on the main process, do not override unless requested
    if worker_id in ("master", None, ""):
        yield None
        return

    # Create worker-isolated sandbox root
    temp_sandbox = tempfile.mkdtemp(prefix=f"pytest_worker_{worker_id}_")
    storage_dir = os.path.join(temp_sandbox, "storage")
    sandbox_dir = os.path.join(temp_sandbox, "sandbox")
    os.makedirs(storage_dir, exist_ok=True)
    os.makedirs(sandbox_dir, exist_ok=True)

    old_storage = os.environ.get("STORAGE_DATA_DIR")
    old_sandbox = os.environ.get("TEST_SANDBOX_DIR")

    os.environ["STORAGE_DATA_DIR"] = storage_dir
    os.environ["TEST_SANDBOX_DIR"] = sandbox_dir

    try:
        yield temp_sandbox
    finally:
        if old_storage is not None:
            os.environ["STORAGE_DATA_DIR"] = old_storage
        else:
            os.environ.pop("STORAGE_DATA_DIR", None)

        if old_sandbox is not None:
            os.environ["TEST_SANDBOX_DIR"] = old_sandbox
        else:
            os.environ.pop("TEST_SANDBOX_DIR", None)

        shutil.rmtree(temp_sandbox, ignore_errors=True)


# ── Wave 0: Receipt generation fixture (P13) ──────────────────────────────────

@pytest.fixture
def write_wave_receipt(tmp_path: "Path") -> "Callable[[str, dict], Path]":  # type: ignore[name-defined]
    """Fixture that writes an immutable wave receipt with digest verification.

    Usage:
        def test_foo(write_wave_receipt):
            receipt_path = write_wave_receipt("Wave 0", {"scope": "...", "result": "PASS"})
            assert receipt_path.is_file()
    """
    import hashlib
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    def _write(wave_label: str, data: dict) -> Path:
        receipt = {
            "wave": wave_label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        payload = json.dumps(receipt, indent=2, sort_keys=True)
        receipt["digest"] = hashlib.sha256(payload.encode()).hexdigest()
        receipt_path = tmp_path / f"wave_receipt_{wave_label.replace(' ', '_').lower()}.json"
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt_path

    return _write  # type: ignore[return-value]


# ── Wave 4: Tier 1 Scout spy fixture (P16) ────────────────────────────────────

@pytest.fixture
def tier1_scout_spy():
    """Spy fixture that wraps execute_tier1_scout_hook and auto-asserts it fires.

    Patches dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook
    with a wrapping spy. Any test requesting this fixture auto-asserts that
    execute_tier1_scout_hook was called at least once during teardown.
    """
    from unittest.mock import MagicMock, patch

    from dual_pipeline.graph_scout_core.pipeline_hook import (
        execute_tier1_scout_hook,
    )

    call_log: list = []

    def _spy_hook(*args, **kwargs):
        result = execute_tier1_scout_hook(*args, **kwargs)
        call_log.append({"args": args, "kwargs": kwargs, "result": result})
        return result

    spy = MagicMock(side_effect=_spy_hook)
    spy.call_log = call_log

    with patch(
        "dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
        spy,
    ):
        yield spy

    assert len(call_log) >= 1, (
        "Tier 1 Scout (execute_tier1_scout_hook) was NOT called during this test. "
        "This violates the universal ingestion guarantee. Check pipeline integration."
    )


# ── Hermetic Credential Sandbox ───────────────────────────────────────────────

LIVE_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
)


@pytest.fixture(autouse=True)
def hermetic_credential_sandbox(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Hermetically isolates test execution from live workstation credentials.

    Unless a test is explicitly marked with @pytest.mark.live_network, live API keys
    from workstation environment files (~/.config/ai_env/common.env, env_di, etc.)
    are scrubbed from os.environ during test execution, preventing accidental live API egress.
    """
    if "live_network" in request.keywords:
        yield
        return

    saved_credentials: dict[str, str] = {}
    for var in LIVE_CREDENTIAL_ENV_VARS:
        val = os.environ.get(var, "")
        if var in os.environ and not val.startswith(("test-only", "mock-", "dummy-", "fake-")):
            saved_credentials[var] = os.environ.pop(var)

    try:
        yield
    finally:
        for var, val in saved_credentials.items():
            os.environ[var] = val


# ── Hermetic Production Storage Guard ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def hermetic_production_storage_guard(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Hermetically bars test suites from mutating production .local paths and active_pair registers.

    Enforces Wave 2: Test Pointer Hermeticity and State Bleed Prevention.
    """
    if os.environ.get("ALLOW_PRODUCTION_LOCAL_MUTATION") == "1":
        yield
        return

    import builtins
    import hashlib
    import io
    from pathlib import Path
    from dual_pipeline.runtime_root import resolve_main_repository_root

    main_repo = resolve_main_repository_root()
    prod_local = (main_repo / ".local").resolve()

    ap_path = prod_local / "dual_pipeline" / "active_pair.json"
    rt_ap_path = prod_local / "dual_pipeline" / "runtime" / "active_pair.json"

    def _file_sha(p: Path) -> str | None:
        if not p.is_file():
            return None
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            return None

    pre_ap_sha = _file_sha(ap_path)
    pre_rt_ap_sha = _file_sha(rt_ap_path)

    orig_open = builtins.open
    orig_path_open = Path.open

    def _check_target(file_obj: Any, mode: str) -> None:
        if not any(m in mode for m in ("w", "a", "x", "+")):
            return
        try:
            target = Path(file_obj).resolve()
            if prod_local in target.parents or target == prod_local:
                if (
                    target in (ap_path, rt_ap_path)
                    or target.name == "active_pair.json"
                    or "forbidden" in target.name
                    or (target.name.startswith("CANONICAL_GRAPH") and not target.name.endswith(".lock"))
                ):
                    raise RuntimeError(
                        f"STATE_BLEED_VIOLATION: Automated tests are strictly prohibited from writing "
                        f"to production active pair or database path: {target}. Use tmp_path or set ALLOW_PRODUCTION_LOCAL_MUTATION=1."
                    )
        except (ValueError, TypeError):
            pass

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        _check_target(file, mode)
        return orig_open(file, *args, **kwargs)

    def guarded_path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        _check_target(self, mode)
        return orig_path_open(self, *args, **kwargs)

    builtins.open = io.open = guarded_open
    Path.open = guarded_path_open

    try:
        yield
    finally:
        builtins.open = io.open = orig_open
        Path.open = orig_path_open

        post_ap_sha = _file_sha(ap_path)
        post_rt_ap_sha = _file_sha(rt_ap_path)
        if pre_ap_sha is not None and post_ap_sha != pre_ap_sha:
            raise AssertionError(
                f"STATE_BLEED_VIOLATION: Production active_pair.json was mutated during test! "
                f"Pre-test SHA: {pre_ap_sha}, Post-test SHA: {post_ap_sha}"
            )
        if pre_rt_ap_sha is not None and post_rt_ap_sha != pre_rt_ap_sha:
            raise AssertionError(
                f"STATE_BLEED_VIOLATION: Production runtime/active_pair.json was mutated during test! "
                f"Pre-test SHA: {pre_rt_ap_sha}, Post-test SHA: {post_rt_ap_sha}"
            )



