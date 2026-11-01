# NEE-KG-DUAL-PIPELINE-01 final scope and production readiness

**Standalone engineering scope: COMPLETE**  
**Production-readiness control acceptance: PASS (8/8)**  
**Production execution evidence: PENDING EXTERNAL INPUTS**  
**PartnerAdvisory origin/main synchronization: EXCLUDED BY OPERATOR INSTRUCTION**

All code, controls, tests, operator surfaces, and fail-closed qualification
automation needed to execute Waves 1–6 are complete on the standalone baseline.
No remaining item can be completed truthfully inside this workspace without
organization-controlled model, identity, approval, and source assets.

## Scope completed in this closeout

1. Production BGE-M3 now requires an immutable 40-hex model revision and an
   exact SHA-256 inventory of every local model file.
2. Model revision, complete-manifest digest, graph snapshot, and sidecar are
   bound into the immutable generation and validated together.
3. Production intake, query, bootstrap, release preparation, activation,
   review, and rollback require a verified short-lived RS256 authorization
   context.
4. User identity, classification clearance, audience memberships, and
   capabilities are derived from signed claims. Caller values cannot override
   the authenticated context.
5. Qualification inputs, PDLC question, governed approval, optional HITL
   decisions, and approved model manifest can be bound by exact digest in the
   authorization token.
6. A one-command production qualification runner now executes real Copilot and
   architecture intake, production graph/index activation, governed PDLC query,
   both audit reconstructions, and receipt-bound rollback.
7. The runner fails closed, writes a machine-readable report, and attempts
   rollback after any post-activation failure.

## Final externally supplied execution inputs

| Input | Owner | Why it cannot be generated here |
|---|---|---|
| Approved pinned BAAI/bge-m3 snapshot and controlled `FlagEmbedding` environment | Model/platform owner | Approval and runtime provenance are organization-controlled |
| Trusted RSA public key plus short-lived JWT containing the required claims and exact resource/model digests | Identity/security owner | The repository must never mint its own production authorization |
| Authorized real Copilot package, architecture package, PDLC question, governed release approval, and any required HITL decisions | NEE information and governance owners | Real internal data and approvals were not supplied to this workspace |
| PASS report from `run_dual_pipeline_production_qualification.py` in the target environment | Production operator | It requires all three inputs above and actual BGE-M3 execution |

These are deployment inputs, not unfinished implementation. The initiative's
strict production milestone must remain unclaimed until that final run returns
`PASS`.

## Acceptance evidence

| Gate | Result |
|---|---:|
| Production-readiness adversarial controls | 8/8 PASS |
| Waves 2–6 acceptance | 26/26 PASS |
| Representative end-to-end CLI lifecycle | PASS |
| Wave 1 acceptance | 21/21 PASS |
| V54 graph and retrieval regressions | 7/7 and 21/21 PASS |
| V55 policy and pre-mutation gates | 31/31 and 19/19 PASS |
| Seed graph mutation | 0 |

Primary evidence:

- `tests/dual_pipeline_production_readiness_results.json`
- `tests/dual_pipeline_waves2_6_acceptance_results.json`
- `tests/dual_pipeline_operational_validation_results.json`
- `documentation/DUAL_PIPELINE_OPERATOR_RUNBOOK.md`
- `governance/dual_pipeline/waves2_6/FINAL_CLOSEOUT_INVENTORY.json`
