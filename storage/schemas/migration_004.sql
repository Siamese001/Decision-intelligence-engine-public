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
