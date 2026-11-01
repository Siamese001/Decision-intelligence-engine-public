# PD-54 — Scoped Retrieval Release Delta and Evaluation Performance Optimization

- **Status:** approved_active
- **Effective version:** 54.0
- **Approval source:** explicit user instruction on 2026-09-07.
- **Problem:**
  1. Commit `4ef8f17` introduced `retrieval_clearance` safety rules in Python code. When release preparation evaluated a new sidecar against the August 4 baseline, 190 unparsed/legacy baseline records were filtered at query time. The automated suite builder diffed raw sidecar tables, misidentifying these 190 filtered records as intentional deletions by the release author, exploding an incremental meeting release from 8 test cases to 5,079 test cases.
  2. Under `repeat_run_count: 2`, each case executed 4 retrievals (baseline, candidate warm-up, run 1, run 2). Re-encoding identical queries on CPU without caching resulted in 20,316 neural inferences, taking 5+ hours, saturating CPU cores, and failing the 7,500 ms latency ceiling and delta MRR 1.0 threshold.
- **Decision:**
  1. **Scoped Release Delta Isolation:** When an intake release contains `component_runs`, automated retrieval release suite generation and coverage validation must derive added and removed records via `_scoped_release_delta()`, restricting test cases strictly to the assertions touched in the directive's graph change sets (plus dependent entity facets). Query-time security/clearance filtering of legacy baseline records is strictly isolated and must never be treated as candidate release deletions.
  2. **Query Encoding Cache:** Steady-state retrieval release evaluation must cache query encodings (`query_cache: dict[str, dict[str, Any]]`) across baseline, warm-up, and repeat runs, eliminating redundant neural inference passes.
  3. **Vectorized Multi-Vector Scoring:** Hybrid retrieval ColBERT MaxSim scoring must execute via vectorized matrix operations (`np.matmul` and `np.max`) rather than iterative Python loops.
- **Unaffected controls:**
  - All 11 non-compensating release evaluation gates (`core_thresholds`, `delta_thresholds`, `latency`, `real_bge_m3`, `bm25_execution`, `rank_stability`, `repeat_stability`, `pair_binding`, `forbidden_leakage`, `critical_cases`, `coverage_complete`) remain strictly blocking.
  - Required BGE-M3 model revision and manifest digest verification remains unchanged.
  - Zero-provider boundaries during ingestion, normalization, and graph writes remain strictly enforced.
- **Rollback:** Revert `_scoped_release_delta()` and `query_cache` in `dual_pipeline/retrieval_release.py` and `dual_pipeline/retrieval_suite.py` through a separately authorized protocol decision.
