"""
End-to-End lifecycle tests for PR consolidation, branch clustering, semantic merge, and cohort release.
Exercises the entire High-Velocity High-Fidelity (HV-HF) pipeline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.gate_impact_core import GateLayer, classify_diff_impact
from tools.merge_train_core import assemble_speculative_cohort
from tools.pr_topology_core import compute_topological_clusters, compute_topology_matrix
from tools.retire_superseded_branch import retire_branch
from tools.semantic_merge_core import merge_python_ast_sets

ROOT = Path(__file__).resolve().parent.parent.parent


def test_full_pr_consolidation_lifecycle_e2e(tmp_path: Path):
    """
    Simulates complete HV-HF lifecycle across 5 stages:
    1. Topology discovery and clustering
    2. AST semantic conflict resolution
    3. Subsystem qualification gate minimization
    4. Speculative cohort merge assembly with commit trailers
    5. Worktree and branch tombstoning
    """
    repo_dir = tmp_path / "lifecycle_repo"
    repo_dir.mkdir(parents=True)

    # Initialize git repo
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "agent@test.internal"], cwd=repo_dir, check=True)

    # Initial commit on main
    base_file = repo_dir / "registry.py"
    base_file.write_text("MODULES = {\n    'core',\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "registry.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit on main"], cwd=repo_dir, check=True)

    # Branch A: adds 'auth'
    subprocess.run(["git", "checkout", "-b", "feat/alpha"], cwd=repo_dir, check=True, capture_output=True)
    alpha_file = repo_dir / "alpha.py"
    alpha_file.write_text("def alpha(): return 1\n", encoding="utf-8")
    base_file.write_text("MODULES = {\n    'core',\n    'auth',\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.py", "registry.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat(alpha): add alpha module"], cwd=repo_dir, check=True)

    # Branch B: adds 'billing' from main
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feat/beta"], cwd=repo_dir, check=True, capture_output=True)
    beta_file = repo_dir / "beta.py"
    beta_file.write_text("def beta(): return 2\n", encoding="utf-8")
    base_file.write_text("MODULES = {\n    'core',\n    'billing',\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "beta.py", "registry.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat(beta): add beta module"], cwd=repo_dir, check=True)

    # Stage 1: Topology discovery
    matrix = compute_topology_matrix(["feat/alpha", "feat/beta"], root=repo_dir, base_branch="main")
    assert len(matrix.branches) == 2
    # Both touched registry.py, so pair overlap > 0
    assert matrix.pairwise_overlap["feat/alpha"]["feat/beta"] == 1
    assert "registry.py" in matrix.shared_files.get("feat/alpha::feat/beta", [])
    clusters = compute_topological_clusters(matrix)
    assert len(clusters) >= 1

    # Stage 2: AST Semantic 3-way merge on registry.py
    base_content = 'MODULES = {\n    "core",\n}\n'
    ours_content = 'MODULES = {\n    "core",\n    "auth",\n}\n'
    theirs_content = 'MODULES = {\n    "core",\n    "billing",\n}\n'
    success, resolved = merge_python_ast_sets(base_content, ours_content, theirs_content)
    assert success
    assert '"auth"' in resolved
    assert '"billing"' in resolved
    assert '"core"' in resolved

    # Stage 3: Subsystem gate impact analysis
    impact = classify_diff_impact(["alpha.py", "beta.py", "registry.py"])
    assert impact.layer == GateLayer.CORE_ENGINE
    assert impact.profile_name == "full"

    # Stage 4: Speculative cohort assembly
    cohort_rec = assemble_speculative_cohort(
        cohort_name="e2e_cohort",
        branches=["feat/alpha", "feat/beta"],
        root=repo_dir,
    )
    assert cohort_rec.cohort_name == "e2e_cohort"
    assert len(cohort_rec.branches) == 2
    assert any("Consolidated-From: feat/alpha" in t for t in cohort_rec.commit_trailers)
    assert any("Consolidated-From: feat/beta" in t for t in cohort_rec.commit_trailers)

    # Stage 5: Branch retirement / tombstoning
    tomb_res = retire_branch("feat/alpha", dry_run=True, root=repo_dir)
    assert tomb_res["branch"] == "feat/alpha"
    assert tomb_res["status"] == "SIMULATED"
    assert "archive/" in tomb_res["archive_tag"]


def test_pr_consolidation_cli_invocation_e2e(tmp_path: Path):
    """Verifies that all three core CLIs execute with JSON receipts."""
    # 1. Topology auditor CLI
    topo_json = tmp_path / "topo.json"
    cmd1 = [
        sys.executable,
        str(ROOT / "tools" / "pr_topology_auditor.py"),
        "--base",
        "main",
        "--branches",
        "feat/live-dociq_q4_scope_bailey_amit",
        "--json",
        str(topo_json),
    ]
    res1 = subprocess.run(cmd1, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res1.returncode == 0, f"Topology CLI error: {res1.stderr}"
    assert topo_json.exists()

    # 2. Gate impact analyzer CLI
    gate_json = tmp_path / "gate.json"
    cmd2 = [
        sys.executable,
        str(ROOT / "tools" / "gate_impact_analyzer.py"),
        "--files",
        "output/test.md",
        "tools/script.py",
        "--json",
        str(gate_json),
    ]
    res2 = subprocess.run(cmd2, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res2.returncode == 0, f"Gate impact CLI error: {res2.stderr}"
    assert gate_json.exists()

    # 3. Merge train orchestrator CLI
    train_json = tmp_path / "train.json"
    cmd3 = [
        sys.executable,
        str(ROOT / "tools" / "merge_train_orchestrator.py"),
        "--cohort",
        "platform_velocity",
        "--dry-run",
        "--json",
        str(train_json),
    ]
    res3 = subprocess.run(cmd3, cwd=ROOT, capture_output=True, text=True, check=False)
    assert "SPECULATIVE COHORT ASSEMBLY RECEIPT" in res3.stdout
    assert train_json.exists()
