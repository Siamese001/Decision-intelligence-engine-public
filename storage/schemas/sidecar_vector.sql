-- Authoritative SQLite DDL for Governed Hybrid Sidecar Vector Database
-- Synchronized with dual_pipeline.index_core.constants.SIDECAR_SCHEMA

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS retrieval_record (
    record_id TEXT PRIMARY KEY,
    lane TEXT NOT NULL CHECK (lane IN ('approved','candidate','external_public')),
    record_type TEXT NOT NULL,
    namespace TEXT NOT NULL,
    retrieval_text_hash TEXT NOT NULL,
    retrieval_kind TEXT NOT NULL CHECK (retrieval_kind IN (
        'assertion','relationship_assertion','entity_facet','evidence_span',
        'guidance','routing_intent','schema'
    )),
    epistemic_status TEXT NOT NULL CHECK (epistemic_status IN (
        'candidate','approved','disputed','superseded','retracted'
    )),
    provenance_kind TEXT NOT NULL CHECK (provenance_kind IN (
        'source_bound','direct_source','derived','policy','observed','user_supplied'
    )),
    answer_use TEXT NOT NULL CHECK (answer_use IN (
        'factual_answer','candidate_hypothesis','guidance','citation_only',
        'navigation_only','routing_only','implementation_only',
        'strategic_context','decision_constraint','external_analogy'
    )),
    artifact_json TEXT NOT NULL CHECK (json_valid(artifact_json))
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_retrieval_record_lane ON retrieval_record(lane, record_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_record_metadata ON retrieval_record(
    lane, retrieval_kind, epistemic_status, answer_use, record_id
);

CREATE TABLE IF NOT EXISTS retrieval_record_object_facet (
    record_id TEXT NOT NULL
        REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (object_type IN ('node','claim','assertion')),
    object_id TEXT NOT NULL,
    vocabulary TEXT NOT NULL,
    value TEXT NOT NULL,
    assignment_method TEXT NOT NULL CHECK (
        assignment_method IN ('explicit','rule_derived','inherited','reviewed')
    ),
    review_state TEXT NOT NULL CHECK (review_state='approved'),
    PRIMARY KEY (record_id, assignment_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_retrieval_record_object_facet_value
    ON retrieval_record_object_facet(vocabulary, value, record_id);

CREATE TABLE IF NOT EXISTS dense_vector (
    record_id TEXT PRIMARY KEY REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    dimensions INTEGER NOT NULL CHECK (dimensions=1024),
    vector BLOB NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS sparse_weight (
    record_id TEXT NOT NULL REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (record_id, token)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_sparse_weight_token ON sparse_weight(token, record_id);

CREATE TABLE IF NOT EXISTS colbert_token (
    record_id TEXT NOT NULL REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    token TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (record_id, position)
) WITHOUT ROWID;
