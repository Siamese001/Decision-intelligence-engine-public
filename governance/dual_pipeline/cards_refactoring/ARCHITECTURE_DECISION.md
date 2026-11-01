# Architecture decision: cards as governed semantic views

Status: implemented as an additive, approval-bound migration proposal
Policy version: `2026-07-27.1`
Migration: `0011_cards_refactoring`

## Decision

The lowest common denominator of the knowledge graph remains a governed
assertion bound to supporting evidence. Retrieval records and embeddings remain
derived from that evidence-safe assertion layer.

A card is a versioned saved semantic view over graph objects. It is useful as a
unit of editorial meaning, navigation, and bounded retrieval scope, but it is
not:

- the graph's atomic fact;
- an embedding unit;
- an authority or validation state;
- a substitute for assertion-level classification and audience controls; or
- evidence for another assertion.

Controlled many-to-many facets classify nodes, claims, and assertions without
forcing each object into exactly one document. The initial controlled
vocabularies are:

- `semantic_domain`: a required structural classification for active nodes and
  current canonical assertions; and
- `topic`: optional editorial membership derived from existing object routes.

Approved card materializations resolve only decision-safe assertions. Candidate
material, when a future view explicitly enables it, remains in a visibly
separate review lane. Materialization is filtered against the caller's
classification clearance and audiences at the assertion level.

## Why this model

A document-shaped card is a useful reading surface, but it is too coarse to be
the graph's semantic atom. One assertion can legitimately belong to several
topics, and a card can contain assertions with different authority, access, and
currentness. Treating the card as the atom would either duplicate facts or
silently widen the weakest assertion's controls.

The view model preserves both needs:

1. assertions plus evidence remain the stable, independently governable unit;
2. facets provide reusable semantic classification; and
3. cards assemble those units into human-scale, versioned views.

## Embedding boundary

Facet metadata is projected into a sidecar join table. It is never appended to
`retrieval_text`, card Markdown is never embedded, and generated card prose is
never admitted to an evidence packet or provider prompt.

Facet and card filters may narrow eligible record IDs. An unavailable facet
index or an empty card selection fails closed; it never falls back to
unrestricted retrieval.

## Compatibility boundary

Phase A preserves:

- `claim.card_id`;
- all claim primary routes;
- `node_card_map`;
- existing ambiguous node routes; and
- legacy route-derived views.

Only assertion-level `intentional_unrouted` compatibility rows are proposed for
retirement, and only after replacement semantic-domain and topic coverage
passes. Rows are retired with `active=0`; they are not deleted.

## Governance and release

The refactor uses a dedicated administrative change set rather than weakening
the evidence-bound ingestion change-set contract. Every plan binds:

- the active generation, graph snapshot, and graph SHA-256;
- the exact migration checksum;
- derivation rule and applicability-policy digests;
- every operation and pre-state hash; and
- an exact active approval whose baseline and proposal hashes match.

Application occurs on a staged graph copy in one SQLite transaction. A failed
schema, coverage, legacy-parity, foreign-key, integrity, or injected-failure
gate rolls the whole transaction back. Activation still uses the repository's
normal W0/E0 release and completion lifecycle.

Materialization receipts are external to GraphDB. Storing a receipt containing
the hash of the graph inside that same graph would create a circular hash
dependency.

## Current proposal posture

The checked-in implementation can build and validate an exact proposal against
the active runtime. It does not mutate or activate the current graph by itself.
Graph mutation requires a completed control intake run and a new exact-scope
approval bound to the generated proposal digest.
