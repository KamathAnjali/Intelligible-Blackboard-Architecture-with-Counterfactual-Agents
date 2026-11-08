# Progress report

Updated 5 October 2026 for `integration-main` (with Student 1 Week 3 Storage & Recovery additions).

## Integrated sources

All four source branch tips are included through Git merge history. Existing source files, documentation, and data are retained.

| Branch | Included tip | Contribution |
| --- | --- | --- |
| `1-Anjali` | `cdf18c3` | Data contracts, synchronized board/storage, events, refined consensus, sandbox isolation, deadlock-breaking recovery verification, and trial-run metrics logging. |
| `2-Lopez` | `9554aae` | Event-driven scheduling, turn enforcement, and scheduler lifecycle. |
| `3-Dhruva` | `6d7b9d8` | Day 9 agent workflows and the conversation archive. |
| `4-Tanisha` | `bc777ba` | UI/backend, ingestion, metrics, and benchmark infrastructure. |

## Implemented behavior

| Area | Current implementation |
| --- | --- |
| Board and storage | Pydantic contracts, synchronized mutations, JSON snapshots, replay export, event subscriptions, and structured trial-run metrics logging (`TrialMetrics`). Consensus requires matching RATIFY predictions from every active agent; a four-entry negative streak flags deadlock; constructive revisions allow deadlock recovery. |
| Scheduler | Uses the board registry, reserves round-robin turns, rejects submissions from the wrong agent, and stops on terminal board status. Board events can wake the orchestration loop through `wait_for_next_agent()`; the current conversation runner still uses `next_agent()`. |
| Local inference | Shared `qwen3:4b-instruct-2507-q4_K_M` model, context 4096, temperature 0, seed 42, and a 768-token generation cap. |
| Structured agents | Four personas share strict PEX/PXP schemas. A separate model call checks explanation support; malformed or rejected outputs trigger bounded retries before entry creation. |
| Conversations | Two-agent sessions and the three-agent seeded disagreement case submit entries through the scheduler. Reports contain prompts, raw responses, reviews, failures, usage, outcomes, and board checkpoints. |
| UI/backend | FastAPI health and conversation-start endpoints, WebSocket board streaming, React/D3 graph layouts, node inspection, history scrubbing, and estimated entry-text token totals. LIVE_TAP attaches to the backend's in-process conversation board; replay and mock sources remain available. |
| Ingestion and metrics | JSON/JSONL parsing supports the upstream KramaBench record shape. Synthetic trial modes produce CSVs; plotting requires official evaluator provenance. |
| Sandbox foundations | History slices, isolated board forks, and deadlock-breaking recovery verification (`test_rollback.py`) are implemented. Sandbox branches remain strictly isolated while recovering deadlocked live sessions. |

## Published conversation records

Five transcripts and five usable snapshots are tracked under `results/transcripts/` and `results/snapshots/`. Three runs completed with a recorded scheduler-consensus outcome; two contain partial checkpoints and retain their original `running` outcome. Consensus labels do not independently establish answer correctness.

Snapshots for `conversation-20260929T144759406083Z` and `conversation-20260929T144943919595Z` were recovered from the board state embedded in their transcripts. The original zero-byte snapshot files are retained in `results/snapshots/originals/`. All other published records are unchanged copies of their local originals.

The Day 9 records show an initial auditor correction labeled REFUTE, followed by prompt tuning and a repeated five-turn sequence ending at `C: insufficient information`. The two completed disagreement runs retain their original generation and explanation-review evidence.

New conversation checkpoints, Ollama reports, benchmark outputs, and raw dataset clones remain ignored; publication is a separate archive operation.

## Integration verification and remaining work

Integration checks cover source ancestry, archive integrity, absence of unresolved conflict markers, and offline test execution. All core storage and isolation tests pass.

The UI's token totals are estimates over prediction/explanation text, while transcript usage includes model generation and review calls. The checked-in replay fixture is synthetic. No official KramaBench performance results have been produced: the current trial runner uses synthetic data and keeps empirical modes disabled.

Remaining work includes runtime integration verification, counterfactual replay/scoring/adoption, and a real benchmark agent/evaluator adapter. The [README](../README.md) contains setup and run commands; the [milestones](PROJECT_MILESTONES.md) retain the planned work.

## Methods: Data Model, Persistence, and Branching State

### 1. Chronological Append-Only State Model
The blackboard state is modeled as an immutable chronological sequence of typed messages (`BoardEntry`) rather than an indexed overwrite dictionary. Each message contains a unique identifier (`entry_id`), participating agent identity (`agent_id`), grammatical move (`tag` in `{RATIFY, REVISE, REFUTE, REJECT}`), hypothesis statement (`prediction`), reasoning rationale (`explanation`), and causal pointer (`target_entry_id`). This append-only design preserves the full historical derivation graph, enabling deterministic history scrubbing, graph edge linking in visual tooling, and unambiguous deadlock detection.

### 2. Thread-Safe Concurrency and In-Memory Mutations
Active reasoning turns operate over an in-memory dictionary store (`InMemoryJSONStore`) guarded by a reentrant synchronization lock (`threading.RLock`). All state modifications (agent registration, message submission, intelligibility evaluation, and snapshot creation) are serialized under lock acquisition. Observers, such as WebSocket event taps, subscribe via `Blackboard.subscribe()` and receive asynchronous `BoardEvent` deltas outside critical sections to prevent UI event loops from deadlocking or interrupting core blackboard operations.

### 3. Snapshot Persistence and Replay Trace Generation
For crash recovery, auditing, and visual scrubbers, in-memory states can be serialized to disk as structured JSON snapshots via `InMemoryJSONStore.snapshot_to_disk()`. Replay traces are generated through `export_replay_trace()`, constructing an incremental step-by-step reconstruction of the deliberative process up to any designated turn without mutating the underlying live state.

### 4. Branching Board State and Counterfactual Isolation
When an argumentative deadlock is detected (a consecutive window of 4 negative `REFUTE`/`REJECT` entries), counterfactual agents initiate alternative hypothesis testing without polluting the live audit trail:
- **History Slicing**: `Blackboard.get_history_slice(cutoff_entry_id)` retrieves an isolated sub-slice up to the cutoff, excluding all entries posted after the selected pivot.
- **Isolated Sandbox Forking**: `Blackboard.fork_simulation_blackboard()` spawns a detached `Blackboard` instance backed by its own in-memory store populated strictly with state up to the cutoff.
- **Simulation Execution**: Downstream turns execute inside the sandbox with `is_counterfactual_sim=True`.
- **Live State Protection**: The live blackboard remains completely unaffected during sandbox exploration. If an alternative hypothesis demonstrates consensus convergence, the agent promotes a new live entry (`PXPTag.REVISE`) referencing the revised rationale to resolve the live deadlock.

### 5. Structured Trial-Run Metrics Logging
Session-level performance and convergence metrics are captured via `TrialMetrics`, tracking task identity, convergence status (`is_converged`), terminal intelligibility level, total turn count, deadlock frequency, counterfactual attempt count, and elapsed time. Results are exported via `Blackboard.log_trial_run()` for downstream benchmark batching and comparative evaluation.
