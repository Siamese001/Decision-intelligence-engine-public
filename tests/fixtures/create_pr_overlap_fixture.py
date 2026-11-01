#!/usr/bin/env python3
"""
Generates the deterministic git fixture repository for PR overlap auditing.
Location: tests/fixtures/pr_overlap
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "pr_overlap"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def generate_fixture(force: bool = False) -> None:
    if not force and (FIXTURE_DIR / ".git").exists():
        return
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    _git(["init", "-b", "main"], FIXTURE_DIR)
    _git(["config", "user.name", "Fixture Generator"], FIXTURE_DIR)
    _git(["config", "user.email", "generator@test.internal"], FIXTURE_DIR)
    _git(["config", "commit.gpgsign", "false"], FIXTURE_DIR)
    (FIXTURE_DIR / ".git" / "info" / "exclude").write_text(".local/\n*.pyc\n__pycache__/\n", encoding="utf-8")

    base_core = """# Core Engine Module
def func_alpha():
    step1 = 10
    step2 = 20
    return step1 + step2

# Spacer lines
# Line 10
# Line 11
# Line 12
# Line 13
# Line 14
# Line 15
# Line 16
# Line 17
# Line 18
# Line 19
# Line 20

def func_beta():
    mode = "offline"
    return mode
"""
    (FIXTURE_DIR / "core.py").write_text(base_core, encoding="utf-8")
    (FIXTURE_DIR / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(["add", "core.py", "README.md"], FIXTURE_DIR)
    _git(["commit", "-m", "chore: initial commit on main"], FIXTURE_DIR)

    # 1. feat/alpha-dup-a
    _git(["checkout", "-b", "feat/alpha-dup-a"], FIXTURE_DIR)
    dup_core = base_core.replace("step1 = 10", "step1 = 100")
    (FIXTURE_DIR / "core.py").write_text(dup_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: update func_alpha step1"], FIXTURE_DIR)

    # 2. feat/alpha-dup-b
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/alpha-dup-b"], FIXTURE_DIR)
    (FIXTURE_DIR / "core.py").write_text(dup_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: update func_alpha step1 identical patch"], FIXTURE_DIR)

    # 3. feat/beta-disjoint
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/beta-disjoint"], FIXTURE_DIR)
    disjoint_core = base_core.replace('mode = "offline"', 'mode = "online"')
    (FIXTURE_DIR / "core.py").write_text(disjoint_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: update func_beta mode"], FIXTURE_DIR)

    # 4. feat/gamma-overlap
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/gamma-overlap"], FIXTURE_DIR)
    overlap_core = base_core.replace("step1 = 10", "step1 = 999")
    (FIXTURE_DIR / "core.py").write_text(overlap_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: conflicting edit on func_alpha step1"], FIXTURE_DIR)

    # 5. feat/delta-same-symbol
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/delta-same-symbol"], FIXTURE_DIR)
    sym_core = base_core.replace("step2 = 20", "step2 = 500")
    (FIXTURE_DIR / "core.py").write_text(sym_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: update func_alpha step2 (disjoint lines, same symbol)"], FIXTURE_DIR)

    # 6. feat/epsilon-blob-a
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/epsilon-blob-a"], FIXTURE_DIR)
    (FIXTURE_DIR / "settings.json").write_text('{"environment": "prod"}\n', encoding="utf-8")
    _git(["add", "settings.json"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add settings.json config A"], FIXTURE_DIR)

    # 7. feat/epsilon-blob-b
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/epsilon-blob-b"], FIXTURE_DIR)
    (FIXTURE_DIR / "settings.json").write_text('{"environment": "prod"}\n', encoding="utf-8")
    _git(["add", "settings.json"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add settings.json config B identical blob"], FIXTURE_DIR)

    # 8. feat/zeta-independent
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/zeta-independent"], FIXTURE_DIR)
    (FIXTURE_DIR / "api.md").write_text("# API Docs\nNew API specs\n", encoding="utf-8")
    _git(["add", "api.md"], FIXTURE_DIR)
    _git(["commit", "-m", "docs: add api docs"], FIXTURE_DIR)

    # 9. feat/theta-stacked-on-beta (stacked on beta-disjoint)
    _git(["checkout", "feat/beta-disjoint"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/theta-stacked-on-beta"], FIXTURE_DIR)
    stacked_core = disjoint_core + "\ndef func_stacked():\n    return 'stacked'\n"
    (FIXTURE_DIR / "core.py").write_text(stacked_core, encoding="utf-8")
    _git(["add", "core.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: stacked work on top of beta"], FIXTURE_DIR)

    # 10. feat/cherry-clean
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-clean"], FIXTURE_DIR)
    (FIXTURE_DIR / "clean_feature.py").write_text("def clean_val():\n    return 42\n", encoding="utf-8")
    _git(["add", "clean_feature.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add clean feature"], FIXTURE_DIR)

    # 11. feat/cherry-prereq (2 commits: step1 clean, step2 depends on step1)
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-prereq"], FIXTURE_DIR)
    (FIXTURE_DIR / "series.py").write_text("def series_step1():\n    return 10\n", encoding="utf-8")
    _git(["add", "series.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add series step1"], FIXTURE_DIR)
    (FIXTURE_DIR / "series.py").write_text("def series_step1():\n    return 10\n\ndef series_step2():\n    return series_step1() * 2\n", encoding="utf-8")
    _git(["add", "series.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add series step2 depending on step1"], FIXTURE_DIR)

    # Commits on main for conflict and already-in-main detection
    _git(["checkout", "main"], FIXTURE_DIR)
    (FIXTURE_DIR / "conflict_target.py").write_text("status = 'main_approved'\n", encoding="utf-8")
    _git(["add", "conflict_target.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: main target status approved"], FIXTURE_DIR)
    (FIXTURE_DIR / "shared_patch.py").write_text("SHARED_KEY = 'v1_production'\n", encoding="utf-8")
    _git(["add", "shared_patch.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add shared key to main"], FIXTURE_DIR)

    # 12. feat/cherry-conflict (conflicting edit on conflict_target.py)
    _git(["checkout", "HEAD~2"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-conflict"], FIXTURE_DIR)
    (FIXTURE_DIR / "conflict_target.py").write_text("status = 'branch_rejected'\n", encoding="utf-8")
    _git(["add", "conflict_target.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: branch conflicting target status"], FIXTURE_DIR)

    # 13. feat/cherry-already-in-main (identical patch to main commit)
    _git(["checkout", "HEAD~1"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-already-in-main"], FIXTURE_DIR)
    (FIXTURE_DIR / "shared_patch.py").write_text("SHARED_KEY = 'v1_production'\n", encoding="utf-8")
    _git(["add", "shared_patch.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: add shared key to branch identical patch"], FIXTURE_DIR)

    # 14. feat/cherry-split-candidate (multi-domain commit)
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-split-candidate"], FIXTURE_DIR)
    (FIXTURE_DIR / "governance").mkdir(exist_ok=True)
    (FIXTURE_DIR / "storage").mkdir(exist_ok=True)
    (FIXTURE_DIR / "tools").mkdir(exist_ok=True)
    (FIXTURE_DIR / "governance" / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (FIXTURE_DIR / "storage" / "db.py").write_text("db = {}\n", encoding="utf-8")
    (FIXTURE_DIR / "tools" / "run.py").write_text("def run(): pass\n", encoding="utf-8")
    _git(["add", "governance/spec.md", "storage/db.py", "tools/run.py"], FIXTURE_DIR)
    _git(["commit", "-m", "feat: update spec and db and run"], FIXTURE_DIR)

    # 15. feat/cherry-empty-commit
    _git(["checkout", "main"], FIXTURE_DIR)
    _git(["checkout", "-b", "feat/cherry-empty-commit"], FIXTURE_DIR)
    _git(["commit", "--allow-empty", "-m", "chore: empty commit"], FIXTURE_DIR)

    _git(["checkout", "main"], FIXTURE_DIR)
    print(f"Deterministic fixture generated at {FIXTURE_DIR}")


if __name__ == "__main__":
    generate_fixture(force=True)
