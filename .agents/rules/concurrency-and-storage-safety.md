---
trigger: always_on
---

# Concurrency & Storage Safety Rules

Governs persistent SQLite database access, thread safety, and test isolation.

## 1. Zero-Contention SQLite Storage Access
1. **Governed Connection Manager**: Database access must route via `storage/connection.py` (`DatabaseManager` / `get_db_connection()`).
2. **Mandatory Pragmas**: Connections must initialize with:
   ```sql
   PRAGMA journal_mode = WAL;
   PRAGMA busy_timeout = 30000;
   PRAGMA synchronous = NORMAL;
   PRAGMA foreign_keys = ON;
   ```
3. **Serialized Writes**: Multi-threaded writes route through an in-memory write serializer or journal. Uncoordinated concurrent writes to a single SQLite file are barred.
4. **Read-Committed Isolation**: Long-running queries use read-only snapshots to prevent write-lock starvation.

## 2. Hermetic Test Environment Isolation
1. **Isolated DB Provisioning**: Integration tests invoke `provision_isolated_test_db()` to create unique temporary databases.
2. **Automatic Teardown**: Test runners must release database locks and file handles cleanly during teardown.
3. **Stress Harness**: Changes to `storage/` or `dual_pipeline/semantic_cache*` must pass the 16-worker concurrency stress harness without `OperationalError: database is locked`.
