---
trigger: glob
globs: "{dual_pipeline/audio_*.py,dual_pipeline/live_meeting_recorder*.py}"
---

# Streaming Lifecycle & Persistence Resilience Rules

Governs streaming protocols, WebSocket state machines, and atomic persistence. Grounded in Invariant 3.

## 1. Dual-State WebSocket Protocol & DOM Locking
Real-time interfaces implement a two-phase state machine:
`[Interim Segment (is_final=False)] -> [Finalization Event] -> [Locked Segment (is_final=True)]`
- **Monotonic Segment Identifiers**: Strictly sequential identifiers (`seg_001`, `seg_002`, ...).
- **Post-Finalization Immutability**: Once broadcast with `is_final=True`, no subsequent message may mutate or re-open that `segment_id`.
- **Single Dispatch SSOT**: Authoritative dispatcher emits messages; callback hooks must not double-dispatch.
- **Latency HUD Metrics**: Envelopes carry `latency_ms` measuring capture to broadcast.

## 2. Atomic Rolling File Persistence & Crash Recovery
File persistence operations (`AtomicRollingFileWriter`) guarantee crash resilience:
1. **Three-Phase Atomic Swap**:
   - Write bytes to isolated `.tmp` file.
   - Flush internal buffers to physical storage via `os.fsync(f.fileno())`.
   - Atomic rename via `os.replace(tmp_path, target_path)`.
2. **Startup Session Resumption**: Inspect target directory on initialization, resume from highest segment ID without overwriting prior transcripts.
3. **Dangling File Cleanup**: Detect, sweep, and remove orphaned `.tmp` artifacts on startup.
