---
trigger: always_on
---

# Credential Isolation & Pre-Egress Sanitization Rules

Governs API secret handling, hermetic test isolation, and egress sanitization.

## 1. Credential Resolution SSOT
1. **Zero Raw `os.environ`**: Direct `os.environ.get()` for secrets is banned outside `storage/credentials.py`, `storage/connection.py`, `config/credentials.py`, `config/secret_providers.py`, and `dual_pipeline/credentials.py`.
2. **Credential Manager**: Resolve secrets exclusively via `storage.credentials.CredentialManager`.
3. **Hermetic Tests**: Tests must default to mock mode (`MOCK_ENV=true`), using dummy keys and blocking live network sockets.
4. **Linter Gate**: Verified via `tools/scan_credential_leaks.py`.

## 2. Pre-Egress Sanitization
1. **Pre-Egress Scrubbing**: Outbound prompts and telemetry must pass `dual_pipeline.sanitizer.LocalPreEgressSanitizer`.
2. **Forbidden Patterns**: Outbound payloads are scrubbed for career leverage (`executive compensation band`, personal advancement), private client billing rates, and cloud secrets (`AKIA...`, `sk-...`).
3. **Compute Fork**: Client-safe extractions route to `CANONICAL_GRAPH.db`. Personal advisory notes and power maps route to encrypted shadow graph (`shadow_graph_nodes`, `namespace: 'INTERNAL_ADVISORY'`, `is_quarantined: 1`, `stealth_score: 1.0`).
4. **Fail-Closed**: Unquarantined sensitive patterns block egress and emit an audit event.
