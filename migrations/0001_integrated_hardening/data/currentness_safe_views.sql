-- Currentness-safe compatibility views.
DROP VIEW IF EXISTS v_stakeholder_map;
CREATE VIEW v_stakeholder_map AS
SELECT p.node_id AS stakeholder_id,p.canonical_name AS stakeholder,p.description,
       e.edge_type,t.canonical_name AS related_object,e.confidence,e.materiality,
       e.validation_priority,e.validation_owner,e.hitl_required,e.source_id,
       s.source_posture,e.description AS relationship_note
FROM node p
LEFT JOIN edge e ON e.from_node_id=p.node_id AND e.superseded_by IS NULL
LEFT JOIN node t ON t.node_id=e.to_node_id
LEFT JOIN source s ON s.source_id=e.source_id
WHERE p.node_type='stakeholder' AND p.active=1;

DROP VIEW IF EXISTS v_hitl_items;
CREATE VIEW v_hitl_items AS
SELECT claim_id AS item_id,'claim' AS item_type,claim_text AS description,evidence_posture,
       confidence,materiality,validation_priority,validation_owner,source_id,card_id
FROM claim WHERE hitl_required=1 AND superseded_by IS NULL
UNION ALL
SELECT edge_id AS item_id,'edge' AS item_type,description,evidence_posture,
       confidence,materiality,validation_priority,validation_owner,source_id,NULL AS card_id
FROM edge WHERE hitl_required=1 AND superseded_by IS NULL;

DROP VIEW IF EXISTS v_sow_status_index;
CREATE VIEW v_sow_status_index AS
SELECT w.canonical_name AS workstream,e.edge_type,sn.canonical_name AS authority_object,
       e.evidence_posture,e.confidence,e.materiality,e.description,e.source_id
FROM node w
JOIN edge e ON e.from_node_id=w.node_id AND e.superseded_by IS NULL
JOIN node sn ON sn.node_id=e.to_node_id
WHERE e.edge_type IN ('SOW_BACKED_BY','Q3_CAPACITY_BACKED_BY','PROPOSAL_SCOPE_FROM','RFP_PROCESS_FROM','PRICING_MODEL_FROM');
