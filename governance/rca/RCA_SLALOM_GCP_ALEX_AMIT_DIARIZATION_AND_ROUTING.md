# Adversarial Root Cause Analysis (Ultra RCA): Catastrophic Diarization Collapse, Attribution Blindness, and Intelligent Folder Misrouting

**Incident Identifier:** `RCA-2026-09-29-DIAR-ROUTER-COLLAPSE`  
**Target Subject:** Meeting `Slalom_GCP_Alex_Amit` (Session: `Slalom_Gcp_Shreyak_Slalom_150712`)  
**Meeting Date & Time:** `09/29/2026`, 03:07 PM – 03:50 PM ET (Master Audio Duration: 2,172.36s / 36.21m)  
**Governed Directive:** `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1`  
**Active Knowledge Graph Generation:** `generation-f0480f3f41de63c0e8c6ea7e1d04`  
**Investigation Branch / Worktree:** `feat/rca-adversarial-pyannote-investigation` (`.worktrees/rca_adversarial_pyannote`)  
**Investigation Status:** `DIAGNOSTIC_COMPLETE` (Adversarial Red-Team Characterization)  
**Execution Boundary:** **PLAN ONLY / STRICT NO IMPLEMENTATION**  

---

## 1. Executive Summary & Forensic Topology

On September 29, 2026, a 36-minute partner sync between PartnerAdvisory and Google Cloud leadership (`Slalom_GCP_Alex_Amit`) was ingested through the live recording and canonical ingestion pipeline. The resulting deliverable `01_Slalom_Gcp_Alex_Amit_09-29-2026_0307PM0350PMET_hitl_resolved_transcript.md` and downstream signal projection collapsed into a 2-person monologue:
- **Amit Ayer:** 89% turn share, including a single monolithic 7-minute 28-second turn (`stmt-003` at `[00:02:50]`).
- **Dan Okray:** 11% turn share.
- **Google Cloud Attendees (Alex Brannan, Dianne Eldridge, Robson Silva, Vani Eswarappa):** **0 turns attributed (0.0%)**, despite actively speaking for >3.5 minutes in the master audio recording.

Crucially, **Output 01 passed all deterministic QA gates (Gate 1.2, Gate 1.6, Gate 1.7) and received a 100/100 score from the Dialectical LLM Judge (`gpt-5.6-sol`)**. Furthermore, the meeting was completely misrouted into Apex Global's `project_nexus`, polluting the production knowledge graph with 546 candidate assertions derived from this corrupted fragment under directive `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1`.

### High-Level Incident Topology

```
[Master Audio (36.2m)] ────────────────────────────────────────────────────────┐
   │ (Single Mono Mix - AirPods / Room Mic Starvation)                         │
   ├── PyAnnote 3.1 Post-Recording: 599 turns across 5 distinct speakers       │
   │   (SPK_00: Amit, SPK_01: Dan, SPK_02: Alex, SPK_03: Dianne, SPK_04: Vani)│
   │                                                                           │
[Live Chunker & Whisper]                                                       │
   │ Chunks dropped/starved -> Cumulative elapsed clock pauses                 │
   │ Live Clock at Chunk 156: 515.1s (08:35) vs Master WAV Clock: 1522.8s (25:22)│
   │ ──> 1,007.7-SECOND TIME-BASE DESYNCHRONIZATION                            │
   │                                                                           │
[Post-Recording Aligner] <─────────────────────────────────────────────────────┘
   │ Aligner queries PyAnnote at 515s (Amit speaking) instead of 1522s (Vani/Alex)
   │ ──> ACTIVELY OVERWRITES Google speakers with Amit Ayer & Dan Okray
   │
[Cross-Meeting Ledger Poisoning]
   │ Unpartitioned fuzzy match on "Speaker 1" matches yesterday's Apex Global meeting
   │ ──> Injects "Daniel Brankin" as Speaker 1
   │
[QA Gates & LLM Judge]
   │ Gate 1.2: Substring "Alex Brannan in f1_text" matches header -> PASS
   │ Gate 1.6: [Recording gap] marker present -> Density checks completely skipped
   │ Single-Speaker Collapse: Hardcoded floor "total_turns < 10" (7 < 10) -> BYPASSED
   │ LLM Judge: Evaluates broken transcript against itself -> 100/100 PASS
   │ Silence Gap Classifier: Δ = 8% < 15% -> Mints "Autonomous Silence Classification"
   │
[Intelligent Folder Failure]
   │ 5 hardcoded fallbacks to "project_nexus" (Prompt Rule 3, CLI, Lifecycle)
   │ Passing mention: "We just got that new Nexus project from Apex Global"
   │ ──> Dumps Google Cloud partner sync into Apex Global project_nexus graph
```

---

## 2. Forensic Investigation: Deep-Dive into the Five Failure Pillars

### Pillar 1: Acoustic Diarization & The 1,007-Second Time-Base Overwrite Bug

#### Did PyAnnote Fire with HuggingFace Token?
**Yes.** Forensic inspection of `.system_generated/tasks/task-130.log` conclusively disproves the hypothesis that PyAnnote failed to initialize or lacked authorization:
- `task-130.log:4`: HTTP 200 OK from HuggingFace model hub for `pyannote/speaker-diarization-3.1`.
- `task-130.log:24`: PyAnnote 3.1 pipeline moved to Apple Silicon Metal Performance Shaders (`mps`).
- In `00_09-29-2026_Slalom_Gcp_Shreyak_Slalom_150712_diarization_tracks.json`, PyAnnote successfully segmented the mono master audio (`master_150712.wav`, 2172.36s) into **599 turns across 5 distinct speakers**:
  - `SPEAKER_00`: 364 turns (797.25s) $\rightarrow$ Amit Ayer
  - `SPEAKER_01`: 166 turns (204.52s) $\rightarrow$ Dan Okray
  - `SPEAKER_02`: 9 turns (15.25s, 1537s–1745s) $\rightarrow$ Alex Brannan (Google)
  - `SPEAKER_03`: 26 turns (86.85s, 1559s–1723s) $\rightarrow$ Dianne Eldridge (Google)
  - `SPEAKER_04`: 34 turns (64.33s, 1520s–1674s) $\rightarrow$ Vani Eswarappa (Google)

**Channel isolation is NOT required.** PyAnnote 3.1 cleanly separated all five speakers on a single mono track.

#### The Overwrite Mechanism: Cumulative Clock vs. Master Timeline
PyAnnote ran **exclusively post-recording**. During live recording, chunk attribution relied entirely on text regex heuristics (`ConversationalSpeakerTracker`).

In `dual_pipeline/live_meeting_recorder.py:600-618`, the live recorder maintained a local variable `_cumulative_elapsed_seconds`. Whenever a chunk was dropped, starved, or failed transcription, `_cumulative_elapsed_seconds` **stopped incrementing**.
- By Chunk 156:
  - `segments.jsonl` clock was recorded at **515.1s – 520.7s** (`[08:35]`).
  - Master WAV true sample clock was at **1522.8s – 1528.5s** (`[25:22]`).
  - **Total Clock Desynchronization: 1,007.7 seconds (~16.8 minutes)!**

In `_align_transcript_with_acoustic_turns()` (`dual_pipeline/live_meeting_recorder.py:970-1005`), the post-recording aligner matched each live segment to PyAnnote using `seg["start_sec"]`:
```python
# The Flawed Alignment Logic
for seg in segments:
    s_start = seg["start_sec"]  # Stored as 515.1s!
    s_end = seg["end_sec"]      # Stored as 520.7s!
    # Queries PyAnnote at 515s, where Amit Ayer was speaking!
    # Master WAV at 1522s had Vani Eswarappa (SPEAKER_04) speaking!
    best_turn = find_overlapping_turn(acoustic_turns, s_start, s_end)
    seg["speaker"] = best_turn["speaker"] # OVERWRITTEN TO AMIT AYER
```
Because the aligner queried PyAnnote at `515s` instead of `1522s`, **it actively stripped Vani Eswarappa, Dianne Eldridge, and Alex Brannan and replaced them with Amit Ayer and Dan Okray**. The subsequent markdown renderer merged adjacent turns, creating the massive 7.5-minute monologue (`stmt-003`).

---

### Pillar 2: Audio Starvation & The Whisper "Closed Captioning" Hallucination Loop

1. **ScreenCaptureKit Audio Starvation:**
   - At `15:12:53` (`task-130.log:120-123`), ScreenCaptureKit starved (chunk remained 0 bytes > 5s).
   - Audio watchdog engaged auto-fallback to the physical Mac microphone (`:0`).
   - The operator was wearing AirPods Pro. The Mac physical mic recorded near-absolute room silence while meeting audio played through headphones.
2. **Lexical Priming Hallucination:**
   - In `dual_pipeline/live_meeting_recorder_core/lexical_priming.py`, Whisper's initial prompt was primed with attendee names: `"Alex Brannan, Amit Ayer, Dan Okray, Dianne Eldridge, Vani Eswarappa"`.
   - On low-RMS room silence, OpenAI Whisper models hallucinate broadcast caption sequences when conditioned with proper names.
   - Whisper produced:
     - `chunk_0214` (828s): `"Closed Captions by CSI"`
     - `chunk_0222` (866s): `"Closed Captioning provided by the Amit"`
     - `chunk_0266` (1092s): `"Closed Captioning provided by the Amit"`
3. **Filter Escape & Chunk Dropping:**
   - In `dual_pipeline/live_meeting_recorder_core/chunk_transcriber.py:22-31`, `HALLUCINATIONS` set contained `"subtitles by"`, `"thank you for watching"`, but **lacked `"closed captioning"` and `"closed captions"`**.
   - These hallucinations were written directly into `segments.jsonl` and attributed to `Dan Okray` at `[18:12]`.
   - Subsequent chunks (267–279) were dropped by repetition filters, terminating transcript generation at 18:12 while master audio continued until 36:21.

---

### Pillar 3: Cross-Meeting Decision Ledger Contamination (`Daniel Brankin`)

In `task-130.log:2461`, the system logged:
`"Prior ledger resolution applied for 'Speaker 1' -> 'Daniel Brankin'"`

1. **The Flawed Lookup:**
   - In `dual_pipeline/speaker_attribution_core/audio_uncertainty_detector.py:204-245`, `check_ledger_for_audio_resolution()` performs an unpartitioned search in `hitl_decision_ledger.json`.
   - It searches for prior operator decisions matching the raw cluster string `"Speaker 1"`.
2. **The Contamination:**
   - On September 28, 2026, in meeting `Followup_Business_Case_Jill_Slalom`, the human operator resolved `"Speaker 1"` to `Daniel Brankin` (Apex Global Jarvis Product Owner).
   - Because both meetings were assigned `project="project_nexus"`, the resolver treated this decision as an active calibrated prior.
   - It injected `Daniel Brankin` into `Slalom_GCP_Alex_Amit` as `Speaker 1`, attributing Google Cloud partner statements to a Apex Global executive who was not on the attendee roster.

---

### Pillar 4: QA Gate 1 & LLM Judge Exploit Vectors (Why Output 01 Passed)

Output 01 passed deterministic gates and received 100/100 from `gpt-5.6-sol` due to five exploitable structural loopholes:

#### Exploit 1: The Hardcoded `< 10 turns` Floor in Single-Speaker Collapse
- **Code Locations:**
  - `tools/qa_gates/collapse_detector.py:192`
  - `dual_pipeline/transcript_qa_core/gate1_attribution.py:133`
  - `dual_pipeline/transcript_qa_core/forensic_checks.py:106`
  - `governance/decision_intelligence/FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md:21`
- **The Bug:**
  ```python
  if total_turns < 10:
      return False, 0.0, "EXEMPT_SHORT_TRANSCRIPT"
  ```
  The single-speaker collapse check **only counts turn boundaries**, completely ignoring turn duration and word share. Because the aligner merged multiple chunks into single turns, the substantive turn count was only 7 ($7 < 10$). Amit Ayer's 7.5-minute monologue counted as 1 turn, completely evading the 85.0% collapse detector!

#### Exploit 2: Naive Substring Matching in Gate 1.2 (`speakers_verified`)
- **Code Location:** `dual_pipeline/transcript_qa_core/gate1_attribution.py:277`
- **The Bug:**
  ```python
  for attendee in confirmed_roster:
      if attendee["name"] in full_markdown_text:
          verified_speakers.append(attendee["name"])
  ```
  The gate searched the entire markdown file for the attendee's name string. Because Alex Brannan, Dianne Eldridge, and Vani Eswarappa appeared in the metadata header and the Ambiguity Lock Register (ALR), `speakers_verified` returned `True` for 100% of the roster, even though they had **0 turns** in the body!

#### Exploit 3: Density Check Bypass in Gate 1.6
- **Code Location:** `dual_pipeline/transcript_qa_core/gate1.py:254`
- **The Bug:**
  ```python
  if meeting_dur and not has_gap_annotation:
      _evaluate_density(...)
  ```
  Because the transcript contained the cosmetic tag `[Recording gap: 00:11:33 – 00:18:12]`, `has_gap_annotation` evaluated to `True`, completely bypassing turn density and words-per-minute checks for an 18-minute truncated recording!

#### Exploit 4: LLM Judge Circular Ground Truth
- In `dual_pipeline/transcript_stages_core/exit_evaluator.py`, `<reference_ground_truth>` was passed the upstream live transcript file, which was already truncated and broken.
- The judge compared Output 01 against the broken live transcript verbatim, awarding 25/25 on Dimension 1 (Content Preservation).
- The judge prompt instructed the judge to accept the ALR as authoritative truth. Because the ALR claimed Google participants had "no attributable source turns due to silent session pause", the judge treated this fabrication as verified reality.

#### Exploit 5: Autonomous Silence Classification Bypass ($\Delta < 15\%$)
- In `dual_pipeline/transcript_stages_core/silence_gap_classifier.py:83-109`, the 1,488-second silence gap was evaluated by a local classifier:
  - Option 0 (`NATURAL_SESSION_PAUSE`): 0.54
  - Option 1 (`AUDIO_INGEST_FAILURE`): 0.46
  - **Confidence Margin $\Delta = 0.54 - 0.46 = 0.08$ (8% < 15%)**.
- Per AGENTS.md, $\Delta < 15\%$ strictly mandates interactive escalation via `ask_question`.
- However, in headless execution (`interactive_resolver is None`), the code defaulted to Option 0, minted `operator_id="autonomous_context"`, and forged an "Autonomous Silence Classification" receipt.

---

### Pillar 5: Intelligent Folder & Multi-Tenant Routing Architecture Breakdown

Why was a PartnerAdvisory / Google Cloud partner sync routed to Apex Global's `project_nexus`?

#### 1. Five Hardcoded Fallbacks to `project_nexus`
1. **Dynamic Prompt Contract:**
   [`governance/decision_intelligence/DYNAMIC_PROJECT_ROUTER_PROMPT.md:12`](file:///Users/amitayer/Git/decision-intelligence-engine/governance/decision_intelligence/DYNAMIC_PROJECT_ROUTER_PROMPT.md#L12)
   > *"3. If no project clearly matches, fallback to 'project_nexus' (or whichever seems most likely), but it MUST be exactly from the list."*
2. **CLI Parser Default:**
   [`dual_pipeline/cli/parser.py:1145`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/cli/parser.py#L1145)
   > `command.add_argument("--project", default="project_nexus", help="Governed project name")`
3. **CLI Execution Engine:**
   [`dual_pipeline/cli/engine.py:1044-1046`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/cli/engine.py#L1044-L1046)
   > `elif "nea" in combined_text or "reid" in combined_text: project_val = "project_nexus"`  
   > `else: project_val = "project_nexus"`
4. **Canonical Lifecycle Resolution:**
   [`dual_pipeline/canonical_pipeline/lifecycle.py:133`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/canonical_pipeline/lifecycle.py#L133)
   > `def _resolve_project_name(...) -> str: ... return "project_nexus"`
5. **Metadata Scaffolder:**
   [`dual_pipeline/transcript_metadata_helper.py:163`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_metadata_helper.py#L163)
   > `if "nexus" in stem_low or "reid" in stem_low: return "project_nexus"`

#### 2. Taxonomy Blindspot in `project_registry.json`
- `config/governance/project_registry.json` contains:
  `["conduit", "project_beacon", "ec_scheduling", "enterprise_architecture", "florida_account_planning", "fpl_rewire", "internal_slalom", "nea", "neet", "project_lens", "project_nea", "project_nexus", "project_spring", "regai"]`.
- There is **no identifier or alias** for Google Cloud partnership, partner alliances, or joint vendor syncs (`partner_alliances`, `alliances_gcp`, `slalom_gcp`).
- `internal_slalom` only matches strings starting with `"internal PartnerAdvisory"`.

#### 3. Lexical Keyword Gravitational Attraction
- In statement `stmt-003` at `[00:02:50]`, Amit Ayer mentioned:
  > *"We just got that new Nexus project from the Apex Global team. So who's the main contact for us? ... I was waiting for that project to come up. I think that's going to be popular."*
- The dynamic router took the first 1,000 characters of the transcript. The only project token present in that window was `"Nexus"`.
- Combined with Rule 3's hardcoded fallback instruction, the router collapsed into `project_nexus`.

---

## 3. Mandatory Knowledge Graph Directive Quarantine & Rollback Plan

### Target Directive
- **Directive ID:** `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1`
- **Associated Run ID:** `Slalom_Gcp_Alex_Amit`
- **Target Active Generation:** `generation-f0480f3f41de63c0e8c6ea7e1d04`
- **Target SQLite File:** `.local/dual_pipeline/generations/generation-f0480f3f41de63c0e8c6ea7e1d04/graph.sqlite`
- **Target Vector Sidecars:** Dense vectors, sparse lexical weights, ColBERT tokens, and retrieval evaluation records in `.local/dual_pipeline/generations/generation-f0480f3f41de63c0e8c6ea7e1d04/`.

### Required Quarantine Operations
1. **Directive State Mutation:** Transition directive status from `OPEN` / `ACTIVE` to `QUARANTINED` in `.local/ledger/directive_registry.json`. Record explicit blocker reason code: `DIARIZATION_COLLAPSE_ROUTING_MISMATCH`.
2. **Purge Extraction Run & Candidate Assertions:**
   - Delete all assertions in `assertion` table linked to extraction run `Slalom_Gcp_Alex_Amit` (546 candidate assertions).
   - Delete evidence items from `evidence_item` and `assertion_evidence`.
   - Purge search documents from `search_document` and `search_fts`.
   - Remove dense/sparse/ColBERT vector embeddings corresponding to purged assertions.
3. **Rollback Generation or Issue Compensating Release Receipt:**
   - Execute clean graph compaction and recompute `graph_sha256`.
   - Update `active_pair.json` to reflect the purged, sanitized generation state.

---

## 4. Comprehensive 6-Wave Implementation Plan

This plan adheres strictly to the **Wave-Based Receipt-Gated Progression Standard** (AGENTS.md). Execution of downstream waves is locked until prior waves produce valid runtime receipts.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 1: Directive Quarantine & Graph DB Rollback                       │
│ (Mark directive QUARANTINED, purge 546 candidate assertions)           │
└────────────────────────────────────┬───────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 2: Intelligent Folder & Multi-Tenant Routing Architecture Overhaul│
│ (Remove 5 hardcoded fallbacks, add partner_alliances, roster domain)  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 3: Sample-Accurate Timeline & Acoustic Alignment Synchronizer     │
│ (Fix 1,007s drift, sample-accurate offsets, overwrite guard)           │
└────────────────────────────────────┬───────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 4: Ingress Watchdog & Whisper Hallucination Hardening             │
│ (AirPods pause, expand HALLUCINATIONS, scoped ledger isolation)        │
└────────────────────────────────────┬───────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 5: QA Gate 1 & LLM Judge Hardening (Anti-Monologue & Anti-Bypass) │
│ (Remove <10 turns floor, header-only attribution, Δ<15% HITL gate)     │
└────────────────────────────────────┬───────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Wave 6: Master WAV Full Audio Recovery & Re-Ingestion into Alliances   │
│ (Re-transcribe master_150712.wav with 5 speakers, deliver Output 01-03)│
└────────────────────────────────────────────────────────────────────────┘
```

---

### Wave 1: Directive Quarantine & Knowledge Graph Rollback
- **Objective & Scope:** Formally quarantine `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1` and purge all candidate assertions, evidence items, and vector embeddings created by `Slalom_Gcp_Alex_Amit` from `project_nexus` active generation.
- **Milestones & Deliverables:**
  1. Script/command `tools/quarantine_directive.py` executed against `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1`.
  2. Directive marked `QUARANTINED` in `.local/ledger/directive_registry.json`.
  3. Graph database SQLite records purged: `assertion` (-546), `evidence_item` (-900), `search_document`, `search_fts`.
  4. Vector sidecars synchronized: dense vectors, sparse BM25 index, and ColBERT tokens purged.
  5. Rollback verification receipt emitted: `reports/rollback_slalom_gcp_alex_amit_receipt.json`.
- **Acceptance Criteria:**
  - `graph_sha256` transition verified.
  - Zero candidate assertions from `Slalom_Gcp_Alex_Amit` remain in `project_nexus` active graph.
  - Query test `python -m dual_pipeline.query --project project_nexus "Alex Brannan Google Cloud"` returns zero hits.
- **Runtime Receipt & Completion Gate:**
  - Command: `python tools/quarantine_directive.py --directive-id directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1 --verify-clean`
  - Gate: Immutable rollback receipt signed with Ed25519; status `COMPLETE`.

---

### Wave 2: Intelligent Folder & Multi-Tenant Routing Architecture Overhaul
- **Objective & Scope:** Eliminate all 5 hardcoded `project_nexus` fallbacks, expand project taxonomy to support joint partner alliances, implement deterministic attendee domain routing, and enforce Stage U0 HITL escalation when routing confidence $\Delta < 15\%$.
- **Milestones & Deliverables:**
  1. `DYNAMIC_PROJECT_ROUTER_PROMPT.md`: Delete Rule 3 hardcoded fallback; replace with confidence scoring and explicit `null` return on diffuse match.
  2. `dual_pipeline/cli/parser.py`: Change `--project` default from `"project_nexus"` to `None` in `record-meeting`.
  3. `dual_pipeline/cli/engine.py`: Remove lines 1044-1046 hardcoded fallbacks; add attendee-domain partner routing.
  4. `dual_pipeline/canonical_pipeline/lifecycle.py`: Replace line 133 `return "project_nexus"` with dynamic project resolution and ambiguity gating.
  5. `config/governance/project_registry.json`: Add `partner_alliances` project and aliases (`PartnerAdvisory gcp`, `PartnerAdvisory google`, `gcp partner`, `partner sync`, `alliances`).
  6. `dual_pipeline/query_router_core/project_router.py`: Add `_resolve_project_from_roster()` checking `@google.com` + `@PartnerAdvisory.com` domains.
  7. Unit and E2E regression tests: `tests/test_intelligent_project_router.py`.
- **Acceptance Criteria:**
  - Routing `Slalom_GCP_Alex_Amit` with Google + PartnerAdvisory attendees automatically resolves to `partner_alliances` with confidence $> 0.90$.
  - Passing mention of `"Nexus"` in dialogue does not divert meeting into `project_nexus`.
  - When confidence margin $\Delta < 15\%$, router raises `RoutingAmbiguityError` and invokes `ask_question`.
- **Runtime Receipt & Completion Gate:**
  - Command: `pytest tests/test_intelligent_project_router.py --timeout=60 -v`
  - Gate: 100% pass across all routing fixtures with zero fallbacks to `project_nexus`.

---

### Wave 3: Sample-Accurate Timeline & Acoustic Alignment Synchronizer
- **Objective & Scope:** Eliminate the 1,007-second time-base drift between live chunks and master audio by binding transcript timestamps directly to master WAV sample offsets, and prevent aligner overwrites.
- **Milestones & Deliverables:**
  1. `dual_pipeline/live_meeting_recorder.py`: Deprecate `_cumulative_elapsed_seconds`. Calculate chunk start/end timestamps strictly from `AudioMasterStitcher` frame counts and wall-clock monotonic references.
  2. `_align_transcript_with_acoustic_turns()`: Add sample-accurate temporal intersection guard. If acoustic turn overlap is $< 0.15$s or if time difference $> 30$s, fail closed with `AlignmentDesynchronizationError`.
  3. Overwrite Guard: Prevent post-recording alignment from reducing the count of active acoustic speaker clusters by $> 1$.
  4. Integration tests: `tests/test_live_recorder_timeline_sync.py` simulating dropped chunks and verifying time alignment.
- **Acceptance Criteria:**
  - Chunk 156 timestamps match master audio at 1522.8s (zero drift).
  - Aligner correctly maps `SPEAKER_02` (Alex), `SPEAKER_03` (Dianne), and `SPEAKER_04` (Vani) to their respective segments.
- **Runtime Receipt & Completion Gate:**
  - Command: `pytest tests/test_live_recorder_timeline_sync.py --timeout=60 -v`
  - Gate: Max clock skew between segments and master WAV $< 0.05$s across 300 simulated chunks.

---

### Wave 4: Ingress Watchdog & Whisper Hallucination Hardening
- **Objective & Scope:** Prevent Whisper broadcast caption hallucinations during low-RMS audio, pause transcription on headphone/mic starvation, and isolate the decision ledger by meeting slug.
- **Milestones & Deliverables:**
  1. `dual_pipeline/live_meeting_recorder_core/chunk_transcriber.py`: Expand `HALLUCINATIONS` set with broadcast caption tokens (`"closed captioning"`, `"closed captions"`, `"subtitles by"`, `"captions provided by"`).
  2. `dual_pipeline/live_meeting_recorder_core/audio_capture.py`: When audio RMS drops below silence threshold (`RMS < 0.005`) for $> 10$s during headphone transition, enter `AUDIO_STARVATION_PAUSE` instead of feeding silence to primed Whisper.
  3. `dual_pipeline/speaker_attribution_core/audio_uncertainty_detector.py`: Scope `check_ledger_for_audio_resolution()` strictly to `(project, standardized_slug, date)` tuple or acoustic voiceprint hash. Ban global matching on `"Speaker 1"`.
  4. Tests: `tests/test_whisper_hallucination_filter.py` and `tests/test_ledger_scope_isolation.py`.
- **Acceptance Criteria:**
  - Whisper output `"Closed Captioning provided by the Amit"` is scrubbed and marked as silence.
  - Decision ledger entry for `Daniel Brankin` is never loaded into `Slalom_GCP_Alex_Amit`.
- **Runtime Receipt & Completion Gate:**
  - Command: `pytest tests/test_whisper_hallucination_filter.py tests/test_ledger_scope_isolation.py --timeout=60 -v`
  - Gate: 100% pass; zero cross-meeting ledger leakage.

---

### Wave 5: QA Gate 1 & Dialectical LLM Judge Hardening
- **Objective & Scope:** Close all 5 QA gate exploit vectors: remove `< 10 turns` floor, evaluate duration/word-count monologue metrics, enforce turn-header speaker verification, require external partner attribution, and ban autonomous silence classification.
- **Milestones & Deliverables:**
  1. Remove `total_turns < 10` exemption across `collapse_detector.py:192`, `gate1_attribution.py:133`, `forensic_checks.py:106`, and `FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md:21`.
  2. Implement Multi-Dimensional Collapse Metric: Fail Gate 1 if single speaker accounts for $> 65\%$ of words or $> 65\%$ of audio time, or if any single turn exceeds 180 seconds without HITL authorization.
  3. `gate1_attribution.py:277`: Check `name in f1_text` strictly against markdown turn speaker headers (`**[HH:MM]** **<Name>:**`), never whole-document text.
  4. Partner Attribution Gate: In meetings with multiple confirmed organizations on roster, fail closed if an entire external organization has 0 attributed turns.
  5. `gate1.py:254`: Enforce density checks regardless of `has_gap_annotation`.
  6. `silence_gap_classifier.py`: Prohibit autonomous resolution when $\Delta < 15\%$. Require operator interaction via `ask_question`. Ban `operator_id="autonomous_context"`.
  7. Judge Prompt Hardening: Feed raw acoustic track turn counts into `<acoustic_evidence>` block; instruct judge to fail transcripts where acoustic turns exist for attendees who have 0 dialogue turns.
  8. Tests: `tests/test_qa_gate1_adversarial_collapse.py`.
- **Acceptance Criteria:**
  - The broken `01_Slalom_Gcp_Alex_Amit_...` transcript fails Gate 1.2, Gate 1.6, and Gate 1.7 closed with `COLLAPSE_DETECTED` and `PARTNER_ORGANIZATION_MISSING`.
  - Dialectical LLM Judge rejects the monologue with score $< 40/100$.
- **Runtime Receipt & Completion Gate:**
  - Command: `pytest tests/test_qa_gate1_adversarial_collapse.py --timeout=60 -v`
  - Gate: Deterministic rejection of broken transcript across all 5 exploit vectors.

---

### Wave 6: Master WAV Audio Recovery, Re-Transcription & Re-Ingestion
- **Objective & Scope:** Re-process `master_150712.wav` (36.21m) from scratch using the hardened pipeline, correctly attributing all 5 speakers and routing to `partner_alliances`.
- **Milestones & Deliverables:**
  1. Run offline Whisper transcription on `master_150712.wav` aligned with the 599 PyAnnote acoustic turns.
  2. Resolve acoustic clusters:
     - `SPEAKER_00` $\rightarrow$ Amit Ayer (PartnerAdvisory)
     - `SPEAKER_01` $\rightarrow$ Dan Okray (PartnerAdvisory)
     - `SPEAKER_02` $\rightarrow$ Alex Brannan (Google Cloud)
     - `SPEAKER_03` $\rightarrow$ Dianne Eldridge (Google Cloud)
     - `SPEAKER_04` $\rightarrow$ Vani Eswarappa (Google Cloud)
  3. Ingest via governed CLI:
     ```bash
     nee ingest-meeting \
       --input "output/transcript_cache/project_nexus/09-29-2026/00_09-29-2026_Slalom_Gcp_Shreyak_Slalom_master_150712.wav" \
       --project "partner_alliances" \
       --meeting "Slalom_GCP_Alex_Amit" \
       --date "09/29/2026" \
       --attendees '[{"name":"Alex Brannan","org":"Google"},{"name":"Amit Ayer","org":"PartnerAdvisory"},{"name":"Dan Okray","org":"PartnerAdvisory"},{"name":"Dianne Eldridge","org":"Google"},{"name":"Vani Eswarappa","org":"Google"},{"name":"Robson N. Silva","org":"Google"},{"name":"Anya Gayvoronskaya","org":"PartnerAdvisory"}]' \
       --provider llm \
       --mutate-graph \
       --directive-id "directive-partner_alliances-slalom_gcp_alex_amit-09-29-2026-v1" \
       --reviewer "Amit Ayer" \
       --rationale "Re-ingest PartnerAdvisory GCP partner sync into partner_alliances with full 5-speaker attribution"
     ```
  4. Generate and seal:
     - `01_Slalom_Gcp_Alex_Amit_09-29-2026_0307PM0350PMET_hitl_resolved_transcript.md`
     - `02_Slalom_Gcp_Alex_Amit_09-29-2026_0307PM0350PMET_executive_recap.md`
     - `03_Slalom_Gcp_Alex_Amit_09-29-2026_0307PM0350PMET_canonical_signal_projection.json`
- **Acceptance Criteria:**
  - All 5 active speakers have dialogue turns proportional to PyAnnote acoustic durations.
  - Zero single-speaker collapse; QA Gate 1 passes legitimately; LLM Judge passes with grounded score $\ge 90/100$.
  - Knowledge graph mutations are routed to `partner_alliances` with zero cross-contamination into `project_nexus`.
- **Runtime Receipt & Completion Gate:**
  - Command: Canonical pipeline receipt verification and 11-gate retrieval clearance.
  - Gate: Universal write gateway activation receipt emitted.

---

## 5. Wave Implementation Status Table

| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | Directive Quarantine & Knowledge Graph Rollback | `⏳ OPEN` (Planned) | Purge 546 candidate assertions and mark `directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1` QUARANTINED in `generation-f0480f3f41de63c0e8c6ea7e1d04` | `python tools/quarantine_directive.py --directive-id directive-project_nexus-slalom_gcp_alex_amit-09-29-2026-v1` |
| **Wave 2** | Intelligent Folder & Multi-Tenant Routing Architecture | `⏳ OPEN` (Planned) | Remove 5 hardcoded `project_nexus` fallbacks; add `partner_alliances` to `project_registry.json`; roster domain router | `pytest tests/test_intelligent_project_router.py -v` |
| **Wave 3** | Sample-Accurate Timeline & Acoustic Alignment | `⏳ OPEN` (Planned) | Deprecate cumulative elapsed drift; bind segments to master WAV frame offsets; prevent speaker overwrites | `pytest tests/test_live_recorder_timeline_sync.py -v` |
| **Wave 4** | Ingress Watchdog & Whisper Hallucination Hardening | `⏳ OPEN` (Planned) | Expand `HALLUCINATIONS` ("closed captioning"); audio starvation pause; partition decision ledger by meeting slug | `pytest tests/test_whisper_hallucination_filter.py tests/test_ledger_scope_isolation.py -v` |
| **Wave 5** | QA Gate 1 & LLM Judge Hardening (Anti-Monologue) | `⏳ OPEN` (Planned) | Remove `< 10 turns` floor; word/duration collapse metrics; turn-header attribution; ban autonomous silence classification | `pytest tests/test_qa_gate1_adversarial_collapse.py -v` |
| **Wave 6** | Master WAV Audio Recovery & Re-Ingestion | `⏳ OPEN` (Planned) | Full 36-minute 5-speaker transcript re-generation from `master_150712.wav`; deliver Outputs 01–03 into `partner_alliances` | Canonical pipeline execution receipt and 11-gate retrieval clearance |

---

## 6. Summary of Invariant Compliance
- **Truth Invariant:** Grounded 100% in empirical logs (`task-130.log`), audio waveforms (`master_150712.wav`), acoustic tracks (`599 turns`), and AST inspections of pipeline code.
- **Scope Containment:** Analysis and planning conducted in dedicated worktree branch `feat/rca-adversarial-pyannote-investigation`.
- **Plan-Only Firewall:** Zero production code modified; zero graph mutations executed in this turn.
