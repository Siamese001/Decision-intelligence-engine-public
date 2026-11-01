# PD-55 — Mandatory post-synthesis HITL clarification prompts

**Status:** approved active  
**Effective package:** NEE_v55 / policy 55.0  
**Effective date:** 2026-07-14  
**Change class:** C6 protocol-only activation

## Decision

Every update, query, briefing, or artifact that synthesizes new information must present a user-visible `HITL clarification prompts` section immediately after the synthesis. It must surface every detected unresolved inconsistency, overlap, and open question; preserve provisional posture; and require explicit clarification or governed evidence for closure.

The approved defaults are:

1. Apply the rule to governed updates and ad hoc query, briefing, and artifact outputs.
2. Consolidate only semantically equivalent issues while retaining every affected proposition and source.
3. Present useful provisional synthesis first; block only the affected proposition or use when existing materiality and HITL rules require it.
4. Activate as a protocol-only release; defer NEE_00 and broader non-policy propagation to a separately authorized release.

When no issue is detected after the required checks, state `No HITL clarification prompts detected` and record the checked dimensions. Do not invent prompts.

## Scope and limits

- NEE_99 and its generated root mirror are the only semantic files changed.
- The account graph and every non-policy canonical card remain byte-identical to V54.0.
- Schema, ontology, relationship vocabulary, routes, migrations, and account-knowledge derivatives are unchanged.
- The rule applies prospectively and cannot ratify earlier unapproved mutations or outputs.

## Approval binding

- Baseline ZIP: `608409bdc6a092b86cd10a877af70789efb1b227aa4c1d93c11f1ad990b15c00`
- G1 proposal: `c0b3d1687b29caa8fc8714b44b7381c699473e0fd7252c24d1394dcfece621e7`
- C1 proposal: `d6ed976c73c17bc99ab86fe3a775ab9610755dcd6eacdf6a1207005900101d7a`
- G1 Gate O1: `ed76033b149f883ae5d93cbcc730a2b205a7f9c26999cefaab3127687a3f49af`
- Approved policy body: `25baa44e7902bc8e99aac14a74c476ec1cf7853b8bd1da1fb4aae3177455c77d`
- Approved canonical NEE_99: `5b5e905e57b026b1a404918515b09eda9b6f25b7f8f7a25dda5fa27295d34269`
- Approval statement: `Approve the PD-55 envelope and recommended defaults.`

## Rollback

Restore the exact V54 NEE_99 and root-mirror bytes, mark PD-55 superseded, preserve its audit evidence, and issue a separately governed forward repair for any revised prompting threshold or grouping behavior.
