---
trigger: always_on
---

# Mandatory Physical Proof for Graph Mutations

Governs physical proof requirements, verification checks, and reporting for all knowledge graph mutations.

## 1. Five Mandatory Physical Checks
Every mutation to a knowledge graph database (`nee_graph.sqlite`, canonical graphs, or release candidates) must produce an empirical proof receipt validating five independent checks:

1. **File Changed**: Size, modification timestamp (`mtime`), and SHA-256 digest differ before vs after the write.
2. **Raw Byte Scan**: Written IDs (nodes, assertions, edges) are confirmed present on disk by scanning the raw SQLite database file bytes, bypassing SQLite drivers. The write-ahead log (`-wal`) is strictly excluded: mutations must be checkpointed to the primary database file.
3. **Integrity Verification**: `PRAGMA integrity_check` executed on a fresh connection returns `ok`.
4. **Fresh Readback**: A fresh read-only SQLite connection (`file:...mode=ro`) independently reads back the exact rows and fields written.
5. **Row Delta**: Per-table row counts change by exactly the expected deltas.

## 2. Direct Graph Write Prohibition
Direct raw SQL modifications (`INSERT`, `UPDATE`, `DELETE`) against knowledge graph databases outside governed pipelines are prohibited:
- All mutations must execute via `GovernedGraphWriter.apply` or the Universal Write Gateway (`UniversalWriteGateway`).
- Enforced mechanically by Gate 32 (`tools/lint_graph_write_paths.py`).
- Fail-Closed: Any mutation lacking a valid, verified proof receipt is treated as uncommitted and rolled back.

## 3. Operator Report Format
Every graph mutation reported to the operator in chat or durable receipts must include the physical proof table:

| Check | Status | Empirical Observation | Physical Detail |
| :--- | :--- | :--- | :--- |
| File Changed | PASS | Size: `<old>` → `<new>` bytes, SHA: `<old_sha>` → `<new_sha>` | `mtime`: `<before>` → `<after>` |
| Raw Bytes | PASS | `<count>` ID(s) located on disk (bypassing SQLite) | Byte offsets: `[<offsets>]`, Page(s): `[<pages>]` |
| Integrity | PASS | `PRAGMA integrity_check` returned `ok` | Zero corruption, foreign keys valid |
| Readback | PASS | Fresh read-only connection verified exact rows | Verified IDs: `[<ids>]` |
| Row Delta | PASS | Table counts matched expected deltas | Per-table: `<table_deltas>` |
