# RCA & Wave Implementation Plan: Mandatory WAV Audio Preservation Across Worktree Teardown

## Root Cause Analysis (RCA)

### Incident Summary
The master audio recording for `Dociq_Jack_Amit` (`output/transcript_cache/project_nexus/10-01-2026/00_10-01-2026_Dociq_Jack_Amit_master_110726.wav`, SHA-256 `223d604c1ad7259147cc48f0f340d4cbb0847d00b65cbdb8a18b9ae092e4a607`) was permanently deleted when its feature worktree (`.worktrees/live_dociq_jack_amit_jack_slalom`) was pruned after merging `feat/live-dociq_jack_amit_jack_slalom` (`2e3fa64`) into `main`.

### Root Causes
1. **RC-1 — Worktree Migration Unlinks Primary Root Copy**: During live recording materialization, `finalize_worktree_materialization` (`dual_pipeline/live_meeting_recorder_core/worktree_repointer.py`) moves staged audio into `.worktrees/<slug>/output/transcript_cache/` and `cleanup_staging_directories` unlinks the primary repository root's copy via `_unlink_migrated_files`.
2. **RC-2 — Git Ignore Excludes Audio From Merge Promotion**: Because `*.wav` is ignored in `.gitignore`, `git merge` promotes only tracked `.md` and `.json` deliverables to `main`, leaving `00_*_master_*.wav` untracked inside `.worktrees/<slug>/`.
3. **RC-3 — Teardown Deletes Worktree Without Audio Repatriation**: Both `teardown_ingest_worktree` (`dual_pipeline/live_meeting_recorder_core/ingest_worktree_cleanup.py`) and `_remove_worktree_files` / `cmd_finish` (`tools/pr_governance_core/worktree.py`) remove `.worktrees/<slug>` without repatriating ignored `*.wav` and ASR cache artifacts back to `<primary_root>/output/transcript_cache/`.

---

## Wave 1: Primary Root Dual-Persistence at Master WAV Stitch & Materialization

### Objective & Scope
Ensure stitched master `.wav` files (`00_*_master_*.wav`) and sealed ASR cache artifacts are atomically mirrored to the primary repository root's canonical `output/transcript_cache/<project>/<date>/` during recording completion, and prevent worktree staging cleanup from unlinking master `.wav` files in the primary root.

### Milestones & Deliverables
- `[MODIFY]` `dual_pipeline/live_meeting_recorder_core/stop_lifecycle.py` — Mirror stitched `master_wav` and acoustic receipts to `<primary_root>/output/transcript_cache/<project>/<date>/` using atomic `.tmp` -> `fsync` -> `os.replace` with SHA-256 verification.
- `[MODIFY]` `dual_pipeline/live_meeting_recorder_core/worktree_repointer.py` — Guard `_unlink_migrated_files` and `cleanup_staging_directories` so master `.wav` files (`00_*_master*.wav`) and sealed ASR caches in `<primary_root>/output/transcript_cache/` are never unlinked when migrating to a worktree.
- `[NEW]` `tests/unit/test_wav_primary_root_persistence.py` — Unit tests verifying primary root `.wav` persistence during `reconcile_master_wav_and_receipt` and `finalize_worktree_materialization`.

### Acceptance Criteria
- Stitched master `.wav` exists in both `<worktree>/output/transcript_cache/<project>/<date>/` and `<primary_root>/output/transcript_cache/<project>/<date>/` with identical SHA-256 digests immediately upon recording stop.
- `cleanup_staging_directories` retains `00_*_master*.wav` in `<primary_root>/output/transcript_cache/`.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python -m pytest tests/unit/test_wav_primary_root_persistence.py -v --timeout=60`
- **Input**: Synthetic multi-chunk PCM WAV fixtures stitched inside an isolated temporary worktree and primary repo root sandbox.
- **Expected**: All tests pass; primary root `output/transcript_cache/<project>/<date>/00_*_master_*.wav` exists with matching SHA-256 after materialization and stop lifecycle.
- **Verify independently**: Compute `hashlib.sha256` on the primary root `.wav` file outside the recorder object and compare against `StitchReceipt.master_wav_sha256`.
- **Fail if**: Primary root `.wav` is missing, 0 bytes, or its SHA-256 differs from the worktree `.wav`.
- **Record**: Test execution log and SHA-256 parity receipt in `reports/wave1_wav_primary_persistence_receipt.json`.

---

## Wave 2: Mandatory Pre-Teardown Audio & ASR Cache Repatriation Gate

### Objective & Scope
Enforce a fail-closed pre-teardown repatriation gate across all worktree removal paths (`cmd_finish`, `_remove_worktree_files`, and `teardown_ingest_worktree`) that copies all `*.wav` and ASR cache sidecars from `<worktree>/output/` into `<primary_root>/output/transcript_cache/` and verifies SHA-256 parity before worktree deletion.

### Milestones & Deliverables
- `[NEW]` `tools/pr_governance_core/audio_repatriation.py` — Implement `repatriate_worktree_audio_artifacts(worktree_dir, primary_root)` to copy `*.wav` and ASR sidecars (`*.raw_asr.json`, `*.segments.json`, `*.utterances.json`, `*.transcript_cache.json`, `*.cache_seal.json`, `*_manifest.json`) to `<primary_root>/output/transcript_cache/`, verify SHA-256 parity, and unlink the worktree copy only after root verification succeeds.
- `[MODIFY]` `tools/pr_governance_core/worktree.py` — Invoke `repatriate_worktree_audio_artifacts` in `cmd_finish` and `_remove_worktree_files` prior to cleanliness checks and `git worktree remove` (including `--force` and `--ingest` modes).
- `[MODIFY]` `dual_pipeline/live_meeting_recorder_core/ingest_worktree_cleanup.py` — Invoke `repatriate_worktree_audio_artifacts` in `teardown_ingest_worktree` before `git worktree remove --force` and `shutil.rmtree`; abort teardown if SHA-256 verification fails.

### Acceptance Criteria
- Running `pr_governance.py finish` (normal, `--force`, or `--ingest`) or `teardown_ingest_worktree` on a worktree containing untracked/ignored `.wav` files preserves every master `.wav` and ASR sidecar in `<primary_root>/output/transcript_cache/`.
- If destination write or SHA-256 check fails, worktree removal aborts fail-closed leaving the worktree intact.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python -m pytest tests/unit/test_wav_primary_root_persistence.py -v --timeout=60`
- **Input**: Isolated git repository with linked `.worktrees/<slug>` containing ignored `output/transcript_cache/<project>/<date>/00_test_master.wav` and ASR JSON sidecars.
- **Expected**: Worktree is cleanly removed while `<primary_root>/output/transcript_cache/<project>/<date>/00_test_master.wav` and sidecars remain intact with identical SHA-256.
- **Verify independently**: Confirm `.worktrees/<slug>` no longer exists on disk while `stat` and `sha256` on `<primary_root>/output/transcript_cache/.../00_test_master.wav` match pre-teardown values.
- **Fail if**: Any `.wav` file is lost during teardown or a simulated checksum mismatch fails to block worktree removal.
- **Record**: Repatriation audit log and receipt in `reports/wave2_wav_repatriation_gate_receipt.json`.

---

## Wave 3: End-to-End & Adversarial Worktree Lifecycle Verification

### Objective & Scope
Verify end-to-end and adversarial `.wav` preservation across the complete branch lifecycle (worktree start, live capture/stitch, PR merge to `main`, and worktree teardown) under Class B receipt gates.

### Milestones & Deliverables
- `[NEW]` `tests/end_to_end/test_wav_preservation_post_worktree_teardown_e2e.py` — End-to-end lifecycle test exercising worktree creation, master WAV stitching, governed PR merge, worktree teardown, and post-teardown audio readback from primary root.
- `[NEW]` `tests/adversarial/test_wav_repatriation_adversarial.py` — Adversarial edge-case suite covering boundary payloads (0-byte WAV rejection), malformed/corrupt destination writes (fail-closed teardown abort), `--force` / `--ingest` teardown flags, and idempotent re-repatriation when root file already exists.

### Acceptance Criteria
- E2E and adversarial test suites pass 100% and resolve dynamically under `tools.test_discovery_core.resolve_all_tests()`.
- Zero regressions in existing worktree governance tests.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python tools/run_tests_with_progress.py tests/unit/test_wav_primary_root_persistence.py tests/end_to_end/test_wav_preservation_post_worktree_teardown_e2e.py tests/adversarial/test_wav_repatriation_adversarial.py --timeout=60`
- **Input**: Hermetic temporary git repositories and linked worktrees with real PCM `.wav` headers/frames and simulated fault injections.
- **Expected**: 100% pass rate across unit, E2E, and adversarial suites; `validate_implementation_plan.py` exits 0.
- **Verify independently**: Run `.venv/bin/python -c "from tools.test_discovery_core import resolve_all_tests; r = resolve_all_tests(); assert any('test_wav_preservation_post_worktree_teardown_e2e.py' in p for p in r['E2E_TESTS'])"`.
- **Fail if**: Any test fails, E2E test is unenrolled, or any `.wav` byte is lost post-teardown.
- **Record**: Class B receipt saved to `reports/wave3_wav_preservation_e2e_receipt.json`.

---

## Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- |
| Wave 1 | Primary root dual-persistence of stitched master `.wav` and ASR sidecars with staging unlink guard | ✅ COMPLETE | `reports/wave1_wav_primary_persistence_receipt.json` |
| Wave 2 | Pre-teardown audio and ASR cache repatriation gate across `cmd_finish` and `teardown_ingest_worktree` | ✅ COMPLETE | `reports/wave2_wav_repatriation_gate_receipt.json` |
| Wave 3 | End-to-end and adversarial worktree lifecycle verification with dynamic test discovery enrollment | ✅ COMPLETE | `reports/wave3_wav_preservation_e2e_receipt.json` |

