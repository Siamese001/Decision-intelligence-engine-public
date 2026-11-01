---
name: nee-transcript-synthesis
description: Meeting audio recording, copilot in-call advisory, and transcript synthesis under RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md. Generates 01-03 signal projections.
---

# NEE Transcript Synthesis & Live Audio Operations

This skill guides operations for processing meeting recordings, audio files, and raw transcripts into governed knowledge-graph signals.

## When to use

- User supplies meeting audio (.m4a, .mp3, .wav, etc.) or raw transcript text (.txt, .md)
- Processing meeting minutes or team discussions into executive recaps
- Extracting entity upserts, relationship signals, and decisions into graph deltas
- Packaging meeting signals for evidence envelope ingestion

## Canonical Prompt Contract

All transcript synthesis is contract-bound to:
[`config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md`](file:///Users/amitayer/Git/decision-intelligence-engine/config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md)

## Live Meeting Audio Recording (Real-Time)

When a user requests to **"start recording"**, **"record meeting"**, or **"capture live audio"**, execute the unified live recorder immediately without exploratory directory scans or one-off scripts:

```bash
# Governed CLI command (< 250ms background dispatch):
.venv/bin/python nee.py record-meeting \
  --project <project_name> \
  --meeting "<meeting_title>" \
  --attendees "<comma-separated-roster>"
```

- **Pinned Interpreter**: Always invoke via `.venv/bin/python` to guarantee required native dependencies (`pydantic`, `mlx`, `avfoundation`).
- **Immediate Background Dispatch**: Capture starts immediately before long-form model reasoning, planning, or directory searches.
- **Zero Terminal Delegation**: Microphone and ScreenCaptureKit permissions must be onboarded in advance outside the meeting. Never infer permissions failure without verified OS denial telemetry (`SCK_TCC_DENIED`), and never instruct the user to run Terminal commands.
- **Independent State Machine**: Capture state (`REQUESTED -> STARTING -> CAPTURING`) is decoupled from transcription readiness (`ASR_WARMING -> ASR_READY -> ASR_FAILED`). Display confirmation only after verified source-bound PCM proof.
- **Output Destination**: Automatically maps to `output/<project_name>/<MM-DD-YYYY>/`
- **Stopping Recording**: Send SIGINT / SIGTERM or notify the process to flush remaining chunks, validate WAV chunks (> 78 bytes), and formulate the canonical `nee ingest-meeting` invocation for operator approval. Seek publication approval concurrently while ingestion runs in the background. If retries or remediation are needed, allow at most 2 automated cycles within 600s, never manually edit `01_*` or `02_*` deliverables, and resume via `nee ingest-meeting --resume` to leverage warm cached stages.

### Progressive Start Flow ("start recording")

Recording launches immediately (< 500ms). While audio captures, metadata is collected progressively:
1. **Prompt 1: Project**: `project_nexus`, `apex_trans`, `project_lens`, `project_beacon`, `project_atlas`, etc.
2. **Prompt 2: Title**: Sets output slug, worktree branch name, and recap header.
3. **Prompt 3: Attendees**: Injected into Whisper primer, Pyannote candidate labels, and graph entity seeds.
4. **Prompt 4: Date / Time**: Routes output folder (`output/<project>/<MM-DD-YYYY>/`).

### Live In-Call Copilot ("copilot", "next best question - copilot", Default Live Routing)

During an active meeting recording:
1. **Default Live Copilot Routing**: Every question or prompt (even without the `copilot` prefix) defaults to `nee copilot` using the live transcript buffer and active graph cache.
2. **Explicit Deep Graph Query Escape Hatch (`query` prefix)**: Only when a prompt explicitly begins with the prefix `query` (e.g., `query <question>`) should the live transcript be ignored in favor of deep GraphDB traversal (`nee ask` / `nee query`).
- **Strict Prohibition**: NEVER read historical `*_live_transcript.md` or `output/**` using `cat`, `grep`, `tail`, or `view_file` (Anti-Fallback Rule C5).
- **Usage**: `nee copilot [--request "<natural_language_question>"] [--intent {next-questions,next-actions,respond,graph-context,gaps,connect}] [--target <speaker>] [--active]`
  - `--request "<question>"` (or positional `"<question>"`): Pass the operator's exact natural-language question so Copilot preserves topic keywords, multi-stakeholder targets, and comparative intent
  - `--intent next-questions` (or `nbq`): Next 3 best strategic questions anchored in decisions/risks
  - `--intent next-actions`: Next 3 best commitments with owners and deliverables
  - `--intent respond --target <person>`: Tactical engagement coaching tailored to specific attendee
  - `--intent graph-context`: Enterprise knowledge retrieved from active graph topology
  - `--intent gaps` (or `review`): Changes, contradictions, and promises across meetings (requires $\ge 2$ dated assertions)
  - `--intent connect`: Surfaces cross-SOW multi-hop relationship chains (workstream → SOW → deliverable → risk → stakeholder) ranked by flow state and salience
- **Fail-Closed Grounding**: If GraphDB is ungrounded or returns 0 assertions, copilot fails closed: `No graph grounding: no suggestion (reason: <STATUS>)`.
- **Format**: Return only the CLI's single fenced block of one to three grounded, speakable comments. Do not add a scratchpad, headings, citations, graph SHA, JSON, or prose around the fence. Keep assertion IDs and graph metadata in `<output_dir>/copilot_answers.jsonl` and the canonical SQLite WAL answer ledger (`dual_pipeline/answer_ledger.py`); use `--json` only when machine diagnostics were requested.
- **Live-call route**: Invoke `nee copilot` once with the known active session or meeting directory. Do not run help, source searches, SQL, or repeated exploratory commands before answering. If the CLI returns a bounded failure, relay its fenced status immediately.
- **Two-Layer Context Model**: Dynamic exponential decay-weighted focus ($\tau=300\text{s}$) and whole-meeting conversational flow tracking ($O(1)$ amortized per turn via `ConversationContextModel`).
- **Two-Tier Grounding & Vectorized BLAS**:
  - *Fast Tier*: $p95 < 1\text{ms}$ low-latency topological retrieval.
  - *Verified Tier*: Latency $\le$ `copilot.rigor.budget_ms` (15ms default) with asynchronous speculative pre-encoding, vectorized BLAS dot-product candidate reranking, sentence-level citation validation, and anti-hallucination fail-closed enforcement.
- **Governed Answer Ledger & Learning Loop**:
  - All copilot responses persist to `dual_pipeline/answer_ledger.py` (`pipeline="copilot"`).
  - Operator feedback via `nee answer-feedback` and offline evaluations via `nee answer-eval`.
  - Closed-loop parameter adaptations (`copilot_context`, `copilot_grounding`) synthesized via `tools/govern_learning_proposals.py answer-scan`, simulated under guard metrics, and committed via Universal Write Gateway ($+0$ graph mutation).
- **Ear-First Spoken Voice Standard (Google / Anthropic Best Practices)**:
  - *Write for the Ear, Not the Eye*: Responses are unbounded by default so analytical and comparative questions receive complete graph-grounded detail; pass `--words <N>` only when an explicit word budget is requested.
  - *Conversational Contractions & Active Plural Voice*: Mandatory contractions (`we're`, `it's`, `don't`, `let's`) and collaborative phrasing (`we lock in`, `our golden set`).
  - *Spoken Numbers & Symbols*: Write numbers as spoken (`eighteen hundred`, `thirty to ninety percent`). Zero math symbols (`>=`, `!=`) or file extensions (`.json`).
  - *Zero Preambles & Fillers*: Never prepend `"Note that"`, `"Our records show"`, or `"What is the gap here?"`.
  - *Zero Written-Essay Structures*: No bulleted lists, lecture enumerations (`First... Second...`), em-dash clauses, or generic filler without concrete workflow anchors.
- **Few-Shot Spoken Calibration Examples**:
  - *Serial Number Accuracy*:
    - BAD (Written Essay — 159 words, 3 bullets): `- Since manufacturer and model are already working well...`
    - GOOD (Ear-First Spoken — 31 words, 2 sentences, $\le 35$ words): `"Let's lock in the manufacturer and model first, which jumps our extraction accuracy from thirty to ninety percent. For serials, we should require strict multi-model agreement so bad asset data never reaches Maximo."`
  - *1,800-Image Scale Intuition*:
    - BAD (Written Essay — 179 words, 4 bullets): `- Ed, your intuition on the 1,800 images is dead on...`
    - GOOD (Ear-First Spoken — 34 words, 2 sentences, $\le 35$ words): `"Ed, your intuition on the eighteen hundred images is spot on. Repetitive field volume dominates the real batch where our active rules clear ninety percent, while the novel plates get cleanly routed to human review."`

### Fast Recording Initialization & SSOT Architecture (< 5.0s)

Live audio recording initialization latency must remain strictly $< 5.0$ seconds from invocation to recording confirmation, with the first PCM byte spooled in $\le 0.5$s.

- **Pre-CLI Fast Bootstrap**: Intercepts `record-meeting` / `record` commands before Click/CLI imports, spawning eager audio capture spools in $< 500\text{ms}$ with zero heavyweight dependencies.
- **Single Capture Authority**: Exactly one capture process (`LiveAudioCaptureManager`) is provisioned per session, adopting running spooler handles without process respawning or orphan chunk fragmentation.
- **Capture-Strategy Profile Cache**: Caches hardware and device decisions keyed by topology fingerprint (`AudioStrategyProfileCache`), resolving optimal capture topology in $< 10\text{ms}$.
- **Background Acoustic Diarization (F10)**: Heavy acoustic diarization models are removed from the critical startup path. A lightweight preflight check ($< 50\text{ms}$) validates tokens and snapshots, while full Pyannote loading runs in a background daemon thread started *after* recording is confirmed.
- **Advisory Slug Mutex**: Parallel collisions on the same meeting slug fail closed in $< 20\text{ms}$ using non-blocking OS advisory file locks (`.session_locks/<clean_slug>.flock`).
- **Global Worktree Provisioning Lock**: Concurrent git worktree creation is strictly serialized through `.git/nee_worktree_provision.flock` to prevent git index corruption.
- **Governed Session Registry & Safe Orphan Reaping**: Active sessions register atomically in `.session_registry/<uuid>.json` with real-time heartbeats. Orphaned processes are reaped in $< 300\text{ms}$ using 4-gate qualification with zero foreign process fratricide.
- **Master WAV Batch SSOT**: Master WAV stitched from rolling chunks and processed via batch MLX transcription is the authoritative Single Source of Truth for Output 01 (Decision D1). Live chunk ASR provides non-authoritative operator preview (`LIVE_PREVIEW` tag) without blocking final synthesis.
- **Live Speaker Identification & Operator Hints**: Real-time pyannote voice clustering (`LiveVoiceRegistry`) fused with operator hints (`nee speaker-hint`), with strict precedence: `Operator Hint > Pyannote Clustering > Default Fallback`. Speaker hints (`Speaker - "..."`, `Speaker "..."`) ingest silently into a durable persistent queue (`speaker_hints_queue.json`) with zero conversational chat replies or markdown tables, reconciling asynchronously against transcribed voice clusters once segments are available.
- **Autonomous Collateral Drop Watcher**: `CollateralFsWatcher` monitors `referenced_artifacts/` throughout every meeting, logging detected assets to `collateral_drop_log.json` for multi-modal evidence correlation.
- **Sample-Accurate Timeline Accounting**: Audio timelines are computed directly from WAV frame headers ($nframes / framerate$) with cumulative tracking to eliminate hardware clock drift across segments.

### Dual-Proof State Machine & Governed Exit Codes

The recording lifecycle requires dual proof-of-capture and proof-of-readiness before proceeding to transcription:

```
[CLI / Bootstrap]
        │
   ┌────┴───────────────────────────────┐
   ▼                                    ▼
[Audio Proof-of-Capture]     [MLX Proof-of-Readiness]
   ├─ P0: Process Alive         ├─ 1. Airgap Socket Isolation
   ├─ P1: Byte Rate ≥ 80%       ├─ 2. Metal GPU Detection
   ├─ P2: Mic Not Digital-Zero  ├─ 3. Local Model Resolution
   └─ P3: Continuous Growth     └─ 4. Golden Inference Verification
   │                            │
   ├─ Fails ──► EXIT 11         ├─ Fails ──► EXIT 12
   ▼                            ▼
┌───────────────────────────────────────┐
│     RECORDING CONFIRMED (Dual Pass)   │
└───────────────────────────────────────┘
```

#### Governed Exit Codes
- **`EXIT_AUDIO_UNPROVEN = 11`**: Raised when audio capture readiness or continuous proof fails (`AudioCaptureUnprovenError`). Fails fast closed, writes `.quarantine` marker with reason code, releases slug mutex, and exits.
- **`EXIT_MLX_UNREADY = 12`**: Raised when MLX Whisper worker initialization, Metal GPU verification, or golden inference probe fails (`MLXInitError`, `MLXRuntimeLost`, or `MLX_READY_DEADLINE_EXCEEDED`). Fails fast closed, writes `.quarantine` marker, releases slug mutex, and exits.

#### Reason Codes (Closed Enums)
- **`AudioReasonCode`**:
  `FFMPEG_SPAWN_FAILED`, `FFMPEG_EARLY_EXIT`, `CAPTURE_STDERR_DENIED`, `PCM_BYTE_RATE_DEFICIT`, `MIC_DIGITAL_ZERO`, `AUDIO_GROWTH_STALLED`, `SCK_TAP_FAILED`, `SCK_TCC_DENIED`, `DEVICE_NOT_FOUND`
- **`MLXReasonCode`**:
  `MLX_IMPORT_FAILED`, `METAL_UNAVAILABLE`, `MODEL_NOT_CACHED`, `MODEL_REVISION_MISMATCH`, `MODEL_LOAD_FAILED`, `GOLDEN_INFERENCE_EMPTY`, `GOLDEN_INFERENCE_MISMATCH`, `MLX_READY_DEADLINE_EXCEEDED`, `MLX_WORKER_DIED`, `MLX_RUNTIME_LOST`

#### Zero-Fallback & Egress Invariants (Operator Decision D1)
1. **Operator Decision D1 (Mixed SCK by Default)**: Default capture strategy is fixed to `mixed_sck` (ScreenCaptureKit system sound + microphone with stereo isolation). Both sources must pass proof at startup.
2. **Strict Zero Fallback to Microphone**: If ScreenCaptureKit fails (permissions or process failure), the recorder **refuses to start** and outputs prescriptive remediation. Silent degradation to microphone is strictly prohibited.
3. **Strict Zero Cloud Egress**: Only offline Apple Silicon Metal GPU transcription via `mlx_whisper` is permitted. Cloud backends (`--backend openai`, cloud endpoints) are strictly prohibited and fail closed at validation. Worker processes operate under strict `socket.socket` airgap enforcement.

## Canonical Pipeline Execution: Direct Stage U0 Ingestion (`nee ingest-meeting`)

> [!IMPORTANT]
> **Master Invariant #10 Mandate**: Meeting transcripts, audio recordings, WebVTT files, and CoPilot exports MUST be ingested directly into Stage U0 using `nee ingest-meeting` (or programmatic `RawTranscriptPipeline.run()`).
> **Never author, modify, or run one-off ingestion scripts (`scripts/build_and_ingest_*.py`).** All meeting metadata must be provided directly via CLI flags or auto-scaffolded.

> [!IMPORTANT]
> **Master Invariant #14 Mandate: Worktree Isolation for Splicing & 3-Output Synthesis**:
> Splicing live meeting audio, processing raw transcript chunks, and synthesizing Deliverables 01, 02, and 03 MUST execute inside an isolated Git worktree branch (`.worktrees/live_<slug>/` on `feat/live-<slug>`).
> - `nee ingest-meeting` automatically provisions `.worktrees/live_<slug>/` and routes outputs inside it when invoked from the repository root (`main`).
> - When running wave-based agent orchestration, agents MUST execute in an isolated worktree branch before writing any artifacts under `output/`. Never synthesize or write Deliverables 01–03 directly on `main`.

Run the unified meeting ingestion CLI (or `./scripts/ingest_meeting.sh`):

```bash
nee ingest-meeting \
  --input "${INPUT}" \
  --project "${PROJECT}" \
  --meeting "${TITLE}" \
  --date "${DATE}" \
  ${ATTENDEES:+--attendees "${ATTENDEES}"}
```

Or execute directly via the governed wrapper script:
```bash
./scripts/ingest_meeting.sh \
  --input "${INPUT}" \
  --project "${PROJECT}" \
  --meeting "${TITLE}" \
  --date "${DATE}" \
  ${ATTENDEES:+--attendees "${ATTENDEES}"}
```

- **Metadata Auto-Scaffolding**: If `--metadata` is omitted, `scaffold_meeting_metadata` automatically infers dates, meeting names, project directories, and attendee rosters from the CLI flags or the input filename/content.
- **Output Hierarchy**: Automatically written to `output/<project_name>/<MM-DD-YYYY>/<standardized_slug>/`. Manual folder setup or post-run moves are forbidden.
- **Automated Publication**: By default, the pipeline validates all QA gates, runs LLM Judge evaluations, creates `intake_package.json`, checks hierarchy compliance, and publishes into GraphDB under the directive ID (with operator approval when required) and real BGE-M3 retrieval evaluations.
- **Dynamic Directive**: When `--directive-id` is omitted, it is automatically synthesized following the canonical formula.

### Path Routing


The pipeline automatically selects one of 3 paths at `U0` + `L1`:

1. **`AUDIO_TRANSCRIBED`**:
   - Transcribes audio offline via local Apple Silicon Metal GPU (`mlx_whisper` / [`audio_ingestion.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/audio_ingestion.py)). Cloud Whisper backends are strictly prohibited.
   - Supports: `.mp3`, `.wav`, `.m4a`, `.webm`, `.mp4`, `.flac`, `.ogg`.
   - Inspects diarization markers to route to native or undiarized sub-paths.
2. **`DIARIZED_TEXT`**:
   - Speaker labels are already present in input text.
   - Skips C0 roster gate and proceeds directly to L1 execution.
3. **`UNDIARIZED_TEXT`**:
   - Exclusively for text-only notes or unsegmented documents lacking speaker markers. Audio sources and multi-attendee rosters are strictly disqualified from undiarized processing.
   - Requires participant roster in metadata.
   - If roster is missing, C0 gate raises `HITLRequiredError` to obtain attendee names before attribution.

## The Three Required Artifacts

Filenames must strictly follow `<prefix>_<meeting_name>_<date>_<time>_<suffix>`:
- **Strict Date Rule**: Filename dates must use `MM-DD-YYYY` (e.g. `08-28-2026`). Internal headers and JSON properties must use `MM/DD/YYYY`.
- **Downloadable Links Only**: Never dump artifact text inline into conversation turns.

### 1. Artifact 01: Forensic Transcript
`01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md`
- Chronological forensic source of truth.
- Cleans ASR noise, stuttering, and cross-talk while strictly preserving meaning.
- Preserves timestamps and speaker attribution.
- Includes an Ambiguity Lock Register table for any low-confidence utterances.

### 2. Artifact 02: Executive Recap
`02_<meeting_name>_<date>_<time>_executive_recap.md`
- Email-ready executive synthesis for team distribution.
- Header: Email Subject, strictly `MM/DD/YYYY` Date, Start/End Time (ET), Attendees grouped by company (`PartnerAdvisory: [...]`, `NEA / Apex Global Enterprises: [...]`).
- **Meeting Objective**: 1–2 sentences defining the core business/technical purpose.
- Discussion points and key findings.
- **Next Steps & Actions Table**: Strictly actionable columns (`Action Item | Company | Owner | Target Date / Cadence | Transcript Locator`).
- Open Decisions / Blockers (material only).

### 3. Artifact 03: Canonical Signal Projection
`03_<meeting_name>_<date>_<time>_canonical_signal_projection.json`
- Source-bound graph delta conforming to `evidence-envelope-intake.schema.json`.
- Enforces closed enum kinds: `summary`, `decision`, `risk`, `dependency`, `technical`, `strategic`.
- W3 Temporal attributes: `observed_at` and `stale_after` (`MM/DD/YYYY`).
- Passthrough of classification and audience tags.

## Governed Output Directory Hierarchy

All synthesis outputs and downstream receipts must be saved in the `output/` directory organized strictly by project, then by meeting date:

```
output/
└── <project_name>/
    └── <meeting_date>/
        ├── 01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md
        ├── 02_<meeting_name>_<date>_<time>_executive_recap.md
        ├── 03_<meeting_name>_<date>_<time>_canonical_signal_projection.json
        ├── qa_gates_report.json
        ├── llm_judge_evaluation.json
        ├── final_release_decision.json
        └── <project_name>_intake_package.json
```

- **Project Grouping**: All meetings associated with a project must reside under that project's canonical folder. In particular, `nexus_reid_nea` shares the same parent directory as `project_nexus` (`output/project_nexus/<meeting_date>/`).
- **Meeting Date Subdirectory**: Subdirectories must be named strictly by meeting date in `MM-DD-YYYY` format (e.g., `08-28-2026`, `09-10-2026`).
- **Meeting Scope**: Group by project, then meeting date, then all outputs in each meeting. All deliverables, evaluation reports, and package copies for a given meeting must reside directly in that meeting's date subdirectory. Flat directory structures directly under `output/` are forbidden.

## Deliverable Links in Completion Responses

Upon completion of meeting ingestion or transcript synthesis, the assistant completion response MUST present four clickable markdown file links (`file:///...`):
1. **Output 01 (Transcript)**: `01_<meeting>_<date>_<time>_hitl_resolved_transcript.md` (clickable absolute `file:///...` link)
2. **Output 02 (Executive Recap)**: `02_<meeting>_<date>_<time>_executive_recap.md` (clickable absolute `file:///...` link)
3. **Output 03 (Signal Projection)**: `03_<meeting>_<date>_<time>_canonical_signal_projection.json` (clickable absolute `file:///...` link)
4. **LLM Judge Evaluation**: `llm_judge_evaluation.json` (clickable absolute `file:///...` link, verdict, composite score)

These links are required execution results under `.agents/rules/engineering-output.md` and are exempt from the "file inventories" ban. The response must truthfully reflect package-only vs active graph publication state; never claim canonical graph publication when `--package-only` was used or mutation is pending.

## Master Constraints (C-01 to C-20)

| Constraint | Rule |
|------------|------|
| **C-01** | **Source Binding**: Every entity, decision, and risk must reference transcript locators (`[HH:MM:SS]`). |
| **C-02** | **Closed Enum Kind**: Top-level statements must use only the 6 valid schema kinds. |
| **C-03 / C-04** | **Entity Spine**: Valid prefixes (`person:`, `org:`, `system:`, `workstream:`, `project:`, `metric:`, `role:`, `policy:`) matching W2 Semantic Spine. |
| **C-05** | **Canonical Predicates**: Relationship signals must use W2 registry predicates. |
| **C-07** | **W3 Temporal Hygiene**: `observed_at` and `stale_after` are mandatory for decisions and risks. |
| **C-08** | **Classification Passthrough**: Ingestion classification must flow from meeting metadata to output JSON. |
| **C-12** | **Anti-Hallucination Guard**: Never invent attendees, deadlines, or decisions not grounded in transcript. |
| **C-14 / C-15** | **HITL Ambiguity**: Surface HITL only if confidence margin $\Delta \le 20\%$; otherwise resolve autonomously. |
| **C-16** | **Prior Context Protocol**: Query active graph before synthesis; use `CONFIRM` for known entities, `CREATE` only for new ones. |
| **C-17** | **Hard Evidence Ceiling**: `evidence_only_statement_ids` $\le \max(3, 2 \times \text{promoted\_mutation\_count})$. |
| **C-18** | **Idempotent Signal IDs**: Hash of `(from_id, to_id, relationship)` to prevent duplicate edges across meetings. |
| **C-19** | **Career Leverage Firewall**: Zero personal advancement or compensation leaks in team deliverables. |
| **C-20** | **Anti-Pattern Firewall**: Proposing undiarized ingestion or attribution stripping on audio sources or multi-attendee rosters is strictly prohibited and fails closed with `ANTI_PATTERN_VIOLATION`. |

## Negative Exclusion Filter

Do **NOT** promote the following into Artifact 03:
- Small talk, pleasantries, weather, scheduling logistics.
- Rhetorical questions or brainstorming musings not agreed upon.
- Unassigned or speculative actions.
- Repetitive conversational acknowledgments.

## Automated vs. Manual Ingestion Handoff

When running `python nee.py ingest-meeting`, packaging (`tools/prepare_evidence_envelope_intake.py`) and GraphDB publication (`_intake_publish`) are handled **automatically and atomically** within the pipeline run.

For diagnostic inspection or standalone Evidence Envelope preparation:
```bash
python tools/prepare_evidence_envelope_intake.py \
  --draft <output-dir>/03_canonical_signal_projection.json \
  --source-export <output-dir>/01_hitl_resolved_transcript.md \
  --source-location <source_path> \
  --output <output-dir>/intake_package.json
```

Then open and execute the ingestion directive:
```bash
python nee.py intake-directive-open --package <output-dir>/intake_package.json
```

> [!CAUTION]
> Under NO circumstances should an agent write a custom script to stitch together packaging and intake. Always use `nee ingest-meeting` or the governed CLI subcommands.


## Host & Hardware Qualification Protocol (Wave 6)

Before deploying or activating recording changes across production meeting targets, run the representative hardware canary outside of live meetings:

```bash
# Governed hardware canary runner (30 warm / 10 cold samples):
.venv/bin/python tools/qualify_hardware_canary.py --warm-samples 30 --cold-samples 10
```

### Canary Objectives & Evidence Boundaries
1. **Preceding Receipt Chaining**: Verifies that cryptographic receipts for Waves 1–5 are sealed and valid.
2. **Host Hardware Telemetry**: Inspects Apple Silicon (`arm64`), Darwin kernel, CoreAudio device HAL, ScreenCaptureKit authorization, and Metal inference acceleration.
3. **Distinct Evidence Tiers**: Evaluates isolated mock and hardware topologies separately; mock and hardware evidence must never be conflated.
4. **Model Comparison Matrix**: Replays identical tool invocations across Gemini Pro and Flash profiles to prove model-independent behavior:
   - Immediate dispatch latency $\le 250\text{ms}$.
   - Silent speaker hint intake into persistent durable queue (`speaker_hints_queue.json`).
   - Zero formal conversational chat replies or markdown tables for clear hints.
   - Clean capture process ownership retention without cross-session contamination.

### Canary Rollback & Ongoing Capture Continuity Runbook
- **Zero Meeting Disruption**: Canary execution MUST NEVER target an active live meeting or kill foreign processes.
- **Rollback Safety**: If canary qualification fails, newly requested automatic entry routes fail closed with a truthful status message. Ongoing live audio captures retain their owned process handles without termination or interruption.
- **Session-Scoped Recovery**: In the event of a local worker failure, recovery executes strictly through `adapter.recover_session(session_id)`, terminating only the session's owned child PIDs. Broad process kills (`pkill -f ffmpeg`) are strictly prohibited.

## Related Files

- Pipeline runner: [`transcript_pipeline.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_pipeline.py)
- Pipeline stages: [`transcript_stages.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_stages.py)
- Audio ingestion: [`audio_ingestion.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/audio_ingestion.py)
- Intake preparation: [`prepare_evidence_envelope_intake.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/prepare_evidence_envelope_intake.py)
- Prompt contract: [`RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md`](file:///Users/amitayer/Git/decision-intelligence-engine/config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md)
