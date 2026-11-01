# Disposition — Original Unapproved v45 Semantic Build

**Artifact SHA-256:** `bd1d1f10291a5ceba8fd673b4def6bc38efae3cc51bb0d7197df25663f82f178`  
**Status:** `quarantined`  
**Approved baseline:** no  
**Ratified by policy 45.0:** no

The original v45 build was created before the user reviewed and approved the
graph and card deltas. It therefore remains audit evidence only.

## Recorded semantic delta

| Component | Delta |
|---|---:|
| source | +4 |
| node | +23 |
| edge | +37 |
| claim | +17 |
| alias | +12 |
| node-card mapping | +58 |
| validation | +10 |
| existing card files rewritten | 10 |

The exact high-level graph summary and card-change matrix are preserved under
`governance/audit/` for incident evidence. They are post-mutation reports and
do not meet policy 45.0's G1/C1 proposal contract.

## Corrective handling

The corrected v45 is rebuilt from `NEE_v44(1).zip`
(`714b3468810e44abed9ed30c2721dfcf39eca6016996cb99d258d4bea9867531`). None of the original v45 graph, migration, derivative, or
non-policy-card changes are imported.

A future attachment reconciliation must begin from read-only analysis and
produce new complete G1 and C1 proposals. The user must approve their exact
digests before implementation.
