-- NEE graph-labeling hardening schema.
--
-- This migration is intentionally additive. Legacy label columns remain
-- immutable compatibility data while normalized satellite tables become the
-- writable semantic model. Applying this file is a graph mutation and must be
-- performed only inside an exact approval-bound release.

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

INSERT OR IGNORE INTO controlled_value_registry
  (vocabulary,value,definition,active,sort_order)
VALUES
  ('validation_state','approved',
   'Explicitly approved within the assertion scope; distinct from legacy acceptance.',
   1,3),
  ('source_kind','governed_intake','Governed dual-pipeline source package.',1,1),
  ('source_kind','legacy_source','Legacy source retained for compatibility.',1,2),
  ('source_format','json','Canonical JSON package.',1,1),
  ('source_format','legacy_unknown','Legacy format not yet normalized.',1,99),
  ('source_origin','internal','Internal source origin.',1,1),
  ('source_origin','external','External source origin.',1,2),
  ('source_origin','legacy_unknown','Legacy origin not yet normalized.',1,99),
  ('source_posture_code','authoritative_internal','Authoritative internal source.',1,1),
  ('source_posture_code','governed_internal','Governed internal source.',1,2),
  ('source_posture_code','derivative_internal','Derivative internal source.',1,3),
  ('source_posture_code','working_notes','Working notes.',1,4),
  ('source_posture_code','external_first_party','External first-party source.',1,5),
  ('source_posture_code','external_partner','External partner source.',1,6),
  ('source_posture_code','external_independent','External independent source.',1,7),
  ('source_posture_code','external_contextual','External contextual source.',1,8),
  ('evidence_directness_code','direct','Direct source evidence.',1,1),
  ('evidence_directness_code','derivative','Derivative evidence.',1,2),
  ('evidence_directness_code','contextual','Contextual evidence.',1,3),
  ('mapping_state','native','Written natively under the hardened policy.',1,1),
  ('mapping_state','deterministic_mapped','Mapped by an approved deterministic rule.',1,2),
  ('mapping_state','hitl_required','Requires source-bound human review.',1,3),
  ('mapping_state','legacy_preserved','Preserved without semantic relabeling.',1,4);

CREATE TABLE IF NOT EXISTS label_policy_version (
  policy_id TEXT PRIMARY KEY CHECK (trim(policy_id)<>''),
  policy_version TEXT NOT NULL CHECK (trim(policy_version)<>''),
  enforcement_state TEXT NOT NULL
    CHECK (enforcement_state IN ('shadow','enforced')),
  mapping_digest TEXT
    CHECK (mapping_digest IS NULL OR length(mapping_digest)=64),
  installed_at TEXT NOT NULL
) WITHOUT ROWID;

INSERT OR IGNORE INTO label_policy_version
  (policy_id,policy_version,enforcement_state,mapping_digest,installed_at)
VALUES
  ('nee-graph-labeling','1.0.0','shadow',NULL,
   strftime('%Y-%m-%dT%H:%M:%SZ','now'));

CREATE TABLE IF NOT EXISTS audience_registry (
  audience_id TEXT PRIMARY KEY CHECK (trim(audience_id)<>''),
  definition TEXT NOT NULL CHECK (trim(definition)<>''),
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

INSERT OR IGNORE INTO audience_registry
  (audience_id,definition,active,created_at)
VALUES
  ('internal_consulting','Internal Consulting audience.',1,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  ('nee-engineering','NEE engineering audience.',1,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  ('internal_working_team','Internal working-team audience.',1,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  ('partner_advisory_account_leadership','PartnerAdvisory account-leadership audience.',1,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  ('client_approved_external','Explicitly approved external client audience.',1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));

CREATE TABLE IF NOT EXISTS source_label_state (
  source_id TEXT PRIMARY KEY
    REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  source_kind TEXT NOT NULL,
  source_format TEXT NOT NULL,
  source_origin TEXT NOT NULL,
  source_posture_code TEXT NOT NULL,
  source_posture_note TEXT,
  mapping_state TEXT NOT NULL,
  mapping_rule_id TEXT,
  updated_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evidence_label_state (
  evidence_id TEXT PRIMARY KEY
    REFERENCES evidence_item(evidence_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  directness_code TEXT NOT NULL,
  basis_note TEXT,
  mapping_state TEXT NOT NULL,
  mapping_rule_id TEXT,
  updated_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS assertion_label_state (
  assertion_id TEXT PRIMARY KEY
    REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  extraction_confidence REAL NOT NULL
    CHECK (extraction_confidence BETWEEN 0.0 AND 1.0),
  assertion_confidence_label TEXT,
  assertion_confidence_score REAL
    CHECK (assertion_confidence_score IS NULL OR
           assertion_confidence_score BETWEEN 0.0 AND 1.0),
  validation_state TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL,
  mapping_state TEXT NOT NULL,
  mapping_rule_id TEXT,
  updated_at TEXT NOT NULL,
  CHECK ((assertion_confidence_label IS NULL) =
         (assertion_confidence_score IS NULL))
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS assertion_audience (
  assertion_id TEXT NOT NULL
    REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE CASCADE,
  audience_id TEXT NOT NULL
    REFERENCES audience_registry(audience_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  mapping_state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (assertion_id,audience_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS legacy_label_mapping (
  mapping_rule_id TEXT PRIMARY KEY CHECK (trim(mapping_rule_id)<>''),
  field_name TEXT NOT NULL CHECK (trim(field_name)<>''),
  legacy_value TEXT NOT NULL,
  canonical_value TEXT,
  decision_state TEXT NOT NULL
    CHECK (decision_state IN ('proposed','approved','rejected','retired')),
  mapping_basis TEXT NOT NULL CHECK (trim(mapping_basis)<>''),
  approval_reference TEXT
    REFERENCES governance_approval(approval_reference)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  UNIQUE (field_name,legacy_value)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS label_mapping_event (
  mapping_event_id TEXT PRIMARY KEY CHECK (trim(mapping_event_id)<>''),
  mapping_rule_id TEXT NOT NULL
    REFERENCES legacy_label_mapping(mapping_rule_id)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  record_type TEXT NOT NULL CHECK (trim(record_type)<>''),
  record_id TEXT NOT NULL CHECK (trim(record_id)<>''),
  before_value_sha256 TEXT NOT NULL CHECK (length(before_value_sha256)=64),
  after_value_sha256 TEXT NOT NULL CHECK (length(after_value_sha256)=64),
  approval_reference TEXT NOT NULL
    REFERENCES governance_approval(approval_reference)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  applied_at TEXT NOT NULL,
  UNIQUE (mapping_rule_id,record_type,record_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS source_label_state_guard_bi
BEFORE INSERT ON source_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='source_kind' AND value=new.source_kind AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_format' AND value=new.source_format AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_origin' AND value=new.source_origin AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_posture_code'
                     AND value=new.source_posture_code AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    THEN RAISE(ABORT,'unregistered normalized source label') END;
END;

CREATE TRIGGER IF NOT EXISTS source_label_state_guard_bu
BEFORE UPDATE ON source_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='source_kind' AND value=new.source_kind AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_format' AND value=new.source_format AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_origin' AND value=new.source_origin AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='source_posture_code'
                     AND value=new.source_posture_code AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    THEN RAISE(ABORT,'unregistered normalized source label') END;
END;

CREATE TRIGGER IF NOT EXISTS evidence_label_state_guard_bi
BEFORE INSERT ON evidence_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='evidence_directness_code'
                  AND value=new.directness_code AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    THEN RAISE(ABORT,'unregistered normalized evidence label') END;
END;

CREATE TRIGGER IF NOT EXISTS evidence_label_state_guard_bu
BEFORE UPDATE ON evidence_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='evidence_directness_code'
                  AND value=new.directness_code AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    THEN RAISE(ABORT,'unregistered normalized evidence label') END;
END;

CREATE TRIGGER IF NOT EXISTS assertion_label_state_guard_bi
BEFORE INSERT ON assertion_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='validation_state'
                  AND value=new.validation_state AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='lifecycle_state'
                     AND value=new.lifecycle_state AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    OR (new.assertion_confidence_label IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM controlled_value_registry
          WHERE vocabulary='confidence_label'
            AND value=new.assertion_confidence_label AND active=1
       ))
    THEN RAISE(ABORT,'unregistered normalized assertion label') END;
END;

CREATE TRIGGER IF NOT EXISTS assertion_label_state_guard_bu
BEFORE UPDATE ON assertion_label_state
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM controlled_value_registry
                WHERE vocabulary='validation_state'
                  AND value=new.validation_state AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='lifecycle_state'
                     AND value=new.lifecycle_state AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    OR (new.assertion_confidence_label IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM controlled_value_registry
          WHERE vocabulary='confidence_label'
            AND value=new.assertion_confidence_label AND active=1
       ))
    THEN RAISE(ABORT,'unregistered normalized assertion label') END;
END;

CREATE TRIGGER IF NOT EXISTS assertion_audience_guard_bi
BEFORE INSERT ON assertion_audience
BEGIN
  SELECT CASE WHEN
    NOT EXISTS (SELECT 1 FROM audience_registry
                WHERE audience_id=new.audience_id AND active=1)
    OR NOT EXISTS (SELECT 1 FROM controlled_value_registry
                   WHERE vocabulary='mapping_state' AND value=new.mapping_state AND active=1)
    THEN RAISE(ABORT,'unregistered assertion audience label') END;
END;

CREATE TRIGGER IF NOT EXISTS audience_registry_delete_guard
BEFORE DELETE ON audience_registry
WHEN EXISTS (
  SELECT 1 FROM assertion_audience aa
  WHERE aa.audience_id=old.audience_id
)
BEGIN
  SELECT RAISE(ABORT,'referenced audience labels cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS accepted_legacy_growth_guard
BEFORE INSERT ON assertion
WHEN new.validation_state='accepted_legacy'
 AND new.origin_type<>'legacy_migration'
BEGIN
  SELECT RAISE(ABORT,'accepted_legacy is reserved for migrated assertions');
END;

CREATE TRIGGER IF NOT EXISTS accepted_legacy_update_guard
BEFORE UPDATE OF validation_state,origin_type ON assertion
WHEN new.validation_state='accepted_legacy'
 AND new.origin_type<>'legacy_migration'
BEGIN
  SELECT RAISE(ABORT,'accepted_legacy is reserved for migrated assertions');
END;

COMMIT;
