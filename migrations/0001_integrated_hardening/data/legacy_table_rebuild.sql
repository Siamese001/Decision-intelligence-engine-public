-- NEE V48 implementation-normalized legacy reconstruction.
-- Approved semantic outcome is unchanged: all legacy values and rows are preserved.
-- Dependent views are managed by the migration orchestrator.
-- Closed priority allowlist: ["P0", "P1", "P1 currentness validation", "P2", "P2 currentness", "P2 currentness validation", "P2 external-safe wording", "P3", "currentness validation", "external-safe wording verification"]
CREATE TABLE node_v48_new (
  node_id TEXT PRIMARY KEY CHECK (trim(node_id)<>''),
  node_type TEXT NOT NULL CHECK (trim(node_type)<>''),
  canonical_name TEXT NOT NULL CHECK (trim(canonical_name)<>''),
  display_name TEXT,
  description TEXT,
  status TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at TEXT NOT NULL CHECK (trim(created_at)<>''),
  updated_at TEXT NOT NULL CHECK (trim(updated_at)<>'')
);
INSERT INTO node_v48_new SELECT node_id,node_type,canonical_name,display_name,description,status,coalesce(active,1),created_at,updated_at FROM node;
DROP TABLE node;
ALTER TABLE node_v48_new RENAME TO node;

CREATE TABLE source_v48_new (
  source_id TEXT PRIMARY KEY CHECK (trim(source_id)<>''),
  source_type TEXT NOT NULL CHECK (trim(source_type)<>''),
  source_name TEXT NOT NULL CHECK (trim(source_name)<>''),
  source_date TEXT,
  authority_rank INTEGER NOT NULL CHECK (authority_rank BETWEEN 1 AND 9),
  source_posture TEXT NOT NULL CHECK (trim(source_posture)<>''),
  cold_archive_ref TEXT,
  notes TEXT,
  created_at TEXT NOT NULL CHECK (trim(created_at)<>'')
);
INSERT INTO source_v48_new SELECT * FROM source;
DROP TABLE source;
ALTER TABLE source_v48_new RENAME TO source;

CREATE TABLE card_v48_new (
  card_id TEXT PRIMARY KEY CHECK (trim(card_id)<>''),
  card_name TEXT NOT NULL CHECK (trim(card_name)<>''),
  card_path TEXT NOT NULL CHECK (trim(card_path)<>''),
  canonical_topic TEXT NOT NULL CHECK (trim(canonical_topic)<>''),
  description TEXT
);
INSERT INTO card_v48_new SELECT * FROM card;
DROP TABLE card;
ALTER TABLE card_v48_new RENAME TO card;

CREATE TABLE edge_v48_new (
  edge_id TEXT PRIMARY KEY CHECK (trim(edge_id)<>''),
  from_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  edge_type TEXT NOT NULL CHECK (trim(edge_type)<>''),
  to_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  description TEXT,
  evidence_posture TEXT NOT NULL CHECK (trim(evidence_posture)<>''),
  confidence TEXT NOT NULL CHECK (trim(confidence)<>''),
  materiality TEXT NOT NULL CHECK (trim(materiality)<>''),
  validation_priority TEXT CHECK (validation_priority IS NULL OR validation_priority IN ('P0','P1','P1 currentness validation','P2','P2 currentness','P2 currentness validation','P2 external-safe wording','P3','currentness validation','external-safe wording verification')),
  validation_owner TEXT,
  hitl_required INTEGER NOT NULL DEFAULT 0 CHECK (hitl_required IN (0,1)),
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  created_at TEXT NOT NULL CHECK (trim(created_at)<>''),
  updated_at TEXT NOT NULL CHECK (trim(updated_at)<>''),
  superseded_by TEXT REFERENCES edge(edge_id) DEFERRABLE INITIALLY DEFERRED,
  CHECK (superseded_by IS NULL OR superseded_by<>edge_id)
);
INSERT INTO edge_v48_new SELECT edge_id,from_node_id,edge_type,to_node_id,description,evidence_posture,confidence,materiality,validation_priority,validation_owner,coalesce(hitl_required,0),source_id,created_at,updated_at,superseded_by FROM edge;
DROP TABLE edge;
ALTER TABLE edge_v48_new RENAME TO edge;

CREATE TABLE claim_v48_new (
  claim_id TEXT PRIMARY KEY CHECK (trim(claim_id)<>''),
  subject_node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  claim_text TEXT NOT NULL CHECK (trim(claim_text)<>''),
  evidence_posture TEXT NOT NULL CHECK (trim(evidence_posture)<>''),
  confidence TEXT NOT NULL CHECK (trim(confidence)<>''),
  materiality TEXT NOT NULL CHECK (trim(materiality)<>''),
  validation_priority TEXT CHECK (validation_priority IS NULL OR validation_priority IN ('P0','P1','P1 currentness validation','P2','P2 currentness','P2 currentness validation','P2 external-safe wording','P3','currentness validation','external-safe wording verification')),
  validation_owner TEXT,
  hitl_required INTEGER NOT NULL DEFAULT 0 CHECK (hitl_required IN (0,1)),
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  card_id TEXT NOT NULL REFERENCES card(card_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  created_at TEXT NOT NULL CHECK (trim(created_at)<>''),
  updated_at TEXT NOT NULL CHECK (trim(updated_at)<>''),
  superseded_by TEXT REFERENCES claim(claim_id) DEFERRABLE INITIALLY DEFERRED,
  CHECK (superseded_by IS NULL OR superseded_by<>claim_id)
);
INSERT INTO claim_v48_new SELECT claim_id,subject_node_id,claim_text,evidence_posture,confidence,materiality,validation_priority,validation_owner,coalesce(hitl_required,0),source_id,card_id,created_at,updated_at,superseded_by FROM claim;
DROP TABLE claim;
ALTER TABLE claim_v48_new RENAME TO claim;

CREATE TABLE alias_v48_new (
  alias_id TEXT PRIMARY KEY CHECK (trim(alias_id)<>''),
  node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  alias TEXT NOT NULL CHECK (trim(alias)<>''),
  confidence TEXT NOT NULL CHECK (trim(confidence)<>''),
  evidence_posture TEXT,
  source_id TEXT NOT NULL REFERENCES source(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  created_at TEXT NOT NULL CHECK (trim(created_at)<>'')
);
INSERT INTO alias_v48_new SELECT alias_id,node_id,alias,confidence,evidence_posture,source_id,created_at FROM alias;
DROP TABLE alias;
ALTER TABLE alias_v48_new RENAME TO alias;

CREATE TABLE node_card_map_v48_new (
  node_id TEXT NOT NULL REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  card_id TEXT NOT NULL REFERENCES card(card_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK (trim(role)<>''),
  PRIMARY KEY (node_id,card_id)
) WITHOUT ROWID;
INSERT INTO node_card_map_v48_new SELECT * FROM node_card_map;
DROP TABLE node_card_map;
ALTER TABLE node_card_map_v48_new RENAME TO node_card_map;

CREATE TABLE validation_queue_v48_new (
  validation_id TEXT PRIMARY KEY CHECK (trim(validation_id)<>''),
  node_id TEXT REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  edge_id TEXT REFERENCES edge(edge_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  claim_id TEXT REFERENCES claim(claim_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  validation_question TEXT NOT NULL CHECK (trim(validation_question)<>''),
  owner TEXT NOT NULL CHECK (trim(owner)<>''),
  priority TEXT NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  due_date TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','closed')),
  resolution TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL CHECK (trim(created_at)<>''),
  CHECK (due_date IS NULL OR date(due_date) IS NOT NULL)
);
INSERT INTO validation_queue_v48_new SELECT validation_id,node_id,edge_id,claim_id,validation_question,owner,priority,due_date,status,resolution,resolved_at,created_at FROM validation_queue;
DROP TABLE validation_queue;
ALTER TABLE validation_queue_v48_new RENAME TO validation_queue;
