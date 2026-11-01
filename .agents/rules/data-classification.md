---
trigger: model_decision
---

# Data Classification Rules

Governs pre-intake data classification. Sourced from `policy/pre-intake-classification-policy.json`.

## Classification Tiers & Domains
- **Default**: Intake defaults to `internal`. Public requires explicit operator assignment.
- **Domains**: `consulting.example.com` -> `partner_advisory` / `internal_consulting`; `grid.example.com` | `enterprise.example.com` -> `apex_global` / `nee-engineering`.
- **Cross-Org**: Spanning >=2 governed orgs triggers `confidential`.

## Restricted vs. Confidential
- **Restricted (Quarantine/Block)**: Private keys (`REDACTED_PRIVATE_KEY_HEADER`), AWS keys (`AKIA`, `ASIA`), credential tokens (`sk-*`, `ghp_*`).
- **Confidential (Review Required)**: SSNs, regulated data (PHI/PCI-DSS), commercial/contract terms, security architecture.

## Obligations
1. Verify classification before intake.
2. Surface detected classification signals.
3. Never ingest `restricted` material without explicit operator override.
4. Require operator acknowledgment for `confidential` content.
