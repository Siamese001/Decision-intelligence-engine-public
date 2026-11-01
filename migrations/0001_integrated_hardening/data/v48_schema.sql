-- NEE V48 integrated hardening proposed schema (Phase A review artifact)
PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;

CREATE TABLE schema_migration (
  migration_id TEXT PRIMARY KEY CHECK (trim(migration_id) <> ''),
  checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256)=64),
  description TEXT NOT NULL CHECK (trim(description) <> ''),
  applied_at TEXT NOT NULL CHECK (trim(applied_at) <> ''),
  applied_by TEXT NOT NULL CHECK (trim(applied_by) <> ''),
  rollback_supported INTEGER NOT NULL CHECK (rollback_supported IN (0,1)),
  release_version TEXT NOT NULL CHECK (trim(release_version) <> '')
) WITHOUT ROWID;

CREATE TABLE write_control (
  control_id INTEGER PRIMARY KEY CHECK (control_id=1),
  allow_legacy_write INTEGER NOT NULL DEFAULT 0 CHECK (allow_legacy_write IN (0,1)),
  active_transaction_id TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE node_type_registry (
  node_type_id TEXT PRIMARY KEY CHECK (trim(node_type_id) <> ''),
  canonical_name TEXT NOT NULL,
  definition TEXT NOT NULL,
  parent_type_id TEXT REFERENCES node_type_registry(node_type_id),
  required_properties_json TEXT NOT NULL CHECK (json_valid(required_properties_json)),
  optional_properties_json TEXT NOT NULL CHECK (json_valid(optional_properties_json)),
  identity_rule TEXT NOT NULL,
  example_node_id TEXT REFERENCES node(node_id),
  registry_state TEXT NOT NULL CHECK (registry_state IN ('active','deprecated')),
  replacement_type_id TEXT REFERENCES node_type_registry(node_type_id),
  retrieval_use TEXT NOT NULL,
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE edge_type_registry (
  edge_type_id TEXT PRIMARY KEY CHECK (trim(edge_type_id) <> ''),
  canonical_predicate TEXT NOT NULL,
  definition TEXT NOT NULL,
  relationship_family TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('directed','symmetric')),
  inverse_predicate TEXT,
  symmetric_flag INTEGER NOT NULL CHECK (symmetric_flag IN (0,1)),
  transitive_flag INTEGER NOT NULL CHECK (transitive_flag IN (0,1)),
  cardinality TEXT NOT NULL CHECK (cardinality IN ('one_to_one','one_to_many','many_to_one','many_to_many','temporal_many_to_one')),
  temporal_requirement TEXT NOT NULL CHECK (temporal_requirement IN ('required','recommended','not_required')),
  evidence_required INTEGER NOT NULL CHECK (evidence_required IN (0,1)),
  qualifier_rules_json TEXT NOT NULL CHECK (json_valid(qualifier_rules_json)),
  registry_state TEXT NOT NULL CHECK (registry_state IN ('active','deprecated')),
  replacement_predicate TEXT,
  allow_self_loop INTEGER NOT NULL DEFAULT 0 CHECK (allow_self_loop IN (0,1)),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE edge_type_domain_range (
  edge_type_id TEXT NOT NULL REFERENCES edge_type_registry(edge_type_id),
  source_node_type TEXT NOT NULL REFERENCES node_type_registry(node_type_id),
  target_node_type TEXT NOT NULL REFERENCES node_type_registry(node_type_id),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  observed_baseline_count INTEGER NOT NULL DEFAULT 0 CHECK (observed_baseline_count >= 0),
  PRIMARY KEY (edge_type_id, source_node_type, target_node_type)
) WITHOUT ROWID;

CREATE TABLE property_registry (
  property_name TEXT NOT NULL,
  applies_to TEXT NOT NULL,
  data_type TEXT NOT NULL,
  required_flag INTEGER NOT NULL CHECK (required_flag IN (0,1)),
  controlled_vocabulary TEXT,
  validation_rule TEXT NOT NULL,
  temporal_meaning TEXT,
  searchable_flag INTEGER NOT NULL CHECK (searchable_flag IN (0,1)),
  retrieval_use TEXT NOT NULL,
  PRIMARY KEY (property_name, applies_to)
) WITHOUT ROWID;

CREATE TABLE controlled_value_registry (
  vocabulary TEXT NOT NULL,
  value TEXT NOT NULL,
  definition TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  sort_order INTEGER NOT NULL,
  PRIMARY KEY (vocabulary, value)
) WITHOUT ROWID;

CREATE TABLE node_state (
  node_id TEXT PRIMARY KEY REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  lifecycle_state TEXT NOT NULL,
  validation_state TEXT NOT NULL,
  currentness_state TEXT NOT NULL,
  approval_state TEXT NOT NULL,
  rag_state TEXT NOT NULL,
  resolution_state TEXT NOT NULL,
  retrieval_eligibility TEXT NOT NULL,
  publication_state TEXT NOT NULL,
  review_state TEXT NOT NULL,
  legacy_status_text TEXT,
  intentional_orphan INTEGER NOT NULL DEFAULT 0 CHECK (intentional_orphan IN (0,1)),
  intentional_orphan_reason TEXT,
  updated_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE source_version (
  source_version_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  version_number INTEGER NOT NULL CHECK (version_number >= 1),
  source_fingerprint TEXT NOT NULL UNIQUE CHECK (length(source_fingerprint)=64),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  source_date_normalized TEXT,
  source_date_precision TEXT NOT NULL CHECK (source_date_precision IN ('day','month','year','unknown')),
  source_date_status TEXT NOT NULL CHECK (source_date_status IN ('exact','estimated_from_source_snapshot','unknown')),
  authority_rank INTEGER NOT NULL CHECK (authority_rank BETWEEN 1 AND 9),
  scope_text TEXT NOT NULL,
  posture_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, version_number)
) WITHOUT ROWID;

CREATE TABLE extraction_run (
  extraction_run_id TEXT PRIMARY KEY,
  algorithm_version TEXT NOT NULL,
  baseline_graph_sha256 TEXT NOT NULL CHECK (length(baseline_graph_sha256)=64),
  input_manifest_sha256 TEXT NOT NULL CHECK (length(input_manifest_sha256)=64),
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('completed','failed','rolled_back')),
  deterministic INTEGER NOT NULL CHECK (deterministic IN (0,1))
) WITHOUT ROWID;

CREATE TABLE assertion (
  assertion_id TEXT PRIMARY KEY CHECK (trim(assertion_id) <> ''),
  assertion_fingerprint TEXT NOT NULL UNIQUE CHECK (length(assertion_fingerprint)=64),
  subject_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  predicate TEXT NOT NULL CHECK (trim(predicate) <> ''),
  legacy_predicate TEXT,
  object_node_id TEXT REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  object_literal TEXT,
  object_literal_type TEXT,
  assertion_type TEXT NOT NULL,
  atomicity_state TEXT NOT NULL CHECK (atomicity_state IN ('atomic','unparsed_legacy_text')),
  scope_text TEXT NOT NULL,
  speaker_or_author TEXT,
  primary_source_id TEXT REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  extraction_run_id TEXT REFERENCES extraction_run(extraction_run_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  extraction_method TEXT NOT NULL,
  origin_type TEXT NOT NULL,
  confidence_label TEXT NOT NULL,
  confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
  validation_state TEXT NOT NULL,
  currentness_state TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  date_precision TEXT NOT NULL CHECK (date_precision IN ('day','month','year','unknown')),
  date_status TEXT NOT NULL CHECK (date_status IN ('exact','estimated_from_source_snapshot','unknown')),
  recorded_at TEXT NOT NULL,
  superseded_at TEXT,
  superseded_by_assertion_id TEXT REFERENCES assertion(assertion_id) DEFERRABLE INITIALLY DEFERRED,
  resolution_state TEXT NOT NULL,
  canonical_status TEXT NOT NULL,
  canonical_eligible INTEGER NOT NULL CHECK (canonical_eligible IN (0,1)),
  retrieval_eligible INTEGER NOT NULL CHECK (retrieval_eligible IN (0,1)),
  materiality TEXT NOT NULL,
  classification TEXT NOT NULL,
  allowed_audiences_json TEXT NOT NULL CHECK (json_valid(allowed_audiences_json)),
  hitl_required INTEGER NOT NULL CHECK (hitl_required IN (0,1)),
  validation_priority TEXT,
  validation_owner TEXT,
  qualifiers_json TEXT NOT NULL CHECK (json_valid(qualifiers_json)),
  legacy_record_type TEXT NOT NULL CHECK (legacy_record_type IN ('edge','claim')),
  legacy_record_ids_json TEXT NOT NULL CHECK (json_valid(legacy_record_ids_json)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((object_node_id IS NOT NULL AND object_literal IS NULL) OR (object_node_id IS NULL AND object_literal IS NOT NULL)),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
  CHECK (superseded_by_assertion_id IS NULL OR superseded_by_assertion_id <> assertion_id)
);

CREATE TABLE evidence_item (
  evidence_id TEXT PRIMARY KEY,
  evidence_fingerprint TEXT NOT NULL UNIQUE CHECK (length(evidence_fingerprint)=64),
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  source_version_id TEXT NOT NULL REFERENCES source_version(source_version_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  locator_kind TEXT NOT NULL,
  locator_value TEXT NOT NULL,
  page_number TEXT,
  section_name TEXT,
  paragraph_number TEXT,
  row_reference TEXT,
  slide_number TEXT,
  transcript_timestamp TEXT,
  direct_quote TEXT,
  evidence_directness TEXT NOT NULL,
  source_authority_rank INTEGER NOT NULL CHECK (source_authority_rank BETWEEN 1 AND 9),
  extraction_confidence REAL NOT NULL CHECK (extraction_confidence BETWEEN 0.0 AND 1.0),
  corroboration_group TEXT,
  location_status TEXT NOT NULL CHECK (location_status IN ('exact','legacy_row_pointer','unavailable_in_baseline')),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE assertion_evidence (
  assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence_item(evidence_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  evidence_relationship TEXT NOT NULL CHECK (evidence_relationship IN ('supports','contradicts')),
  independent_corroboration INTEGER NOT NULL CHECK (independent_corroboration IN (0,1)),
  created_at TEXT NOT NULL,
  PRIMARY KEY (assertion_id, evidence_id)
) WITHOUT ROWID;

CREATE TABLE legacy_claim_assertion_map (
  claim_id TEXT PRIMARY KEY REFERENCES claim(claim_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  migration_confidence REAL NOT NULL CHECK (migration_confidence BETWEEN 0.0 AND 1.0),
  migration_state TEXT NOT NULL CHECK (migration_state IN ('candidate_unparsed','structured','quarantined')),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE legacy_edge_assertion_map (
  edge_id TEXT PRIMARY KEY REFERENCES edge(edge_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  migration_confidence REAL NOT NULL CHECK (migration_confidence BETWEEN 0.0 AND 1.0),
  migration_state TEXT NOT NULL CHECK (migration_state IN ('structured','quarantined')),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE canonical_alias (
  canonical_alias_id TEXT PRIMARY KEY,
  normalized_alias TEXT NOT NULL,
  display_alias TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  alias_type TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  confidence_label TEXT NOT NULL,
  confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
  valid_from TEXT,
  valid_to TEXT,
  ambiguity_state TEXT NOT NULL CHECK (ambiguity_state IN ('unique','ambiguous')),
  preferred_flag INTEGER NOT NULL CHECK (preferred_flag IN (0,1)),
  review_state TEXT NOT NULL CHECK (review_state IN ('legacy_migrated','pending','reviewed','ambiguous_blocked')),
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE legacy_alias_canonical_map (
  alias_id TEXT PRIMARY KEY REFERENCES alias(alias_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  canonical_alias_id TEXT NOT NULL REFERENCES canonical_alias(canonical_alias_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE entity_resolution_candidate (
  candidate_id TEXT PRIMARY KEY,
  candidate_type TEXT NOT NULL CHECK (candidate_type IN ('normalized_node_duplicate','alias_collision')),
  normalized_key TEXT NOT NULL,
  node_type_scope TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending_human_review','resolved_merge','resolved_distinct','rejected')),
  auto_merge_allowed INTEGER NOT NULL CHECK (auto_merge_allowed IN (0,1)),
  rationale TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved_at TEXT
) WITHOUT ROWID;

CREATE TABLE entity_resolution_candidate_member (
  candidate_id TEXT NOT NULL REFERENCES entity_resolution_candidate(candidate_id) ON UPDATE CASCADE ON DELETE CASCADE,
  node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
  PRIMARY KEY (candidate_id, node_id)
) WITHOUT ROWID;

CREATE TABLE entity_merge_event (
  merge_event_id TEXT PRIMARY KEY,
  canonical_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (event_type IN ('merge','split','reverse_merge')),
  status TEXT NOT NULL CHECK (status IN ('proposed','approved','completed','rolled_back')),
  approval_reference TEXT,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
) WITHOUT ROWID;

CREATE TABLE entity_redirect (
  from_node_id TEXT PRIMARY KEY REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  to_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  merge_event_id TEXT NOT NULL REFERENCES entity_merge_event(merge_event_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  CHECK (from_node_id <> to_node_id)
) WITHOUT ROWID;

CREATE TABLE canonical_route (
  route_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL CHECK (object_type IN ('node','claim','assertion')),
  object_id TEXT NOT NULL,
  card_id TEXT REFERENCES card(card_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  route_role TEXT NOT NULL,
  is_primary INTEGER NOT NULL CHECK (is_primary IN (0,1)),
  route_status TEXT NOT NULL CHECK (route_status IN ('active','ambiguous','intentional_unrouted')),
  source_mechanism TEXT NOT NULL,
  intentional_unrouted_reason TEXT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  CHECK ((route_status='intentional_unrouted' AND card_id IS NULL AND intentional_unrouted_reason IS NOT NULL) OR
         (route_status<>'intentional_unrouted' AND card_id IS NOT NULL))
);

CREATE TABLE graph_quality_exception (
  exception_id TEXT PRIMARY KEY,
  metric_name TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  exception_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  control_action TEXT NOT NULL,
  owner TEXT NOT NULL,
  approved_reference TEXT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE search_document (
  search_rowid INTEGER PRIMARY KEY,
  search_document_id TEXT NOT NULL UNIQUE,
  object_type TEXT NOT NULL CHECK (object_type IN ('node','claim','assertion')),
  object_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  canonical_status TEXT NOT NULL,
  currentness_state TEXT NOT NULL,
  retrieval_eligible INTEGER NOT NULL CHECK (retrieval_eligible IN (0,1)),
  source_id TEXT REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  assertion_id TEXT REFERENCES assertion(assertion_id) ON UPDATE CASCADE ON DELETE CASCADE,
  updated_at TEXT NOT NULL,
  UNIQUE (object_type, object_id)
);

CREATE VIRTUAL TABLE search_fts USING fts5(
  title,
  body,
  object_type UNINDEXED,
  object_id UNINDEXED,
  content='search_document',
  content_rowid='search_rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE ingestion_run (
  ingestion_run_id TEXT PRIMARY KEY,
  source_fingerprint TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('registered','parsed','validated','promoted','quarantined','failed','rolled_back')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error_message TEXT,
  UNIQUE (source_fingerprint, pipeline_version)
) WITHOUT ROWID;

CREATE TABLE ingestion_source (
  ingestion_run_id TEXT NOT NULL REFERENCES ingestion_run(ingestion_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  source_version_id TEXT NOT NULL REFERENCES source_version(source_version_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  PRIMARY KEY (ingestion_run_id, source_id)
) WITHOUT ROWID;

CREATE TABLE candidate_entity (
  candidate_entity_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES ingestion_run(ingestion_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
  proposed_node_type TEXT NOT NULL,
  proposed_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  match_state TEXT NOT NULL CHECK (match_state IN ('new','deterministic_match','ambiguous','rejected')),
  matched_node_id TEXT REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE candidate_assertion (
  candidate_assertion_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES ingestion_run(ingestion_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
  subject_candidate_or_node_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_candidate_node_id TEXT,
  object_literal TEXT,
  validation_state TEXT NOT NULL,
  confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
  created_at TEXT NOT NULL,
  CHECK ((object_candidate_node_id IS NOT NULL AND object_literal IS NULL) OR (object_candidate_node_id IS NULL AND object_literal IS NOT NULL))
) WITHOUT ROWID;

CREATE TABLE quarantine_item (
  quarantine_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES ingestion_run(ingestion_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
  record_type TEXT NOT NULL,
  record_id TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  details_json TEXT NOT NULL CHECK (json_valid(details_json)),
  status TEXT NOT NULL CHECK (status IN ('open','reprocessed','resolved','rejected')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
) WITHOUT ROWID;

CREATE TABLE promotion_event (
  promotion_event_id TEXT PRIMARY KEY,
  ingestion_run_id TEXT NOT NULL REFERENCES ingestion_run(ingestion_run_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  transaction_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('started','committed','rolled_back','failed')),
  promoted_assertion_count INTEGER NOT NULL DEFAULT 0 CHECK (promoted_assertion_count >= 0),
  promoted_entity_count INTEGER NOT NULL DEFAULT 0 CHECK (promoted_entity_count >= 0),
  approval_reference TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT
) WITHOUT ROWID;

CREATE TABLE audit_event (
  audit_event_id TEXT PRIMARY KEY,
  actor_or_process TEXT NOT NULL,
  action TEXT NOT NULL,
  record_type TEXT NOT NULL,
  record_id TEXT NOT NULL,
  previous_value_json TEXT CHECK (previous_value_json IS NULL OR json_valid(previous_value_json)),
  new_value_json TEXT CHECK (new_value_json IS NULL OR json_valid(new_value_json)),
  source_id TEXT REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  event_timestamp TEXT NOT NULL,
  reason TEXT NOT NULL,
  validation_reference TEXT,
  approval_reference TEXT,
  migration_or_ingestion_run_id TEXT,
  release_version TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE operational_metric (
  metric_run_id TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  metric_value_json TEXT NOT NULL CHECK (json_valid(metric_value_json)),
  measured_at TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY (metric_run_id, metric_name)
) WITHOUT ROWID;

CREATE TABLE backup_run (
  backup_run_id TEXT PRIMARY KEY,
  source_database_sha256 TEXT NOT NULL CHECK (length(source_database_sha256)=64),
  backup_database_sha256 TEXT NOT NULL CHECK (length(backup_database_sha256)=64),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256)=64),
  journal_mode TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  integrity_status TEXT NOT NULL CHECK (integrity_status IN ('pass','fail')),
  retrieval_smoke_status TEXT NOT NULL CHECK (retrieval_smoke_status IN ('pass','fail'))
) WITHOUT ROWID;

CREATE TABLE restore_run (
  restore_run_id TEXT PRIMARY KEY,
  backup_run_id TEXT NOT NULL REFERENCES backup_run(backup_run_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  restored_database_sha256 TEXT NOT NULL CHECK (length(restored_database_sha256)=64),
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  integrity_status TEXT NOT NULL CHECK (integrity_status IN ('pass','fail')),
  retrieval_smoke_status TEXT NOT NULL CHECK (retrieval_smoke_status IN ('pass','fail'))
) WITHOUT ROWID;

CREATE TABLE quality_gate_run (
  gate_run_id TEXT PRIMARY KEY,
  release_version TEXT NOT NULL,
  database_sha256 TEXT NOT NULL CHECK (length(database_sha256)=64),
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  overall_status TEXT NOT NULL CHECK (overall_status IN ('pass','fail'))
) WITHOUT ROWID;

CREATE TABLE quality_gate_result (
  gate_run_id TEXT NOT NULL REFERENCES quality_gate_run(gate_run_id) ON UPDATE CASCADE ON DELETE CASCADE,
  gate_name TEXT NOT NULL,
  measured_value_json TEXT NOT NULL CHECK (json_valid(measured_value_json)),
  threshold_text TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pass','fail','exception')),
  evidence_reference TEXT NOT NULL,
  PRIMARY KEY (gate_run_id, gate_name)
) WITHOUT ROWID;

CREATE TABLE query_definition (
  query_id TEXT PRIMARY KEY,
  query_name TEXT NOT NULL UNIQUE,
  intent TEXT NOT NULL,
  sql_text TEXT NOT NULL,
  maximum_depth INTEGER,
  currentness_mode TEXT NOT NULL,
  evidence_required INTEGER NOT NULL CHECK (evidence_required IN (0,1)),
  safe_abstention_rule TEXT NOT NULL,
  created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE UNIQUE INDEX ux_canonical_alias_unique_preferred
  ON canonical_alias(normalized_alias)
  WHERE active=1 AND ambiguity_state='unique' AND preferred_flag=1;
CREATE INDEX idx_canonical_alias_normalized ON canonical_alias(normalized_alias, active, ambiguity_state);
CREATE INDEX idx_canonical_alias_entity ON canonical_alias(entity_id, active);
CREATE INDEX idx_assertion_subject_predicate_current ON assertion(subject_node_id, predicate, currentness_state, canonical_status, retrieval_eligible);
CREATE INDEX idx_assertion_object_predicate_current ON assertion(object_node_id, predicate, currentness_state, canonical_status, retrieval_eligible);
CREATE INDEX idx_assertion_source ON assertion(primary_source_id);
CREATE INDEX idx_assertion_valid_time ON assertion(valid_from, valid_to, currentness_state);
CREATE INDEX idx_assertion_legacy ON assertion(legacy_record_type, canonical_status);
CREATE UNIQUE INDEX ux_assertion_current_canonical_edge ON assertion(
  subject_node_id, legacy_predicate, object_node_id,
  ifnull(valid_from,''), ifnull(valid_to,''), scope_text
) WHERE canonical_status='canonical' AND currentness_state='current'
    AND object_node_id IS NOT NULL AND superseded_by_assertion_id IS NULL;
CREATE INDEX idx_assertion_evidence_evidence ON assertion_evidence(evidence_id, assertion_id);
CREATE INDEX idx_evidence_source ON evidence_item(source_id, source_version_id);
CREATE INDEX idx_domain_range_types ON edge_type_domain_range(source_node_type, target_node_type, edge_type_id);
CREATE UNIQUE INDEX ux_canonical_route_primary
  ON canonical_route(object_type, object_id)
  WHERE active=1 AND is_primary=1;
CREATE INDEX idx_canonical_route_object ON canonical_route(object_type, object_id, active, route_status);
CREATE INDEX idx_canonical_route_card ON canonical_route(card_id, active);
CREATE INDEX idx_search_document_object ON search_document(object_type, object_id, retrieval_eligible, currentness_state);
CREATE INDEX idx_quality_exception_object ON graph_quality_exception(object_type, object_id, active);
CREATE INDEX idx_ingestion_run_status ON ingestion_run(status, started_at);
CREATE INDEX idx_quarantine_status_reason ON quarantine_item(status, reason_code);
CREATE INDEX idx_audit_record ON audit_event(record_type, record_id, event_timestamp);
CREATE INDEX idx_audit_run ON audit_event(migration_or_ingestion_run_id, event_timestamp);

-- Query-backed indexes on preserved legacy tables.
CREATE INDEX IF NOT EXISTS idx_node_type_active ON node(node_type, active, canonical_name);
CREATE INDEX IF NOT EXISTS idx_node_name_normalized ON node(lower(trim(canonical_name)), active);
CREATE INDEX IF NOT EXISTS idx_edge_from_type_current ON edge(from_node_id, edge_type, to_node_id) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_edge_to_type_current ON edge(to_node_id, edge_type, from_node_id) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_edge_source_current ON edge(source_id, edge_type) WHERE superseded_by IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_edge_exact_current ON edge(
  from_node_id, edge_type, to_node_id,
  ifnull(description,''), evidence_posture, confidence, materiality,
  ifnull(validation_priority,''), ifnull(validation_owner,''), hitl_required, source_id
) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_claim_subject_current ON claim(subject_node_id, updated_at) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_claim_source_current ON claim(source_id, subject_node_id) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_claim_card_current ON claim(card_id, subject_node_id) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_alias_value_node ON alias(lower(trim(alias)), node_id);
CREATE INDEX IF NOT EXISTS idx_alias_node_source ON alias(node_id, source_id);
CREATE INDEX IF NOT EXISTS idx_validation_status_priority_due ON validation_queue(status, priority, due_date, owner);
CREATE INDEX IF NOT EXISTS idx_node_card_map_card ON node_card_map(card_id, node_id, role);

CREATE TRIGGER search_document_ai AFTER INSERT ON search_document BEGIN
  INSERT INTO search_fts(rowid,title,body,object_type,object_id)
  VALUES (new.search_rowid,new.title,new.body,new.object_type,new.object_id);
END;
CREATE TRIGGER search_document_ad AFTER DELETE ON search_document BEGIN
  INSERT INTO search_fts(search_fts,rowid,title,body,object_type,object_id)
  VALUES ('delete',old.search_rowid,old.title,old.body,old.object_type,old.object_id);
END;
CREATE TRIGGER search_document_au AFTER UPDATE ON search_document BEGIN
  INSERT INTO search_fts(search_fts,rowid,title,body,object_type,object_id)
  VALUES ('delete',old.search_rowid,old.title,old.body,old.object_type,old.object_id);
  INSERT INTO search_fts(rowid,title,body,object_type,object_id)
  VALUES (new.search_rowid,new.title,new.body,new.object_type,new.object_id);
END;

CREATE TRIGGER assertion_domain_range_bi BEFORE INSERT ON assertion
WHEN new.object_node_id IS NOT NULL AND new.legacy_predicate IS NOT NULL AND NOT EXISTS (
  SELECT 1
  FROM node s, node o, edge_type_domain_range dr
  WHERE s.node_id=new.subject_node_id
    AND o.node_id=new.object_node_id
    AND dr.edge_type_id=new.legacy_predicate
    AND dr.source_node_type=s.node_type
    AND dr.target_node_type=o.node_type
    AND dr.active=1
)
BEGIN SELECT RAISE(ABORT,'invalid assertion domain/range'); END;

CREATE TRIGGER assertion_domain_range_bu BEFORE UPDATE OF subject_node_id,object_node_id,legacy_predicate ON assertion
WHEN new.object_node_id IS NOT NULL AND new.legacy_predicate IS NOT NULL AND NOT EXISTS (
  SELECT 1
  FROM node s, node o, edge_type_domain_range dr
  WHERE s.node_id=new.subject_node_id
    AND o.node_id=new.object_node_id
    AND dr.edge_type_id=new.legacy_predicate
    AND dr.source_node_type=s.node_type
    AND dr.target_node_type=o.node_type
    AND dr.active=1
)
BEGIN SELECT RAISE(ABORT,'invalid assertion domain/range'); END;

CREATE TRIGGER assertion_self_loop_bi BEFORE INSERT ON assertion
WHEN new.object_node_id IS NOT NULL AND new.subject_node_id=new.object_node_id
 AND new.canonical_status='canonical' AND new.currentness_state='current' AND new.retrieval_eligible=1
 AND EXISTS (SELECT 1 FROM edge_type_registry r WHERE r.edge_type_id=new.legacy_predicate AND r.allow_self_loop=0)
BEGIN SELECT RAISE(ABORT,'self-loop prohibited for current canonical predicate'); END;

CREATE TRIGGER assertion_self_loop_bu BEFORE UPDATE OF subject_node_id,object_node_id,legacy_predicate,canonical_status,currentness_state,retrieval_eligible ON assertion
WHEN new.object_node_id IS NOT NULL AND new.subject_node_id=new.object_node_id
 AND new.canonical_status='canonical' AND new.currentness_state='current' AND new.retrieval_eligible=1
 AND EXISTS (SELECT 1 FROM edge_type_registry r WHERE r.edge_type_id=new.legacy_predicate AND r.allow_self_loop=0)
BEGIN SELECT RAISE(ABORT,'self-loop prohibited for current canonical predicate'); END;

CREATE TRIGGER edge_domain_range_bi BEFORE INSERT ON edge
WHEN NOT EXISTS (
  SELECT 1 FROM node s, node o, edge_type_domain_range dr
  WHERE s.node_id=new.from_node_id AND o.node_id=new.to_node_id
    AND dr.edge_type_id=new.edge_type
    AND dr.source_node_type=s.node_type AND dr.target_node_type=o.node_type AND dr.active=1
)
BEGIN SELECT RAISE(ABORT,'invalid edge domain/range'); END;

CREATE TRIGGER edge_domain_range_bu BEFORE UPDATE OF from_node_id,to_node_id,edge_type ON edge
WHEN NOT EXISTS (
  SELECT 1 FROM node s, node o, edge_type_domain_range dr
  WHERE s.node_id=new.from_node_id AND o.node_id=new.to_node_id
    AND dr.edge_type_id=new.edge_type
    AND dr.source_node_type=s.node_type AND dr.target_node_type=o.node_type AND dr.active=1
)
BEGIN SELECT RAISE(ABORT,'invalid edge domain/range'); END;

CREATE TRIGGER edge_self_loop_bi BEFORE INSERT ON edge
WHEN new.from_node_id=new.to_node_id
 AND EXISTS (SELECT 1 FROM edge_type_registry r WHERE r.edge_type_id=new.edge_type AND r.allow_self_loop=0)
BEGIN SELECT RAISE(ABORT,'self-loop prohibited for edge type'); END;

CREATE TRIGGER edge_self_loop_bu BEFORE UPDATE OF from_node_id,to_node_id,edge_type ON edge
WHEN new.from_node_id=new.to_node_id
 AND EXISTS (SELECT 1 FROM edge_type_registry r WHERE r.edge_type_id=new.edge_type AND r.allow_self_loop=0)
BEGIN SELECT RAISE(ABORT,'self-loop prohibited for edge type'); END;

CREATE VIEW v_current_assertion AS
SELECT * FROM assertion
WHERE canonical_status IN ('canonical','candidate')
  AND currentness_state='current'
  AND retrieval_eligible=1
  AND superseded_by_assertion_id IS NULL
  AND (valid_from IS NULL OR valid_from <= date('now'))
  AND (valid_to IS NULL OR valid_to > date('now'))
  AND validation_state NOT IN ('rejected','superseded');

CREATE VIEW v_historical_assertion AS
SELECT * FROM assertion
WHERE currentness_state='historical'
   OR canonical_status IN ('historical','historical_candidate')
   OR superseded_by_assertion_id IS NOT NULL
   OR valid_to IS NOT NULL;

CREATE VIEW v_canonical_edge AS
SELECT a.assertion_id,
       a.subject_node_id AS from_node_id,
       a.predicate AS relationship_family,
       a.legacy_predicate AS edge_type,
       a.object_node_id AS to_node_id,
       json_extract(a.qualifiers_json,'$.description') AS description,
       a.confidence_label AS confidence,
       a.materiality,
       a.validation_priority,
       a.validation_owner,
       a.hitl_required,
       a.primary_source_id AS source_id,
       a.valid_from,
       a.valid_to,
       a.recorded_at,
       a.currentness_state,
       a.canonical_status
FROM v_current_assertion a
WHERE a.object_node_id IS NOT NULL AND a.canonical_status='canonical';

CREATE VIEW v_assertion_evidence_chain AS
SELECT a.assertion_id,a.subject_node_id,a.predicate,a.legacy_predicate,a.object_node_id,a.object_literal,
       a.canonical_status,a.currentness_state,a.validation_state,a.confidence_score,
       ae.evidence_relationship,e.evidence_id,e.source_id,e.locator_kind,e.locator_value,
       e.page_number,e.section_name,e.paragraph_number,e.row_reference,e.slide_number,
       e.transcript_timestamp,e.direct_quote,e.evidence_directness,e.source_authority_rank,
       s.source_name,s.source_date,s.source_posture
FROM assertion a
JOIN assertion_evidence ae ON ae.assertion_id=a.assertion_id
JOIN evidence_item e ON e.evidence_id=ae.evidence_id
JOIN source s ON s.source_id=e.source_id;

CREATE VIEW v_alias_resolution AS
SELECT ca.normalized_alias,
       COUNT(DISTINCT ca.entity_id) AS candidate_count,
       MIN(ca.ambiguity_state) AS ambiguity_state,
       GROUP_CONCAT(DISTINCT ca.entity_id) AS candidate_entity_ids
FROM canonical_alias ca
JOIN node n ON n.node_id=ca.entity_id
WHERE ca.active=1 AND n.active=1
GROUP BY ca.normalized_alias;

CREATE VIEW v_node_card_map_derived AS
SELECT object_id AS node_id,card_id,route_role AS role
FROM canonical_route
WHERE object_type='node' AND active=1 AND card_id IS NOT NULL;

CREATE VIEW v_claim_card_route_derived AS
SELECT object_id AS claim_id,card_id
FROM canonical_route
WHERE object_type='claim' AND active=1 AND is_primary=1 AND card_id IS NOT NULL;

CREATE VIEW v_conflicting_assertions AS
SELECT a1.assertion_id AS assertion_id_1,a2.assertion_id AS assertion_id_2,
       a1.subject_node_id,a1.predicate,a1.object_node_id AS object_1,a2.object_node_id AS object_2,
       a1.object_literal AS literal_1,a2.object_literal AS literal_2
FROM v_current_assertion a1
JOIN v_current_assertion a2
  ON a1.subject_node_id=a2.subject_node_id
 AND a1.predicate=a2.predicate
 AND a1.assertion_id<a2.assertion_id
WHERE ifnull(a1.object_node_id,a1.object_literal) <> ifnull(a2.object_node_id,a2.object_literal);
