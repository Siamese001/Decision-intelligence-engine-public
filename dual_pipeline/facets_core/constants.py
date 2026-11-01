"""Constants, schema SQL, and vocabulary for graph facets."""
from __future__ import annotations

CARDS_REFACTOR_POLICY_VERSION = "2026-07-27.1"
CARDS_REFACTOR_MIGRATION_ID = "0011_cards_refactoring"
SEMANTIC_DOMAIN_VOCABULARY = "semantic_domain"
SEMANTIC_DOMAIN_RULE_ID = "nee-semantic-domain-by-subject-node-type-1.0.0"
TOPIC_VOCABULARY = "topic"
TOPIC_ROUTE_RULE_ID = "nee-topic-from-editorial-route-1.0.0"
FACET_RULE_VERSION = 1

SEMANTIC_DOMAINS: dict[str, str] = {
    "architecture_and_capability": (
        "Architecture, technical anchors, reusable patterns, capabilities, and "
        "operating-model concepts."
    ),
    "commercial_and_contract": (
        "Statements whose principal organizing context is commercial, pricing, "
        "statement-of-work, or contract authority."
    ),
    "evidence_and_provenance": (
        "Sources, source packages, evidence lineage, and provenance controls."
    ),
    "governance_and_risk": (
        "Risks, controls, governance requirements, and decision guardrails."
    ),
    "knowledge_and_taxonomy": (
        "Editorial views, glossary concepts, and knowledge-organization metadata."
    ),
    "people_and_organization": (
        "People, organizations, audiences, roles, staffing, and organizational models."
    ),
    "portfolio_and_delivery": (
        "Programs, workstreams, product lanes, deliverables, opportunities, and strategy."
    ),
}

SEMANTIC_DOMAIN_BY_NODE_TYPE: dict[str, str] = {
    "audience": "people_and_organization",
    "capability": "architecture_and_capability",
    "card": "knowledge_and_taxonomy",
    "control": "governance_and_risk",
    "control_protocol": "architecture_and_capability",
    "deliverable": "portfolio_and_delivery",
    "deprecated_stakeholder_fragment": "people_and_organization",
    "external_concept": "architecture_and_capability",
    "glossary_term": "knowledge_and_taxonomy",
    "internal_person": "people_and_organization",
    "operating_model": "architecture_and_capability",
    "opportunity": "portfolio_and_delivery",
    "org": "people_and_organization",
    "org_model": "people_and_organization",
    "pattern": "architecture_and_capability",
    "pricing_model": "commercial_and_contract",
    "product_family": "portfolio_and_delivery",
    "product_lane": "portfolio_and_delivery",
    "program": "portfolio_and_delivery",
    "risk": "governance_and_risk",
    "role_group": "people_and_organization",
    "source": "evidence_and_provenance",
    "source_lineage": "evidence_and_provenance",
    "source_pack": "evidence_and_provenance",
    "sow": "commercial_and_contract",
    "staffing_footprint": "people_and_organization",
    "staffing_pool": "people_and_organization",
    "stakeholder": "people_and_organization",
    "strategy": "portfolio_and_delivery",
    "technical_anchor": "architecture_and_capability",
    "workstream": "portfolio_and_delivery",
}

FACET_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facet_derivation_rule (
  derivation_rule_id TEXT NOT NULL CHECK (trim(derivation_rule_id) <> ''),
  rule_version INTEGER NOT NULL CHECK (rule_version > 0),
  policy_version TEXT NOT NULL CHECK (trim(policy_version) <> ''),
  rule_definition_json TEXT NOT NULL CHECK (json_valid(rule_definition_json)),
  rule_digest TEXT NOT NULL CHECK (length(rule_digest)=64),
  applicability_policy_digest TEXT NOT NULL CHECK (
    length(applicability_policy_digest)=64
  ),
  approval_reference TEXT NOT NULL
    REFERENCES governance_approval(approval_reference)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL CHECK (trim(created_at) <> ''),
  retired_at TEXT,
  PRIMARY KEY (derivation_rule_id,rule_version),
  CHECK ((active=1 AND retired_at IS NULL) OR active=0)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS object_facet_assignment (
  assignment_id TEXT PRIMARY KEY CHECK (trim(assignment_id) <> ''),
  object_type TEXT NOT NULL CHECK (object_type IN ('node','claim','assertion')),
  object_id TEXT NOT NULL CHECK (trim(object_id) <> ''),
  vocabulary TEXT NOT NULL,
  value TEXT NOT NULL,
  assignment_method TEXT NOT NULL CHECK (
    assignment_method IN ('explicit','rule_derived','inherited','reviewed')
  ),
  review_state TEXT NOT NULL CHECK (
    review_state IN ('approved','needs_review')
  ),
  derivation_rule_id TEXT,
  derivation_rule_version INTEGER,
  rationale TEXT NOT NULL CHECK (trim(rationale) <> ''),
  approval_reference TEXT NOT NULL
    REFERENCES governance_approval(approval_reference)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL CHECK (trim(created_at) <> ''),
  retired_at TEXT,
  FOREIGN KEY (vocabulary,value)
    REFERENCES controlled_value_registry(vocabulary,value)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  FOREIGN KEY (vocabulary,object_type)
    REFERENCES property_registry(property_name,applies_to)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  FOREIGN KEY (derivation_rule_id,derivation_rule_version)
    REFERENCES facet_derivation_rule(derivation_rule_id,rule_version)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  CHECK (
    (
      assignment_method IN ('rule_derived','inherited')
      AND derivation_rule_id IS NOT NULL
      AND derivation_rule_version IS NOT NULL
    )
    OR (
      assignment_method IN ('explicit','reviewed')
      AND derivation_rule_id IS NULL
      AND derivation_rule_version IS NULL
    )
  ),
  CHECK ((active=1 AND retired_at IS NULL) OR active=0)
) WITHOUT ROWID;

CREATE UNIQUE INDEX IF NOT EXISTS ux_object_facet_assignment_active
  ON object_facet_assignment(object_type,object_id,vocabulary,value)
  WHERE active=1;

CREATE INDEX IF NOT EXISTS idx_object_facet_assignment_lookup
  ON object_facet_assignment(object_type,object_id,vocabulary,active,value);

CREATE TABLE IF NOT EXISTS facet_coverage_exception (
  exception_id TEXT PRIMARY KEY CHECK (trim(exception_id) <> ''),
  object_type TEXT NOT NULL CHECK (object_type IN ('node','claim','assertion')),
  object_id TEXT NOT NULL CHECK (trim(object_id) <> ''),
  vocabulary TEXT NOT NULL CHECK (trim(vocabulary) <> ''),
  reason TEXT NOT NULL CHECK (trim(reason) <> ''),
  owner TEXT NOT NULL CHECK (trim(owner) <> ''),
  review_due_date TEXT NOT NULL CHECK (
    review_due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
  ),
  approval_reference TEXT NOT NULL
    REFERENCES governance_approval(approval_reference)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL CHECK (trim(created_at) <> ''),
  resolved_at TEXT,
  CHECK ((active=1 AND resolved_at IS NULL) OR active=0)
) WITHOUT ROWID;

CREATE UNIQUE INDEX IF NOT EXISTS ux_facet_coverage_exception_active
  ON facet_coverage_exception(object_type,object_id,vocabulary)
  WHERE active=1;

CREATE TABLE IF NOT EXISTS card_view_definition (
  card_view_id TEXT PRIMARY KEY CHECK (trim(card_view_id) <> ''),
  card_id TEXT NOT NULL REFERENCES card(card_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  view_version INTEGER NOT NULL CHECK (view_version > 0),
  view_kind TEXT NOT NULL CHECK (
    view_kind IN ('route_membership','facet_query','explicit_objects')
  ),
  selector_schema_version TEXT NOT NULL CHECK (
    trim(selector_schema_version) <> ''
  ),
  selector_json TEXT NOT NULL CHECK (json_valid(selector_json)),
  selector_digest TEXT NOT NULL CHECK (length(selector_digest)=64),
  authority_mode TEXT NOT NULL CHECK (
    authority_mode IN ('approved_only','candidate_review')
  ),
  classification_ceiling TEXT NOT NULL CHECK (
    classification_ceiling IN (
      'external_approved','internal','confidential','restricted'
    )
  ),
  eligible_audiences_json TEXT NOT NULL CHECK (
    json_valid(eligible_audiences_json)
  ),
  editorial_owner TEXT NOT NULL CHECK (trim(editorial_owner) <> ''),
  view_state TEXT NOT NULL CHECK (
    view_state IN ('draft','active','retired')
  ),
  approval_reference TEXT NOT NULL
    REFERENCES governance_approval(approval_reference)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at TEXT NOT NULL CHECK (trim(created_at) <> ''),
  retired_at TEXT,
  UNIQUE (card_id,view_version),
  CHECK ((active=1 AND view_state='active' AND retired_at IS NULL) OR active=0)
) WITHOUT ROWID;

CREATE UNIQUE INDEX IF NOT EXISTS ux_card_view_definition_active
  ON card_view_definition(card_id)
  WHERE active=1;

CREATE TRIGGER IF NOT EXISTS facet_derivation_rule_approval_guard_bi
BEFORE INSERT ON facet_derivation_rule
WHEN NOT EXISTS (
  SELECT 1 FROM governance_approval ga
   WHERE ga.approval_reference=new.approval_reference
     AND ga.status='approved'
     AND ga.active=1
)
BEGIN
  SELECT RAISE(ABORT,'facet derivation rule requires an active approval');
END;

CREATE TRIGGER IF NOT EXISTS object_facet_assignment_object_guard_bi
BEFORE INSERT ON object_facet_assignment
WHEN
  (new.object_type='node' AND NOT EXISTS (
    SELECT 1 FROM node WHERE node_id=new.object_id
  ))
  OR (new.object_type='claim' AND NOT EXISTS (
    SELECT 1 FROM claim WHERE claim_id=new.object_id
  ))
  OR (new.object_type='assertion' AND NOT EXISTS (
    SELECT 1 FROM assertion WHERE assertion_id=new.object_id
  ))
BEGIN
  SELECT RAISE(ABORT,'object_facet_assignment target does not exist');
END;

CREATE TRIGGER IF NOT EXISTS object_facet_assignment_policy_guard_bi
BEFORE INSERT ON object_facet_assignment
WHEN NOT EXISTS (
       SELECT 1 FROM controlled_value_registry cv
        WHERE cv.vocabulary=new.vocabulary
          AND cv.value=new.value
          AND cv.active=1
     )
  OR NOT EXISTS (
       SELECT 1 FROM property_registry pr
        WHERE pr.property_name=new.vocabulary
          AND pr.applies_to=new.object_type
          AND pr.data_type='controlled_text_many'
          AND pr.controlled_vocabulary=new.vocabulary
     )
  OR (
       new.derivation_rule_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM facet_derivation_rule fr
          WHERE fr.derivation_rule_id=new.derivation_rule_id
            AND fr.rule_version=new.derivation_rule_version
            AND fr.active=1
       )
     )
  OR NOT EXISTS (
       SELECT 1 FROM governance_approval ga
        WHERE ga.approval_reference=new.approval_reference
          AND ga.status='approved'
          AND ga.active=1
     )
BEGIN
  SELECT RAISE(ABORT,'object_facet_assignment violates the active facet policy');
END;

CREATE TRIGGER IF NOT EXISTS object_facet_assignment_immutable_bu
BEFORE UPDATE OF
  object_type,object_id,vocabulary,value,assignment_method,review_state,
  derivation_rule_id,derivation_rule_version,rationale,approval_reference,created_at
ON object_facet_assignment
BEGIN
  SELECT RAISE(ABORT,'object facet assignment is immutable; retire and replace');
END;

CREATE TRIGGER IF NOT EXISTS object_facet_assignment_immutable_bd
BEFORE DELETE ON object_facet_assignment
BEGIN
  SELECT RAISE(ABORT,'object facet assignment cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS facet_coverage_exception_object_guard_bi
BEFORE INSERT ON facet_coverage_exception
WHEN
  (new.object_type='node' AND NOT EXISTS (
    SELECT 1 FROM node WHERE node_id=new.object_id
  ))
  OR (new.object_type='claim' AND NOT EXISTS (
    SELECT 1 FROM claim WHERE claim_id=new.object_id
  ))
  OR (new.object_type='assertion' AND NOT EXISTS (
    SELECT 1 FROM assertion WHERE assertion_id=new.object_id
  ))
  OR NOT EXISTS (
    SELECT 1 FROM governance_approval ga
     WHERE ga.approval_reference=new.approval_reference
       AND ga.status='approved'
       AND ga.active=1
  )
BEGIN
  SELECT RAISE(ABORT,'facet coverage exception violates object or approval policy');
END;

CREATE TRIGGER IF NOT EXISTS facet_derivation_rule_immutable_bu
BEFORE UPDATE OF
  derivation_rule_id,rule_version,policy_version,rule_definition_json,rule_digest,
  applicability_policy_digest,approval_reference,created_at
ON facet_derivation_rule
BEGIN
  SELECT RAISE(ABORT,'facet derivation rule definition is immutable');
END;

CREATE TRIGGER IF NOT EXISTS facet_derivation_rule_immutable_bd
BEFORE DELETE ON facet_derivation_rule
BEGIN
  SELECT RAISE(ABORT,'facet derivation rule cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS card_view_definition_approval_guard_bi
BEFORE INSERT ON card_view_definition
WHEN NOT EXISTS (
  SELECT 1 FROM governance_approval ga
   WHERE ga.approval_reference=new.approval_reference
     AND ga.status='approved'
     AND ga.active=1
)
BEGIN
  SELECT RAISE(ABORT,'card view definition requires an active approval');
END;

CREATE TRIGGER IF NOT EXISTS card_view_definition_immutable_bu
BEFORE UPDATE OF
  card_id,view_version,view_kind,selector_schema_version,selector_json,
  selector_digest,authority_mode,classification_ceiling,eligible_audiences_json,
  editorial_owner,approval_reference,created_at
ON card_view_definition
BEGIN
  SELECT RAISE(ABORT,'card view definition is immutable; create a new version');
END;

CREATE TRIGGER IF NOT EXISTS card_view_definition_immutable_bd
BEFORE DELETE ON card_view_definition
BEGIN
  SELECT RAISE(ABORT,'card view definition cannot be deleted');
END;
"""

