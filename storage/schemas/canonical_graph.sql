-- Canonical Graph Nodes
CREATE TABLE IF NOT EXISTS canonical_graph_nodes (
    node_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    properties TEXT,
    strategic_value REAL NOT NULL DEFAULT 0.0,
    evidence_quality REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    tacticality REAL NOT NULL DEFAULT 0.0,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'CLIENT_SAFE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_canonical_evidence 
    ON canonical_graph_nodes(evidence_id);
CREATE INDEX IF NOT EXISTS idx_canonical_policy_filter 
    ON canonical_graph_nodes(is_quarantined, namespace, strategic_value);

-- Relational Graph Edges
CREATE TABLE IF NOT EXISTS canonical_graph_edges (
    edge_id TEXT PRIMARY KEY,
    from_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'CLIENT_SAFE',
    properties TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(from_node_id) REFERENCES canonical_graph_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(to_node_id) REFERENCES canonical_graph_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_canonical_edges_from 
    ON canonical_graph_edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_canonical_edges_to 
    ON canonical_graph_edges(to_node_id);
CREATE INDEX IF NOT EXISTS idx_canonical_edges_pred 
    ON canonical_graph_edges(predicate);

-- Backward-compatible view for graph_edges
CREATE VIEW IF NOT EXISTS graph_edges AS SELECT * FROM canonical_graph_edges;

CREATE TRIGGER IF NOT EXISTS trg_graph_edges_insert INSTEAD OF INSERT ON graph_edges BEGIN
    INSERT INTO canonical_graph_edges (
        edge_id, from_node_id, predicate, to_node_id, weight, confidence,
        is_quarantined, quarantine_reason, namespace, properties, created_at, updated_at
    ) VALUES (
        new.edge_id, new.from_node_id, new.predicate, new.to_node_id, new.weight, new.confidence,
        new.is_quarantined, new.quarantine_reason, new.namespace, new.properties, new.created_at, new.updated_at
    );
END;

-- Backward-compatible view for node
CREATE VIEW IF NOT EXISTS node AS 
SELECT 
    node_id,
    type AS node_type,
    name AS canonical_name,
    name AS display_name,
    properties AS description,
    'CONFIRMED' AS status,
    1 AS active,
    created_at,
    updated_at
FROM canonical_graph_nodes;

CREATE TRIGGER IF NOT EXISTS trg_node_insert INSTEAD OF INSERT ON node BEGIN
    INSERT INTO canonical_graph_nodes (
        node_id, evidence_id, type, name, properties, created_at, updated_at
    ) VALUES (
        new.node_id, 'ev_injected', new.node_type, new.canonical_name, new.description, new.created_at, new.updated_at
    );
END;

-- W2 Semantic Spine First-Class Assertions
CREATE TABLE IF NOT EXISTS assertion (
    assertion_id TEXT PRIMARY KEY,
    subject_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_node_id TEXT,
    literal_value TEXT,
    assertion_type TEXT NOT NULL CHECK(assertion_type IN ('DECISION', 'RISK', 'DEPENDENCY', 'TECHNICAL', 'STRATEGIC', 'SUMMARY')),
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK(status IN ('PROPOSED', 'CONFIRMED', 'SUPERSEDED', 'RETRACTED')),
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stale_after TEXT,
    namespace TEXT NOT NULL DEFAULT 'CLIENT_SAFE',
    qualifiers_json TEXT,
    allowed_audiences_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(subject_node_id) REFERENCES canonical_graph_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(object_node_id) REFERENCES canonical_graph_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assertion_subject ON assertion(subject_node_id);
CREATE INDEX IF NOT EXISTS idx_assertion_status ON assertion(status);
CREATE INDEX IF NOT EXISTS idx_assertion_type ON assertion(assertion_type);
CREATE INDEX IF NOT EXISTS idx_assertion_subj_pred ON assertion(subject_node_id, predicate);
CREATE INDEX IF NOT EXISTS idx_assertion_qualifiers_card ON assertion(json_extract(qualifiers_json, '$.card_id')) WHERE qualifiers_json IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_assertion_allowed_audiences ON assertion(json_extract(allowed_audiences_json, '$[0]')) WHERE allowed_audiences_json IS NOT NULL;

-- Multi-Point Assertion Evidence Binding
CREATE TABLE IF NOT EXISTS assertion_evidence (
    assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    excerpt_text TEXT NOT NULL,
    relevance_score REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (assertion_id, evidence_id)
);

-- Full-Text Search FTS5 Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS canonical_nodes_fts USING fts5(
    node_id UNINDEXED,
    name,
    properties,
    content='canonical_graph_nodes',
    content_rowid='rowid'
);

-- Auto-sync triggers for FTS5
CREATE TRIGGER IF NOT EXISTS canonical_nodes_ai AFTER INSERT ON canonical_graph_nodes BEGIN
    INSERT INTO canonical_nodes_fts(rowid, node_id, name, properties) 
    VALUES (new.rowid, new.node_id, new.name, new.properties);
END;
CREATE TRIGGER IF NOT EXISTS canonical_nodes_ad AFTER DELETE ON canonical_graph_nodes BEGIN
    INSERT INTO canonical_nodes_fts(canonical_nodes_fts, rowid, node_id, name, properties) 
    VALUES ('delete', old.rowid, old.node_id, old.name, old.properties);
END;
CREATE TRIGGER IF NOT EXISTS canonical_nodes_au AFTER UPDATE ON canonical_graph_nodes BEGIN
    INSERT INTO canonical_nodes_fts(canonical_nodes_fts, rowid, node_id, name, properties) 
    VALUES ('delete', old.rowid, old.node_id, old.name, old.properties);
    INSERT INTO canonical_nodes_fts(rowid, node_id, name, properties) 
    VALUES (new.rowid, new.node_id, new.name, new.properties);
END;

-- Executive Decision Financial & Capability Impact
CREATE TABLE IF NOT EXISTS executive_decision_impact (
    impact_id TEXT PRIMARY KEY,
    decision_node_id TEXT NOT NULL,
    capability_l1 TEXT NOT NULL,
    capability_l2 TEXT,
    financial_value_usd REAL NOT NULL DEFAULT 0.0,
    capex_opex_classification TEXT NOT NULL DEFAULT 'HYBRID' CHECK(capex_opex_classification IN ('CAPEX', 'OPEX', 'HYBRID')),
    risk_blast_radius_tier TEXT NOT NULL DEFAULT 'MEDIUM' CHECK(risk_blast_radius_tier IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    downstream_affected_systems TEXT,
    strategic_kpi_linked TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(decision_node_id) REFERENCES canonical_graph_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exec_impact_decision ON executive_decision_impact(decision_node_id, capability_l1);
CREATE INDEX IF NOT EXISTS idx_exec_impact_tier ON executive_decision_impact(risk_blast_radius_tier, financial_value_usd);

-- Strategic Value Streams Bridging Tech Assets to Executive Outcomes
CREATE TABLE IF NOT EXISTS strategic_value_stream (
    stream_id TEXT PRIMARY KEY,
    stream_name TEXT NOT NULL,
    business_domain TEXT NOT NULL,
    lead_executive_sponsor TEXT NOT NULL,
    underlying_technical_assets TEXT,
    revenue_impact_tier TEXT NOT NULL DEFAULT 'CORE_REVENUE' CHECK(revenue_impact_tier IN ('CORE_REVENUE', 'EFFICIENCY_SAVINGS', 'REGULATORY_COMPLIANCE')),
    maturity_score REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_value_stream_domain ON strategic_value_stream(business_domain, revenue_impact_tier);

-- Reusable Thought Leadership Blueprints & Publication Frameworks
CREATE TABLE IF NOT EXISTS thought_leadership_blueprint (
    blueprint_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    target_publication TEXT NOT NULL,
    abstract_concept TEXT NOT NULL,
    anonymized_industry_patterns TEXT,
    architectural_exhibits TEXT,
    executive_takeaway TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'READY_FOR_PUBLICATION', 'DELIVERED')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_thought_leadership_status ON thought_leadership_blueprint(status, target_publication);

-- Canonical Graph Nodes History & Time-Travel Versioning
CREATE TABLE IF NOT EXISTS canonical_graph_nodes_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    properties TEXT,
    strategic_value REAL NOT NULL DEFAULT 0.0,
    evidence_quality REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    tacticality REAL NOT NULL DEFAULT 0.0,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'CLIENT_SAFE',
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    operation TEXT NOT NULL CHECK(operation IN ('UPDATE', 'DELETE')),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nodes_history_node 
    ON canonical_graph_nodes_history(node_id, recorded_at);

CREATE TRIGGER IF NOT EXISTS trg_canonical_nodes_bu
BEFORE UPDATE ON canonical_graph_nodes BEGIN
    INSERT INTO canonical_graph_nodes_history (
        node_id, evidence_id, type, name, properties, strategic_value,
        evidence_quality, confidence, tacticality, is_quarantined,
        quarantine_reason, namespace, created_at, updated_at, operation
    ) VALUES (
        old.node_id, old.evidence_id, old.type, old.name, old.properties, old.strategic_value,
        old.evidence_quality, old.confidence, old.tacticality, old.is_quarantined,
        old.quarantine_reason, old.namespace, old.created_at, old.updated_at, 'UPDATE'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_canonical_nodes_bd
BEFORE DELETE ON canonical_graph_nodes BEGIN
    INSERT INTO canonical_graph_nodes_history (
        node_id, evidence_id, type, name, properties, strategic_value,
        evidence_quality, confidence, tacticality, is_quarantined,
        quarantine_reason, namespace, created_at, updated_at, operation
    ) VALUES (
        old.node_id, old.evidence_id, old.type, old.name, old.properties, old.strategic_value,
        old.evidence_quality, old.confidence, old.tacticality, old.is_quarantined,
        old.quarantine_reason, old.namespace, old.created_at, old.updated_at, 'DELETE'
    );
END;

