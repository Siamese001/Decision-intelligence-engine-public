-- Migration v026 / v58: Forward-Looking Executive Graph Schema
-- Policy: Executive Strategic Foresight (>=30% Strategic Floor across PGD, APEX, Core Tech, Products, M&A)

-- 1. Business Unit Strategic Mandates (12-18 Month Forward Outcomes)
CREATE TABLE IF NOT EXISTS bu_strategic_mandate (
    mandate_id TEXT PRIMARY KEY,
    bu_name TEXT NOT NULL,
    owning_executive_id TEXT NOT NULL,
    owning_executive_name TEXT NOT NULL,
    title TEXT NOT NULL,
    target_horizon TEXT NOT NULL CHECK (target_horizon IN ('H1_90_DAY', 'H2_18_MONTH', 'H3_MULTI_YEAR')),
    strategic_objective TEXT NOT NULL,
    kpi_metrics_json TEXT NOT NULL DEFAULT '[]',
    estimated_capital_budget_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_bu_mandate_exec ON bu_strategic_mandate(owning_executive_id, bu_name);
CREATE INDEX IF NOT EXISTS idx_bu_mandate_horizon ON bu_strategic_mandate(target_horizon, status);

-- 2. Proactive White-Space Theses (Unsolicited Expansion & High-Conviction Plays)
CREATE TABLE IF NOT EXISTS whitespace_thesis (
    thesis_id TEXT PRIMARY KEY,
    bu_name TEXT NOT NULL,
    target_executive_id TEXT NOT NULL,
    target_executive_name TEXT NOT NULL,
    thesis_title TEXT NOT NULL,
    opportunity_type TEXT NOT NULL CHECK (opportunity_type IN ('UNSOLICITED_PROPOSAL', 'CROSS_BU_EXPANSION', 'OPERATING_MODEL_TRANSFORMATION', 'AI_CAPABILITY_LEAP', 'VENDOR_CONSOLIDATION')),
    problem_statement TEXT NOT NULL,
    proposed_play TEXT NOT NULL,
    strategic_horizon TEXT NOT NULL CHECK (strategic_horizon IN ('H1_90_DAY', 'H2_18_MONTH')),
    partner_advisory_role TEXT NOT NULL DEFAULT 'STRATEGIC_ADVISORY_PARTNER',
    estimated_sow_value_usd REAL NOT NULL DEFAULT 0.0,
    competitive_threat TEXT NOT NULL DEFAULT 'NONE',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_whitespace_exec ON whitespace_thesis(target_executive_id, bu_name);
CREATE INDEX IF NOT EXISTS idx_whitespace_horizon ON whitespace_thesis(strategic_horizon);

-- 3. Cross-BU Tension Points (November One Tech Transition Friction)
CREATE TABLE IF NOT EXISTS cross_bu_tension (
    tension_id TEXT PRIMARY KEY,
    source_bu TEXT NOT NULL,
    target_bu TEXT NOT NULL,
    source_executive_id TEXT NOT NULL,
    source_executive_name TEXT NOT NULL DEFAULT '',
    target_executive_id TEXT NOT NULL,
    target_executive_name TEXT NOT NULL DEFAULT '',
    tension_category TEXT NOT NULL CHECK (tension_category IN ('BUDGET_ALLOCATION', 'PLATFORM_OWNERSHIP', 'OPERATING_MODEL', 'HEADCOUNT_CONTROL', 'VENDOR_DUPLICATION')),
    description TEXT NOT NULL,
    friction_points_json TEXT NOT NULL DEFAULT '[]',
    november_reorg_impact TEXT NOT NULL,
    mitigation_strategy TEXT NOT NULL,
    severity_score INTEGER NOT NULL CHECK (severity_score BETWEEN 1 AND 5),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cross_bu_tension ON cross_bu_tension(source_bu, target_bu);

-- 4. Decision Horizon Ledger (Audits Strategic vs Past-Fact Weighting)
CREATE TABLE IF NOT EXISTS decision_horizon_ledger (
    entry_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    time_horizon TEXT NOT NULL CHECK (time_horizon IN ('H0_PAST_FACT', 'H1_90_DAY_DECISION', 'H2_18_MONTH_THESIS')),
    strategic_weight REAL NOT NULL CHECK (strategic_weight >= 0.0 AND strategic_weight <= 1.0),
    evidence_source TEXT NOT NULL,
    verified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_horizon_ledger_entity ON decision_horizon_ledger(entity_id, time_horizon);
