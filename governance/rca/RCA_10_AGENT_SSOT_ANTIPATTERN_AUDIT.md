# 10-Agent Repo-Wide Forensic Audit: Hardcoded Entities, Metadata Stringification & GraphDB SSOT Bypasses

**Date**: 2026-10-18  
**Branch / Worktree**: `feat/rca-infra-orals-v2-ingest` (`.worktrees/feat_rca_infra_orals_v2_ingest`)  
**Audit Scope**: 10 specialized sub-agents across `dual_pipeline/`, `pipeline_a_intake/`, `pipeline_b_extraction/`, `config/`, `contracts/`, and `storage/` (~350+ files inspected).

---

## Executive Answer: Why Did the Code Search Metadata for `"conduit"` and Rewrite `"Dan Okray"` to `"Dan Okay"`?

The pipeline suffered from **two compounding architectural violations**:

1. **Bespoke Meeting-Overfitted Healers Using Full-Dictionary Stringification (`str(meta)`)**:
   - Instead of resolving entities against `graph/nee_graph.sqlite`, previous remediation waves patched historical ASR defects by adding regex rules directly into Python healer modules (`forensic_healer.py`, `recap_healer.py`, `recap_key_discussion_healer.py`, `healing_mesh.py`, `graph_healer.py`).
   - To decide whether a meeting belonged to `"conduit"`, `"lens"`, or `"jarvis"`, those healers stringified the entire 559 KB runtime metadata dictionary (`str(meta)` / `str(data)`), which includes every spoken dialogue turn (`raw_text`) and every historical graph node (`graph_context_packet`).
   - In the October 2, 2026 Infrastructure Automation meeting, when Daniel Simao said *"the conduit team"* at `00:11:51` (and because `PD Conduit` was in `graph_context_packet`), `"conduit" in str(meta).lower()` evaluated to `True`, firing the Project Conduit regex `re.sub(r"\bOkray\b", "Okay", current)` (`forensic_healer.py:323-332`) and corrupting all 22 turns of canonical GraphDB stakeholder `person_dan_okray` (`Dan Okray`) into `"Dan Okay"`.

2. **Competing Shadow Dictionaries Overriding `graph/nee_graph.sqlite` SSOT**:
   - Even when `entity_resolver_graph.py` loaded person nodes from `graph/nee_graph.sqlite`, `entity_resolver.py` (`Lines 54-67, 339-445`) loaded a 1,540-line static JSON file (`canonical_enterprise_directory.json`, containing 308 hardcoded people including prohibited synthetic personas `Mitchell Simmons` and `Tenika Johnson`) and **overwrote** the GraphDB node's `canonical_name`, `org`, and `role` (`Lines 406-431`).
   - Similarly, `c06_graphdb_role_gate.py` (`Lines 84-94, 240-268`) gave `canonical_enterprise_directory.json` (`0.85` confidence) higher priority than GraphDB (`0.80` confidence) and marked static JSON matches as `"DIRECTORY_VERIFIED"`, bypassing GraphDB verification.

---

## 10-Agent Findings Matrix by Subsystem

### 1. Dangerous `str(meta)` / `str(data)` Stringification Hacks (Agent 1 & Agent 10)
| File | Lines | Anti-Pattern | Impact |
| :--- | :--- | :--- | :--- |
| `dual_pipeline/transcript_qa_core/forensic_healer.py` | `307-336` | `is_lens = any("lens" in str(x).lower() for x in (..., str(meta)))`<br>`is_conduit = any("conduit" in str(x).lower() for x in (..., str(meta)))` | Rewrites `"Okray"` -> `"Okay"`, `"Sierra"` -> `"Siara"`, `"to maximum"` -> `"to Maximo"`, and deletes `[00:18:41] **Amit Ayer:**` whenever `"conduit"` or `"lens"` appears anywhere in `meta`. |
| `dual_pipeline/transcript_qa_core/recap_healer.py` | `354, 870, 887` | `ctx = text.lower() + " " + str(meta).lower()` | Triggers meeting-specific Key Discussion Point rewrites (`sync_mandy_slalom`, `is_conduit_catch_up`, `00000129`) across unrelated meetings. |
| `dual_pipeline/transcript_qa_core/recap_key_discussion_healer.py` | `184` | `ctx += " " + str(meta).lower()` | Injects hardcoded McDonald's / Seagen / Luis Arencibia / Dan Okray KDP text if any token matches `str(meta)`. |
| `dual_pipeline/transcript_qa_core/healing_mesh.py` | `243, 252, 259` | `is_jarvis_charter(metadata, str(data))`, `is_engineering_jarvis(metadata, str(data))`, `is_partner_alliances(metadata, str(data))` | Stringifies the entire Output 03 graph payload to trigger meeting-specific graph overwrites. |
| `dual_pipeline/transcript_qa_core/recap_grounding_healer.py` | `442` | `nl in str(metadata).lower()` | Treats any substring match anywhere in serialized metadata as grounded speaker evidence. |

### 2. Whole-Module Meeting-Overfitted Healers in `dual_pipeline/transcript_qa_core/` (Agent 1)
12 files in `dual_pipeline/transcript_qa_core/` exist solely to inject pre-baked transcripts, recaps, appendices, and Output 03 graphs for specific historical meetings, completely bypassing LLM synthesis and GraphDB SSOT:
- `known_attendee_topics.json` (157 lines — 30 hardcoded speaker keys including `dan okray`, `luis arencibia`, `daniel simao`, `andrew hanson`)
- `standup_1009_content.py` (98 lines — hardcoded KDP and speaker bullets for 10/09 standup)
- `engineering_jarvis_appendix.py` (76 lines), `engineering_jarvis_forensic.py` (229 lines), `engineering_jarvis_graph.py` (340 lines), `engineering_jarvis_ontology.py` (289 lines)
- `jarvis_charter_ontology.py` (905 lines)
- `jarvis_kickoff_appendix.py` (67 lines), `jarvis_kickoff_forensic.py` (261 lines)
- `lens_ontology.py` (666 lines)
- `neet_insights_hub_graph.py` (319 lines)
- `partner_alliances_ontology.py` (592 lines)
- Plus **1,850+ lines** inside `graph_healer.py` (`Lines 1684-3540`: `_heal_noemi_monnerville_projection`, `_heal_conduit_catch_up_projection`, `_heal_digital_sherpa_taxonomy_projection`, `_heal_tactical_alignment_projection`).

### 3. Shadow Entity Directories & Precedence Inversions in `transcript_metadata_core/` & `transcript_stages_core/` (Agent 2 & Agent 3)
- **`canonical_enterprise_directory.json` (`1,540 lines`) & `entity_resolver.py` (`Lines 54-67, 205-243, 321-445, 646-835`)**:
  - Static JSON directory of 308 people overrides `self._kg_cache` (GraphDB SSOT) at `Lines 406-431`.
  - Contains prohibited synthetic personas (`"mitchell simmons"`, `"tenika johnson"` at `Lines 1342-1351`) in violation of `.agents/rules/production-purity.md`.
  - Hardcodes `"venkat"` (`Lines 321-331`), `DEFAULT_PROJECT_ROSTERS` (`Lines 740-793`), first-name project hijacking (`if any(k in all_text for k in ("ed", "jake", "karyn", "alex")): proj = "project_lens"`, `Lines 646-649`), and `_disambiguate_homonym` (`"alex"`, `"david"`, `"brandon"`, `Lines 796-835`).
- **`entity_resolver_graph.py` (`Lines 125-204, 275-300, 339-342, 750-854`)**:
  - `_ROLE_TITLE_RE` matches lowercase `"executive"` inside narrative description clauses (`"June 29 notes support Project Spring architecture/data-engineering context, Asset Data Product executive visibility"` -> extracted as Andrew Hanson's job title).
  - `_refine_org_from_directory()` (`Lines 275-286`) overwrites GraphDB orgs with `canonical_enterprise_directory.json`.
  - Hardcodes `sherpa_names` (`Lines 840-853`) and `"more" in k and "eduardo" in c_name.lower()` (`Line 300`).
- **`c06_graphdb_role_gate.py` (`Lines 84-94, 181-268`) & `speaker_role_reconciler.py` (`Lines 55-97`)**:
  - `c06_graphdb_role_gate.py` prioritizes `_CANONICAL_ENTERPRISE_DIRECTORY` (`0.85`) over GraphDB (`0.80`), marks static directory hits as `"DIRECTORY_VERIFIED"`, and stores resolved titles in `metadata["hitl_role_resolutions"]` without updating `metadata["roster"]` or `metadata["speaker_role_resolutions"]`.
  - `speaker_role_reconciler.py` applies `hitl_role_resolutions` first (`L60-77`) and `speaker_role_resolutions` second (`L79-97`), so stale C0.5 titles overwrite C0.6 GraphDB-verified titles across Outputs 01, 02, and 03.
- **`attribution_builder.py` (`Lines 19-68`)**:
  - Hardcodes `_DEFAULT_ROLES` for 10 people (`rose`, `amanda`, `meredith`, `chuck`, `stefanos`, `anthony`, `eduardo`, `amit`, `jen`, `jake`), loads `canonical_enterprise_directory.json` directly, and defaults unknown orgs to `"PartnerAdvisory"`.
- **`graph_post_entity_healer.py` (`Lines 234, 390`) & `graph_post_extractor.py` (`Lines 476-498`)**:
  - `graph_post_entity_healer.py` sets `is_lens = (proj_str == "project_lens" or "lens" in proj_str) if proj_str else True` — treating any meeting with an empty project string as Project LENS!
  - `graph_post_extractor.py` (`_ensure_roster_attendees`) parses roles via regex instead of GraphDB and defaults missing orgs to `"Apex Global"`.

### 4. Canonical Pipeline, Normalizers, Prompts & Config Bypasses (Agents 4, 5, 6, 7, 8, 9)
- **`dual_pipeline/canonical_pipeline/c0_pa_runner.py` (`Lines 144-165`)**: Hardcodes `_ensure_lens_entity_clarifications` (`tony`, `anthony`, `jason`, `alex`, `richard`, `jen`).
- **`dual_pipeline/transcript_pipeline_core/cross_meeting_context.py` (`Lines 207-228`)**: Hardcodes `"eduardo"`/`"ed"` and `"alex"`/`"sanchez"` role/company bindings.
- **`dual_pipeline/transcript_stages_core/lexical_normalizer.py` (`Lines 12-33, 105-138, 221-227`)**: Hardcodes `ayer -> Air`, `kablan -> Cablon`, `maione -> Mayon`, `siara -> Sierra`, `Serpa -> Sherpa`, and `amb_chakra_*` ALR locators.
- **`pipeline_b_extraction/entity_resolver_core/constants.py` & `extractors_core/constants.py`**: Hardcodes 22 Project LENS people (`CANONICAL_SEEDS`, `KNOWN_SPEAKERS`, `ROLE_MAP` mapping generic `"architect"` to `person_alex_sanchez` and `"project lead"` to `person_eduardo_more`).
- **`config/runtime/learned_hitl_option_store.json`**: Contains synthetic test personas (`tenika johnson`, `mitchell simmons`, `alice chen`, `bob martin`) polluting runtime priors.

---

## GraphDB SSOT Schema Mapping (`graph/nee_graph.sqlite`) (Agent 8)

`graph/nee_graph.sqlite` already contains the authoritative relational schema for all entity, title, company, and relationship resolution:
- **`node` table**:
  - People: `node_type IN ('stakeholder', 'internal_person') AND active = 1` (203 active person nodes, including `person_dan_okray`, `person_andrew_hanson`, `person_daniel_simao`, `person_luis_arencibia`, `person_jason_nicholas`, `person_amit_ayer`, etc.).
  - Organizations: `node_type = 'org' AND active = 1` (29 active org nodes, e.g., `org_slalom`, `org_fpl`, `org_edo`, `org_ec`).
  - Workstreams / Projects: `node_type IN ('workstream', 'program', 'product_lane') AND active = 1` (118 active nodes).
- **`alias` table**:
  - Maps `node_id` to verified email addresses, short names, nicknames, and phonetic variants with `confidence` and `source_id`.
- **`edge` & `assertion` tables (plus `v_current_assertion` and `v_stakeholder_map` views)**:
  - Company affiliation: `edge_type = 'AFFILIATED_WITH'` / `predicate = 'AFFILIATED_WITH'` (and `ROSTER_ROW_IN`, `DATED_ORG_CHART_NAME_IN`).
  - Verified job titles & roles: `edge_type = 'DATED_ROLE_IN'` / `predicate = 'HAS_ROLE_IN'` (`qualifiers_json -> $.description`), `OWNS_OR_LEADS`, `CONTRIBUTES_TO`.
  - Reporting hierarchy: `edge_type = 'REPORTS_TO'` / `predicate = 'REPORTS_TO'` (`OPERATING_UNDER`).
