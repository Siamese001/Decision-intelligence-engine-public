# Repository Strategic Knowledge Graph Review Prompt (v1.0.0)
**Document ID:** `PROMPT-GOV-STRAT-GRAPH-REVIEW-20260912-V1`  
**Classification:** Internal Strategic Engineering & Governance Contract  
**Target Subsystem:** Decision Intelligence Engine (`storage/`, `dual_pipeline/`, `graph/`, `contracts/`, `cards/`)  
**Objective:** Audit, evaluate, and structurally upgrade the Knowledge Graph Database (graphDB) architecture, ontology, and inference pipelines to ensure strategic insights, multi-hop connection-making, and commercial leverage that rival the world's elite IT strategists, CTOs, and management consulting leaders across internal PartnerAdvisory and external enterprise clients (Apex Global / Apex Utility today, extensible to multi-client federations).

---

## EXECUTION INSTRUCTIONS FOR THE EVALUATING AGENT / LLM

> **Role & Persona**: You are an elite Principal Enterprise Strategist, Chief Technology Officer, and Knowledge Graph Systems Architect with over 25 years of experience advising Fortune 50 C-suites, directing high-stakes digital transformations at premier management consultancies (McKinsey, BCG, PartnerAdvisory Strategy), and designing hyperscale semantic knowledge networks.
>
> **Task**: Perform a rigorous, forensic, end-to-end audit of this repository (`decision-intelligence-engine`) to assess whether its GraphDB schema, ontology, semantic spine, data extraction pipelines, hybrid retrieval, and strategic reasoning engines possess the structural capability to deliver insights, connections, and strategic foresight on par with the world's greatest IT leaders.
>
> **Standard of Judgment**: Do not give polite or generic praise. Apply uncompromising scrutiny. Grade the codebase against the highest standards of enterprise architecture (TOGAF / BIZBOK), strategic portfolio management, multi-agent systems governance, commercial consulting economics, and multi-tenant enterprise data boundaries.

---

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                MASTER AUDIT & EVALUATION PROMPT DIRECTIVE                                   ║
║                                                                                                             ║
║  "Evaluate this repository to guarantee that the knowledge graph does not merely record meeting minutes    ║
║   and surface-level entity catalogs, but constructs an active strategic intelligence substrate capable of  ║
║   discovering latent connections, systemic risks, architectural contradictions, and multi-million-dollar   ║
║   commercial leverage across internal PartnerAdvisory consulting and external client ecosystems."                   ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## PART 1: REPOSITORY ASSETS & EVIDENCE SCOPE TO INSPECT

Before forming conclusions or issuing scores, you MUST inspect the following primary source files across the codebase:

1. **Storage & Graph Schemas**:
   - `storage/database.py`: Core SQLite relational schemas, `canonical_graph_nodes`, `graph_edges`, `shadow_graph_nodes`, `shadow_graph_edges`, and immutable WAL logs.
   - `graph/migrations/`: All historical SQL migrations (specifically `v23`, `v26`, `v36`, `v44`, `v54`, `v55`) defining active tables, registries (`node_type_registry`, `edge_type_registry`, `assertion_predicate_registry`), and views (`v_stakeholder_map`, `v_workstream_index`, `v_assertion_evidence_chain`).
   - `.local/dual_pipeline/active_pair.json` & active SQLite databases: Inspection of actual deployed generational state.

2. **Strategic Reasoning & Decision Intelligence Engines**:
   - `dual_pipeline/strategic_reasoning.py`: Knowledge roles (`KNOWLEDGE_ROLES`), modalities, authority scopes, applicability scopes, and strategic channels.
   - `dual_pipeline/strategic_waves.py`: W1–W7 progression from semantic foundation to existing-graph migration.
   - `dual_pipeline/decision_engine.py`: Decision frames, criteria extraction, option evaluation, retrieval rank decoupling.
   - `dual_pipeline/decision_service.py` & `dual_pipeline/decision_context.py`: Provider-free context projection, W14 contracts, W3 temporal observation integration.

3. **Extraction & Ingestion Pipelines**:
   - `dual_pipeline/transcript_stages.py` & `dual_pipeline/transcript_pipeline.py`: Decomposed 3-stage synthesis pipeline (Forensic Normalization → Executive Brief → Canonical Signal Projection).
   - `governance/decision_intelligence/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md`: Master extraction prompt governing C-01 through C-18 constraints.
   - `dual_pipeline/identity.py` & `dual_pipeline/identity_resolver.py`: Deterministic entity resolution and alias reconciliation.

4. **Multi-Tenant, Governance & Card Systems**:
   - `cards/`: Strategic synthesis cards (`NEE_01_Executive_Account_Thesis.md`, `NEE_02`, `NEE_03_SOW_Commercial`, `NEE_04_Capacity`, `NEE_06_CoDelivery`, `NEE_07_AI_SDLC`, `NEE_10_Stakeholders`, `NEE_11_Cross_Workstream_Reuse_Map`).
   - `dual_pipeline/card_views.py`: Materialized card views and entity projections.
   - `governance/decision_intelligence/`: Semantic spine contracts (`W1`, `W2`, `W3`, `W6`, `W13`, `W14`, `W15`, and `W0_GOLDEN_QUESTION_BASELINE.md`).

---

## PART 2: THE 8 STRATEGIC AUDIT DIMENSIONS (THE WORLD-CLASS CTO SCORECARD)

Evaluate the repository across eight rigorous architectural dimensions. For each dimension, assign a **Maturity Score (0% – 100%)**, identify **Concrete Strengths**, expose **Critical Deficiencies**, and specify **Prescriptive Architectural Upgrades**.

---

### DIMENSION 1: STRATEGIC ENTERPRISE ARCHITECTURE & ONTOLOGY DEPTH
*Does the ontology reflect high-level enterprise architecture (TOGAF / BIZBOK / ArchiMate) or merely tactical project tracking?*

- **Audit Questions**:
  1. **Business Drivers & Outcomes**: Does the graph schema possess first-class node types and predicates for Business Drivers (e.g., Margin Expansion, Decarbonization, Regulatory Compliance, Outage Duration), OKRs, and Value Streams, or are these flattened into generic `strategy` or `initiative` nodes?
  2. **Business Capability Mapping**: Can the graph model hierarchical Business Capabilities (Level 1–3) independently of the ephemeral software applications that implement them?
  3. **Architectural Decision Records (ADRs)**: Does the graph capture formal architectural decisions with context, evaluated options, explicit rationale, rejected alternatives, trade-offs, and downstream consequences?
  4. **Technical Debt & Fragility Topography**: Can the graph model technical debt items, architectural anti-patterns, protocol obsolescence, single-points-of-failure (SPOFs), and blast-radius vectors?
  5. **Vendor & Commercial Dependencies**: Does the schema link internal systems to third-party software vendors, contract terms, license models, and vendor lock-in risks?

---

### DIMENSION 2: MULTI-HOP CONNECTION-MAKING & CROSS-SILO SYNTHESIS
*Can the GraphDB uncover second- and third-order emergent insights that human leaders miss due to organizational silos?*

- **Audit Questions**:
  1. **Cross-Silo Contradiction Detection**: If Team A (e.g., Apex Global Analytics) commits to a decentralized event-driven architecture using Kafka/GCP, while Team B (e.g., EDO) standardizes on a centralized Databricks Medallion lakehouse without real-time streaming, can graph queries automatically surface this architectural conflict before integration testing?
  2. **Duplicative Investment & Reuse Identification**: If three disparate business units (e.g., Apex Utility Power Delivery, PGD Fleet Ops, and Customer Care) are independently contracting or building computer vision / GenAI document extraction pipelines, does the graph detect this pattern and alert leadership to a shared platform opportunity?
  3. **Blast Radius & Dependency Cascades**: When a core platform component or API (e.g., Maximo Work Management, Catalyst Platform Substrate) changes its authentication scheme or deprecates an endpoint, can the graph traverse $N$-hop dependency paths to identify every impacted application, business process, and client stakeholder?
  4. **Hidden Blocker Identification**: Can the graph trace why a high-priority executive initiative is stalled by connecting it across: Initiative $\to$ Pod $\to$ Unresolved Dependency $\to$ Blocked Architectural Decision $\to$ Disputed Decision Right between two specific leaders?

---

### DIMENSION 3: DUAL-AUDIENCE FEDERATION & MULTI-TENANT BOUNDARIES
*How elegantly does the architecture bridge Internal PartnerAdvisory Consulting Intelligence with External Client Ecosystems (NEE today, Client X tomorrow)?*

- **Audit Questions**:
  1. **Consulting Commercial & Delivery Mechanics**:
     - Does the graph model PartnerAdvisory's commercial posture: SOW boundaries, billing/staffing models (e.g., the 106-SWE capacity allocation vs. fixed-fee deliverable pods), burn-rates, out-of-scope delivery creep, and contract renewal triggers?
     - Can the graph map account health, sponsor relationship temperature, and early warning signals of client executive churn?
  2. **Proactive Advisory & Opportunity Pipeline**:
     - Can the graph connect observed client technical pain points (e.g., high LLM inference costs, lack of agent evaluation frameworks) to PartnerAdvisory's specialized capability centers (e.g., AI/ML Center of Excellence, Cloud Architecture Practice) to generate proactive, pre-RFP client proposals?
  3. **Multi-Tenant Partitioning & Zero-Leakage Architecture**:
     - Are entity IDs, namespaces, and graph tables cleanly decoupled so that `client_id` (e.g., `client:nee`, `client:utility_b`, `client:health_c`) is a strict partitioning key?
     - Is there an ironclad guarantee that private internal PartnerAdvisory advisory data (`INTERNAL_ADVISORY`, `career_dynamics`, `account_strategy`) can NEVER be queried, retrieved, or leaked into a client-facing deliverable or cloud prompt?
  4. **Cross-Client Pattern Abstraction**:
     - Can the engine abstract a client-specific technical solution (e.g., "NEE Substation Onboarding via Maximo & Databricks") into an anonymized, generalized Industry Blueprint ("Utility Asset Digital Thread Accelerator") for reuse across other clients without violating NDA or confidentiality agreements?

---

### DIMENSION 4: ORGANIZATIONAL TOPOLOGY, CONWAY'S LAW & POLITICAL INFLUENCE
*Does the graph understand the human, organizational, and political dynamics that govern technology success or failure?*

- **Audit Questions**:
  1. **Formal Org Chart vs. Informal Power Topology**:
     - Does the graph distinguish between nominal authority (e.g., VP of Architecture) and informal technical veto power (e.g., Principal Architect or Lead Data Engineer whose informal nod is required for adoption)?
  2. **Conway's Law Alignment**:
     - Can the graph evaluate whether team organizational boundaries (e.g., siloed pods vs. cross-functional platform teams) match the desired target software architecture?
  3. **Stakeholder Sentiment, Stance & Friction**:
     - Does the schema track individual stakeholder stances (Champion, Skeptic, Detractor, Passive Approver, Gatekeeper) toward specific initiatives, technologies (e.g., Gemini vs. GPT-4o, AWS vs. GCP), or delivery models?
  4. **Key-Person Risk & Succession Vulnerability**:
     - Can the graph identify single-person dependencies where critical domain knowledge or system access is held by a single contractor or employee with zero redundancy?

---

### DIMENSION 5: INGESTION FIDELITY & STRATEGIC SIGNAL EXTRACTION
*Do the extraction pipelines capture profound executive reality or merely transcribe superficial conversation?*

- **Audit Questions**:
  1. **Signal vs. Noise Filtering**:
     - In `RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md` and `dual_pipeline/transcript_stages.py`, does the extraction isolate high-leverage strategic commitments, architectural trade-offs, and unstated risks, or does it drown in administrative meeting trivia?
  2. **Extraction of Implicit Trade-offs & Rejected Options**:
     - When meeting participants debate two architectural approaches and choose one, does the extraction capture the rejected alternative and the underlying rationale, or only the chosen action item?
  3. **Unspoken Organizational Friction Capture**:
     - Does the extraction detect subtle expressions of resistance, skepticism, resource contention, and political hesitation, routing them safely to the `INTERNAL_ADVISORY` shadow graph?
  4. **Strict Epistemic Typing**:
     - Are extracted statements rigorously partitioned into the closed epistemic categories: `FACT`, `PROPOSAL`, `ASSUMPTION`, `DECISION`, `RISK`, `OPEN_QUESTION`, `COMMITMENT`?

---

### DIMENSION 6: EPISTEMIC RIGOR, TEMPORAL COHERENCE & GROUNDED TRUST
*Is the graph protected against hallucinations, stale context, and false consensus?*

- **Audit Questions**:
  1. **W3 Temporal Validity (Valid-Time vs. Transaction-Time / Bi-Temporal)**:
     - Does every assertion distinguish between when an event occurred in the physical world (`observed_at`), when it is expected to expire (`stale_after`), and when the record was written to the database (`transaction_time`)?
     - Does the graph prevent stale 6-month-old architectural assumptions from contaminating modern decision queries?
  2. **Counter-Evidence & Dialectical Tension**:
     - Can the graph represent conflicting assertions (Assertion A: "Databricks is approved for PII" vs. Assertion B: "CISO blocked PII on Databricks") without silently overwriting or crashing? Does it preserve dialectical tension until explicitly resolved?
  3. **The Provider-Free Write Boundary**:
     - Is the commit path to `CANONICAL_GRAPH.db` 100% deterministic and provider-free? Can a hallucinating LLM ever directly mutate the graph without passing deterministic ontology, schema, and gate checks?
  4. **Forensic Traceability**:
     - Can every single assertion, edge, and card statement be traced back through an unbroken foreign-key chain to exact audio milliseconds or primary source document locators?

---

### DIMENSION 7: DECISION INTELLIGENCE & DIALECTICAL REASONING (LIFECYCLE 2)
*Does the query layer provide strategic decision guidance or simply return vector search snippets?*

- **Audit Questions**:
  1. **Retrieval Rank vs. Strategic Option Decoupling**:
     - Does the decision engine enforce the fundamental law: "Retrieval score never becomes an option score"?
  2. **Multi-Criteria Trade-Off Evaluation**:
     - Does `dual_pipeline/decision_engine.py` evaluate architectural options against multi-dimensional criteria (Strategic Alignment, Total Cost of Ownership [TCO], Time-to-Value, Organizational Friction, Security/Compliance Risk, Architectural Elegance)?
  3. **Dialectical Synthesis**:
     - Does the reasoning engine pit opposing perspectives against one another (Thesis $\to$ Antithesis $\to$ Synthesis) rather than generating sycophantic, one-sided recommendations?
  4. **Strict Decision Terminal States**:
     - Does every query terminate in a governed state (`DECISION_PRODUCED`, `CLARIFICATION_REQUIRED`, `NO_DECISION`, `ABSTENTION`), refusing to manufacture false certainty when evidence is incomplete?

---

### DIMENSION 8: EXECUTIVE SYNTHESIS, COCKPIT & ACTION GENERATION
*Are the outputs directly consumable by Fortune 50 CEOs, CTOs, and Managing Directors?*

- **Audit Questions**:
  1. **Next Best Action (NBA) Generation**:
     - Does the graphDB continuously generate prioritized, actionable next steps for account leaders, enterprise architects, and engineering managers?
  2. **C-Suite Briefing Quality**:
     - Are generated briefs (`02_executive_recap.md` and Card syntheses) crisp, financialized, risk-calibrated, and email-ready, answering: "What is happening? Why does it matter economically/operationally? What decision must leadership make now? What happens if we delay?"
  3. **Visual & Strategic Frameworks**:
     - Can graph insights be projected into executive frameworks: Wardley Maps, Gartner Magic Quadrants, Risk/Complexity Heatmaps, and 3-Year Strategic Roadmaps?

---

## PART 3: AUDIT OUTPUT FORMAT & REQUIRED DELIVERABLES

When executing this audit, structure your findings into the following four mandatory sections:

### 1. THE EXECUTIVE MATURITY SCORECARD
Provide an executive markdown summary table rating all 8 dimensions:

| # | Dimension | Current Maturity (Level 1–5) | Numerical Score (0–100%) | Verdict | Primary Gap |
|---|---|---|---|---|---|
| 1 | Strategic EA & Ontology Depth | ... | ... | PASS / WARN / FAIL | ... |
| 2 | Multi-Hop Connection-Making | ... | ... | ... | ... |
| 3 | Dual-Audience & Multi-Tenant Federation | ... | ... | ... | ... |
| 4 | Organizational & Political Topology | ... | ... | ... | ... |
| 5 | Ingestion & Signal Extraction Fidelity | ... | ... | ... | ... |
| 6 | Epistemic Rigor & Temporal Coherence | ... | ... | ... | ... |
| 7 | Decision Intelligence & Dialectics | ... | ... | ... | ... |
| 8 | Executive Synthesis & Actionability | ... | ... | ... | ... |
| **TOTAL** | **OVERALL STRATEGIC GRAPH READINESS** | **...** | **...** | **...** | **...** |

*Maturity Level Legend*:
- **Level 1 (Tactical)**: Surface-level task/meeting transcription; zero architectural causality.
- **Level 2 (Relational)**: Basic entity linking (who attended what meeting, basic workstream tags).
- **Level 3 (Contextual)**: Clear separation of client-safe vs. internal advisory; basic temporal observation.
- **Level 4 (Strategic)**: Multi-hop dependency traversal, Conway's law modeling, ADR tracking, multi-criteria decision options.
- **Level 5 (World-Class)**: Proactive emergent insight generation, cross-tenant pattern abstraction, automated architectural conflict detection, C-suite foresight rivaling top global strategists.

---

### 2. FORENSIC GAP ANALYSIS (P0 / P1 / P2 DEFICIENCIES)
Detail the concrete gaps where the current codebase falls short of World-Class IT Leadership capabilities. Categorize by:
- **P0 (Critical Flaws / Blindspots)**: Gaps that prevent the graph from seeing high-impact strategic risks, causes data leakage, or restricts it to mere meeting logging.
- **P1 (Architectural Limitations)**: Gaps that limit multi-hop reasoning, cross-account federation, or commercial insight generation.
- **P2 (Enhancement Opportunities)**: Advanced modeling refinements (e.g., Wardley mapping coordinates, automated FinOps graph queries).

---

### 3. CONCRETE ARCHITECTURAL UPGRADE BLUEPRINT
Provide exact, copy-paste ready code, schema, and query specifications to elevate the repository to Level 5:

1. **Target Ontological Schema Migration (`SQL` / Pydantic)**:
   - Provide the exact SQL table definitions and registry inserts (`node_type_registry`, `edge_type_registry`, `assertion_predicate_registry`) for missing strategic primitives:
     * `business_capability`
     * `architectural_decision` (ADR)
     * `technical_debt`
     * `vendor_commercial_dependency`
     * `political_influence_vector`
     * `cross_client_reusable_pattern`
   - Provide the exact semantic predicates (e.g., `CONFLICTS_WITH_PRINCIPLE`, `CARRIES_KEY_PERSON_RISK`, `CREATES_SINGLE_POINT_OF_FAILURE`, `EXPOSES_VENDOR_LOCKIN`, `REPRESENTS_REUSE_OPPORTUNITY`).

2. **The "World-Class CTO" Golden Query Catalog (`SQL` & Graph Traversals)**:
   - Provide 5–10 production-ready recursive SQL queries or graph traversal algorithms that demonstrate world-class insight generation:
     * Query 1: *Cross-silo architectural contradiction detector*.
     * Query 2: *Key-person and vendor dependency risk heatmap*.
     * Query 3: *Unstated political veto vs. formal approval discrepancy finder*.
     * Query 4: *Cross-workstream reusable capability harvester (PartnerAdvisory IP opportunity)*.
     * Query 5: *Strategic debt blast-radius projection*.

3. **Multi-Tenant / Multi-Client Decoupling Architecture**:
   - Specify the exact refactoring plan to transition from single-client hardcoded conventions (`NEE_*`) to a multi-client federated model (`client:nee`, `client:fpl`, `client:acme_health`, `PartnerAdvisory:internal`) with zero cross-tenant leakage and strict data sovereignty.

4. **Extraction Pipeline & Prompt Enhancements**:
   - Specific delta additions to `RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md` and `dual_pipeline/transcript_stages.py` to instruct LLM extractors to actively listen for implicit trade-offs, organizational friction, political body language, and architectural debt during meeting ingestion.

---

### 4. 10 TEST CASES: THE "WORLD-CLASS IT STRATEGIST" BENCHMARK
Define 10 complex, real-world scenario tests that the upgraded Knowledge Graph must be able to answer conclusively. Include:
- Question & Scenario Context.
- Expected Graph Traversal Path ($N$-hops).
- Required Strategic Recommendation & Risk Assessment.
- Why an ordinary IT analyst or basic vector RAG would fail this test.

---

```
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
END OF AUDIT PROMPT — COMMENCE FORENSIC CODEBASE EVALUATION ACCORDING TO THESE SPECIFICATIONS
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
```
