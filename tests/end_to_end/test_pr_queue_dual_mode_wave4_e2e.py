"""Wave 4 CLI lifecycle checks for both invocation modes and durable state."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=10).stdout.strip()


def _invoke(repo: Path, state: Path, mode: str, origin: str) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[2] / "tools/pr_queue_observer.py"
    return subprocess.run(
        [sys.executable, "-B", str(script), "--repo-root", str(repo), "--output-dir", str(state), "--mode", mode, "--launch-origin", origin, "--format", "table"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False, timeout=30,
    )


def test_scheduled_and_interactive_cli_emit_same_table_without_sharing_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = repo / ".local" / "pr_queue_observer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Queue Test")
    _git(repo, "config", "user.email", "queue-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: base")
    _git(repo, "switch", "-c", "feat/observer-mode-test")
    (repo / "change.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "change.txt")
    _git(repo, "commit", "-m", "feat: test queue")
    (repo / "draft.txt").write_text("dirty but protected\n", encoding="utf-8")

    scheduled = _invoke(repo, state, "scheduled", "scheduler")
    assert scheduled.returncode == 0, scheduled.stderr
    assert "DEPRECATION NOTICE: --mode scheduled is deprecated" in scheduled.stderr
    assert scheduled.stdout.startswith("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |\n")
    assert scheduled.stdout.count("| Priority |") == 1
    assert "feat/observer-mode-test" in scheduled.stdout
    assert all(line.startswith("|") and line.endswith("|") for line in scheduled.stdout.splitlines())
    baseline_path = state / "scheduled_baseline.json"
    baseline_before = baseline_path.read_bytes()

    interactive = _invoke(repo, state, "interactive", "manual")
    assert interactive.returncode == 0, interactive.stderr
    assert interactive.stdout.count("| Priority |") == 1
    assert "feat/observer-mode-test" in interactive.stdout
    assert baseline_path.read_bytes() == baseline_before
    assert (state / "interactive_baseline.json").is_file()

    run_id = json.loads(baseline_before)["run_id"]
    run_dir = state / "runs" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["members"].items():
        assert hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == digest
    assert not (state / "observer.lock").exists()
