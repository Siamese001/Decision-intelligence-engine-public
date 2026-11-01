CREATE TABLE IF NOT EXISTS shadow_graph_nodes (
    node_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    target_entity_hmac TEXT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('STAKEHOLDER_DYNAMICS', 'DECISION_INFLUENCE', 'ADOPTION_RISK', 'BLOCKER')),
    properties TEXT,
    strategic_value REAL NOT NULL DEFAULT 0.0,
    tacticality REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    stealth_score REAL NOT NULL DEFAULT 1.0,
    namespace TEXT NOT NULL DEFAULT 'INTERNAL_ADVISORY',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shadow_evidence 
    ON shadow_graph_nodes(evidence_id);
CREATE INDEX IF NOT EXISTS idx_shadow_target_hmac 
    ON shadow_graph_nodes(target_entity_hmac);
CREATE INDEX IF NOT EXISTS idx_shadow_policy_filter 
    ON shadow_graph_nodes(is_quarantined, namespace, category);

CREATE TABLE IF NOT EXISTS shadow_graph_edges (
    edge_id TEXT PRIMARY KEY,
    from_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'INTERNAL_ADVISORY',
    properties TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(from_node_id) REFERENCES shadow_graph_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(to_node_id) REFERENCES shadow_graph_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shadow_edges_from 
    ON shadow_graph_edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_shadow_edges_to 
    ON shadow_graph_edges(to_node_id);
CREATE INDEX IF NOT EXISTS idx_shadow_edges_pred 
    ON shadow_graph_edges(predicate);

-- Informal Organizational Power Dynamics & Stakeholder Stance (Rule C-19 Protected)
CREATE TABLE IF NOT EXISTS stakeholder_influence_map (
    influence_id TEXT PRIMARY KEY,
    stakeholder_name_hmac TEXT NOT NULL,
    informal_role TEXT NOT NULL CHECK(informal_role IN ('INFORMAL_VETO', 'COGNITIVE_SPONSOR', 'TECHNICAL_GATEKEEPER', 'CHAMPION', 'SKEPTIC')),
    title_authority_gap TEXT NOT NULL CHECK(title_authority_gap IN ('HIGH_INFLUENCE_LOW_TITLE', 'HIGH_TITLE_LOW_INFLUENCE', 'ALIGNED')),
    sentiment_trajectory TEXT NOT NULL CHECK(sentiment_trajectory IN ('WARMING', 'STABLE', 'RESISTANT', 'SKEPTICAL')),
    single_point_of_failure_risk INTEGER NOT NULL DEFAULT 0,
    advisory_notes_encrypted TEXT,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    stealth_score REAL NOT NULL DEFAULT 1.0,
    namespace TEXT NOT NULL DEFAULT 'INTERNAL_ADVISORY',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_influence_role ON stakeholder_influence_map(informal_role, sentiment_trajectory);
CREATE INDEX IF NOT EXISTS idx_stakeholder_influence_hmac ON stakeholder_influence_map(stakeholder_name_hmac);

-- Private Career Strategy & $1.2M Compensation Positioning Enclave (Rule C-19 Protected)
CREATE TABLE IF NOT EXISTS career_leverage_enclave (
    milestone_id TEXT PRIMARY KEY,
    target_outcome TEXT NOT NULL,
    executive_narrative TEXT NOT NULL,
    demonstrated_multi_million_impact REAL NOT NULL DEFAULT 0.0,
    thought_leadership_citations TEXT,
    strategic_leverage_levers TEXT,
    stealth_score REAL NOT NULL DEFAULT 1.0,
    namespace TEXT NOT NULL DEFAULT 'INTERNAL_ADVISORY',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_career_leverage_target ON career_leverage_enclave(target_outcome, stealth_score);

