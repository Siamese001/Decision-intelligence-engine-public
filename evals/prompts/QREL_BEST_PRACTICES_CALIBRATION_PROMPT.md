# Canonical Prompt: BGE-M3 QRELS Calibration & Grounded Relevance Assessment

> **Contract / Protocol Specification**
> **Target Corpus:** NEE Decision Intelligence Corpus (Project Atlas / Nexus transcripts, SOWs, Architecture blueprints, 19 Topic Cards, 693 Graph Nodes, 509 Claims, 1,667 Evidence Items)
> **Model Under Test:** BAAI/bge-m3 (Dense 1024-dim, Learned Sparse Lexical, ColBERT Late Interaction MaxSim) fused with Local Graph BM25
> **Baseline Weights:** Dense = 0.34, Sparse = 0.17, BM25 = 0.15, ColBERT = 0.34

---

## 1. Objective & Operational Mandate

You are the **Retrieval & Relevance Calibration Specialist** for the Decision Intelligence Engine. Your mandate is to calibrate and validate the multi-vector fusion weights of the BGE-M3 retrieval pipeline against the actual decisions, contracts, risks, and transcript evidence in the corpus.

You must reject synthetic edge-routing probes (e.g., grading whether an entity points to a card identifier). Instead, you must construct a contract-grade, TREC/BEIR-aligned Query Relevance (QRELS) benchmark grounded in genuine decision needs extracted from:
1. **Raw Meeting Transcripts** (e.g., APEX Development / Nexus discovery, rate case fallout, land management paper workflows, transmission 2034 constraints).
2. **Commercial & SOW Governance** (Q3 fixed-capacity bounds, proposal rate restrictions, staffing models).
3. **Operational Risks & Architecture** (Project Beacon runtime topology, Gemini authentication blockers, GitLab P0 triage).
4. **Canonical Topic Cards & Claims** (Cards NEE_00 through NEE_22).

---

## 2. Methodology & Information Retrieval Best Practices

### A. Graded Relevance Scale (TREC / BEIR Standards)
Every candidate passage retrieved for a test query must be evaluated on an atomic 3-point scale:

- **Grade 2 (Highly Relevant / Direct Decision Grounding):**
  - The passage directly and decisively answers the specific decision question.
  - Contains concrete entities, dates, dollar figures, constraints, architectural components, or explicit attributions (e.g., *"Sharon Diaram noted 200,000 acres are managed on paper; APEX halted land acquisitions post-rate-case"*).
  - An executive or technical lead could make an informed decision based solely or primarily on this passage.

- **Grade 1 (Partially Relevant / Contextual Background):**
  - The passage provides topical context, related domain background, or adjacent workstream details (e.g., mentions APEX solar development or FORGE tools generally, but lacks the specific land management constraint).
  - Helpful as supplementary context, but does not directly answer the specific inquiry.

- **Grade 0 (Non-Relevant / Confounder):**
  - The passage does not answer the question.
  - Includes lexical confounders: records that contain query keywords (e.g., *"capacity"*, *"pod"*, *"Gemini"*, *"rate"*) in an unrelated context (e.g., AWS database capacity instead of SOW staffing capacity).

---

### B. 3-Tier Cosine-Similarity Stratification

All benchmark queries must be mathematically stratified using BGE-M3 1024-dimensional dense embeddings to stress-test distinct failure modes:

```
+-----------------------------------------------------------------------------------+
| Tier 1: Paraphrase Clusters (Pairwise Cosine >= 0.85)                             |
| Goal: Test embedding invariance & fusion stability across stylistic phrasing      |
| Structure: 10 Core Decision Topics x 3 Variants (Executive, Engineering, Shorthand)|
+-----------------------------------------------------------------------------------+
| Tier 2: Cross-Domain Orthogonal Probes (Pairwise Cosine <= 0.45)                  |
| Goal: Ensure corpus topology coverage without domain bias                         |
| Structure: 15-20 Queries across Governance, Commercial, Risk, Architecture, etc.  |
+-----------------------------------------------------------------------------------+
| Tier 3: Hard-Negative Confounder Traps (High BM25, Low Dense Cosine < 0.55)      |
| Goal: Verify that Dense + ColBERT suppress superficial lexical overlap traps       |
| Structure: 10 Adversarial queries where BM25 retrieves misleading high-overlap    |
|            passages that human ground-truth marks as Grade 0                      |
+-----------------------------------------------------------------------------------+
```

---

### C. 4-Engine Candidate Pooling Protocol (TREC Depth K=10)

To eliminate unjudged document bias:
1. For each query $q$, execute four independent, unweighted retrieval passes:
   - **Dense Lane:** Cosine similarity over BGE-M3 1024-dim dense vectors.
   - **Learned Sparse Lane:** Token-level lexical weight dot-product from BGE-M3 sparse head.
   - **ColBERT Lane:** Late interaction multi-vector MaxSim ($\sum_{i} \max_{j} E_{q,i}^\top E_{d,j}$).
   - **BM25 Lane:** Corpus-local BM25 ranking across structured assertion and source chunk texts.
2. Pool the Top-10 candidates from each component:
   $$\mathcal{P}(q) = \text{Top10}_{\text{Dense}}(q) \cup \text{Top10}_{\text{Sparse}}(q) \cup \text{Top10}_{\text{ColBERT}}(q) \cup \text{Top10}_{\text{BM25}}(q)$$
3. Deduplicate across candidate IDs.
4. Present candidate passages to the human evaluator in **randomized order** with component provenance stripped, displaying the actual full passage text, source locator, and timestamp.

---

## 3. Decision Topics Grounded in Real Corpus Artifacts

The query set must be constructed directly from the following verified corpus topics:

### Topic 1: APEX Development / Nexus Land Management Constraints
- **Source Artifact:** `Project_Atlas_Transcript_Attributed_08.28.2026.md` & `02_executive_recap.md`
- **Ground Truth Facts:** APEX manages 200,000+ acres on physical paper (lease agreements, cattle farming invoices); rate case advisory halted land prospecting (healthy inventory of 264,000+ acres / ~3.5 GW through 2030); LCDA is the custom system of record post-2021 rate case, not Primavera P6.
- **Sample Query Triplet (Tier 1):**
  - *Executive:* "What are the primary operational bottlenecks in APEX's land management workflow following the recent rate case?"
  - *Engineering:* "Why is land prospecting excluded from the Q4 Nexus scope and what system holds the actual land records?"
  - *Shorthand:* "APEX land management paper disaster LCDA rate case acreage"

### Topic 2: Nexus Data Center Siting & Transmission Line Blockers
- **Source Artifact:** `02_executive_recap.md` (Executive direction "fifth floor")
- **Ground Truth Facts:** Demand tripling for data center sites; candidate sites face transmission interconnection delays with lines not scheduled until 2034; urgently drives demand for AI transmission capacity siting tool.
- **Sample Query Triplet (Tier 1):**
  - *Executive:* "What executive directive is driving urgent AI siting analysis for data center interconnection?"
  - *Engineering:* "How does the 2034 transmission line timeline constrain APEX data center site selection?"
  - *Shorthand:* "APEX data center 3x transmission lines 2034 siting constraint"

### Topic 3: Commercial Guardrails & Q3 SOW Capacity Bounds
- **Source Artifact:** Card `NEE_03`, `NEE_04`, and SOW assertions
- **Ground Truth Facts:** Q3 NEA SOW is fixed-capacity resourcing, not a fixed-output or deliverable guarantee; PartnerAdvisory proposal rates, team size, and timelines cannot be committed without signed change orders; risk of being treated as generic staff aug.
- **Sample Query Triplet (Tier 1):**
  - *Executive:* "What commercial boundaries govern the Q3 NEA engagement regarding deliverable guarantees vs capacity?"
  - *Engineering:* "Under what contractual terms are PartnerAdvisory engineering pods deployed for NEA Q3 work?"
  - *Shorthand:* "NEA Q3 SOW fixed capacity deliverable guarantee commercial risk"

### Topic 4: Project Beacon Architecture & Runtime Topology
- **Source Artifact:** Card `NEE_22`, Node `risk:Sherpa runtime topology ambiguity`
- **Ground Truth Facts:** Ambiguity across AgentCore, ECS container tasks, LangGraph orchestration, and AWS Bedrock foundation models; Clay Palumbo tech leadership transition risk.
- **Sample Query Triplet (Tier 1):**
  - *Executive:* "What architectural ambiguities exist regarding the Project Beacon deployment topology on AWS?"
  - *Engineering:* "Which runtime components—AgentCore, ECS, LangGraph, or Bedrock—define the Project Beacon execution boundary?"
  - *Shorthand:* "Project Beacon architecture runtime topology AgentCore ECS Bedrock"

### Topic 5: Gemini Enterprise Authentication & Agent Sharing Blockers
- **Source Artifact:** Node `risk:Gemini auth and agent sharing blockers`
- **Ground Truth Facts:** Active technical blockers with Google around authentication, enterprise identity propagation, and cross-user agent sharing in production pods.
- **Sample Query Triplet (Tier 1):**
  - *Executive:* "What technical blockers are currently delaying enterprise agent sharing for Gemini?"
  - *Engineering:* "What are the specific auth and IAM constraints preventing Gemini agents from being shared across pods?"
  - *Shorthand:* "Gemini auth agent sharing blockers Google IAM pod"

---

## 4. Evaluation & Optimization Mathematical Protocol

### A. Graded Ranking Metric: NDCG@10
For each query $q$ with graded judgments $rel_i \in \{0, 1, 2\}$:

$$\text{DCG@}10 = \sum_{i=1}^{10} \frac{2^{rel_i} - 1}{\log_2(i + 1)}$$

$$\text{NDCG@}10 = \frac{\text{DCG@}10}{\text{IDCG@}10}$$

Where $\text{IDCG@}10$ is the ideal DCG obtained by sorting all judged candidates by decreasing relevance grade.

### B. Reciprocal Rank: MRR@10
$$\text{RR@}10 = \frac{1}{\min \{i \le 10 \mid rel_i = 2\}} \quad (\text{or } 0 \text{ if no Grade 2 in top 10})$$

$$\text{MRR@}10 = \frac{1}{|Q|} \sum_{q \in Q} \text{RR@}10(q)$$

### C. Simplex Weight Optimization Sweep
Evaluate all weight vectors $\mathbf{w} = (w_d, w_s, w_b, w_c)$ on the 4-simplex:
$$\Delta^3 = \left\{ (w_d, w_s, w_b, w_c) \in \mathbb{R}^4 \;\middle|\; \sum w_i = 1.0, \; w_i \ge 0.05, \; w_i = k \cdot 0.05 \right\}$$
Total configurations: $N = 1,771$.

For each configuration $\mathbf{w}$, recompute reciprocal rank fusion:
$$\text{RRF}(d; \mathbf{w}) = \sum_{m \in \{d, s, b, c\}} \frac{w_m}{60 + \text{rank}_m(d)}$$

Find the empirical optimal configuration:
$$\mathbf{w}^* = \arg\max_{\mathbf{w} \in \Delta^3} \frac{1}{|Q|} \sum_{q \in Q} \text{NDCG@}10(q; \mathbf{w})$$

---

## 5. Formal Decision Gate (Pass / Fail Criteria)

Compare current canonical weights $\mathbf{w}_{\text{canonical}} = (0.34, 0.17, 0.15, 0.34)$ against $\mathbf{w}^*$:

| Criterion | Threshold | Action |
|---|---|---|
| **Weight Validation** | $\text{NDCG}(\mathbf{w}^*) - \text{NDCG}(\mathbf{w}_{\text{canonical}}) < 0.02$ | **PASS:** Canonical weights validated. Keep production weights unchanged. |
| **Weight Drift / Re-tune** | $\text{NDCG}(\mathbf{w}^*) - \text{NDCG}(\mathbf{w}_{\text{canonical}}) \ge 0.02$ | **RECALIBRATE:** Update `BM25_RRF_WEIGHTS` in `dual_pipeline/index.py` to $\mathbf{w}^*$ and re-run E3 retrieval release gate. |
| **Ablation Defense** | $\text{NDCG}(\mathbf{w}_{\text{canonical}}) - \text{NDCG}(\mathbf{w}_{w_b=0}) > 0.05$ | **BM25 ESSENTIAL:** Confirms hybrid lexical retrieval is strictly necessary alongside neural. |
| **ColBERT MaxSim Defense**| $\text{NDCG}(\mathbf{w}_{\text{canonical}}) - \text{NDCG}(\mathbf{w}_{w_c=0}) > 0.05$ | **ColBERT ESSENTIAL:** Confirms token-level late interaction prevents dense semantic wash. |

---

## 6. Output Delivery Requirements
When this prompt is executed, the pipeline must emit:
1. `evals/qrels_query_set.json`: The 40+ stratified queries with BGE-M3 cosine distance matrices.
2. `evals/qrels_candidate_pool.json`: The 4-engine pooled candidates with raw passage text and scores.
3. `tools/score_qrels_v2.py`: Interactive atomic HITL console for scoring.
4. `evals/qrels_weight_calibration_results.json`: Simplex sweep results and formal gate determination.
