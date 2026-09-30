<<<<<<< HEAD
# Project Progress Report

**Updated:** September 2026  
**Workstream Focus:** Student 1 - Data Model & Storage (`1-Anjali`)  
**Basis:** Latest integrated branch state (`1-Anjali`)

---

## 1. Executive summary

The Data Model and Storage layer (`1-Anjali`) has completed its **Week 1 foundation** and **Week 2 core deliverables**. The repository now features thread-safe Blackboard state management with reentrant locking, strict PXP grammatical validation, event emitter hooks for real-time WebSocket consumption, refined unanimous intelligibility classification (differentiating Strong vs Ultra-Strong and preventing single-agent impersonation), enhanced snapshot persistence with replay trace export, and an isolated sandbox forking mechanism supporting Student 3's counterfactual rollback contract.

The automated test suite contains **34 passing tests** (`.venv/bin/pytest`) verifying schema validation, multi-agent consensus, deadlock detection, event dispatch, thread-safe high-concurrency writes, and sandbox isolation.

---

## 2. Team contribution map

| Branch | Owner | Main responsibility | Implemented | Status |
|---|---|---|---|---|
| `1-Anjali` | Anjali | Blackboard models, core, storage, events, rollback contract | Models, validation, snapshots, event emitter hooks, refined consensus, rollback sandbox, tests | Week 1 & Week 2 complete (34 tests passing) |
| `2-Lopez` | Lopez | Protocol and scheduler | Round-robin turn selection, turn enforcement, registration synchronization, terminal status handling | Integrated with Blackboard |
| `3-Dhruva` | Dhruva | Agents, prompts, local Ollama, PXP mapping | Strict PEX/PXP validation, retries, `BoardEntry` mapping, counterfactual persona prompts | Ready for counterfactual sandbox integration |
| `4-Tanisha` | Tanisha | UI, WebSocket, ingestion, metrics | UI server, WebSocket scaffolding, React/D3 graph, token counter, KramaBench parser | Connected via BoardEvent emitter hooks |

---

## 3. Student 1 Deliverables Summary

### Week 1 Deliverables (Verified)

- **BlackboardState Schema Draft & Justification**: Defined in `docs/Schema_Draft.md` and `blackboard/models.py`, listing every field (`task_id`, `entries`, `agents`, `intelligibility`, `deadlocks`, `counterfactual_events`) with types. Includes the 3-sentence justification for the append-only chronological list structure.
- **Backing-Store ADR**: Documented in `architecture/backing-store.md` and `architecture/ADR-001-Storage-Architecture.md` (in-memory dict with JSON snapshots chosen; Redis deferred).
- **Python Package Skeleton**: Scaffolded in `blackboard/` (`__init__.py`, `models.py`, `store.py`, `core.py`, `scheduler.py`).
- **BoardEntry Pydantic Contract & Sample Payloads**: `BoardEntry` pydantic model with validation for non-empty fields, valid tags, and target IDs; 3 hand-written example payloads documented in `docs/Schema_Draft.md`.
- **PXP Grammatical Parser & Unit Tests**: Verified via `test_parser.py` and `test_models.py` (malformed input, missing fields, invalid types, and whitespace rejection).
- **Store Persistence & Snapshot Tests**: Verified in `test_store.py` (empty board, populated board round-trip, corrupted JSON error handling).
- **Public API Documentation**: Documented in `docs/API_USAGE.md` and docstrings in `blackboard/core.py`.
- **Multi-Agent RATIFY Integration Test**: Verified in `test_integration.py` driving agents through full consensus.

### Week 2 Deliverables (Implemented & Verified)

- **Board Event Emitter Hooks (`blackboard/core.py`)**:
  - Implemented `subscribe()`, `unsubscribe()`, and `_emit()` on `Blackboard`.
  - Emits real-time `BoardEvent` objects (`AGENT_REGISTERED`, `ENTRY_POSTED`, `DEADLOCK_DETECTED`, `INTELLIGIBILITY_CHANGED`, `SNAPSHOT_SAVED`, `ROLLBACK_FORKED`) for the WebSocket layer.
  - Listener exceptions are isolated so UI listeners cannot abort core state mutations.
  - Verified in `test_emitter.py`.
- **Refined Intelligibility Classification (`blackboard/core.py`)**:
  - Requires unanimous consensus across all registered active agents (minimum 2 agents).
  - Anti-impersonation: prevents repeated agreement posts from a single agent from faking consensus.
  - Enforces matching prediction values.
  - Tiers: `ULTRA_STRONG` (unanimous consensus reached through productive `REVISE` loops in history) vs `STRONG` (direct unanimous ratification).
  - Verified in `test_classification.py`.
- **Board-Snapshot Persistence & Replay (`blackboard/store.py`)**:
  - Tagged snapshot persistence to disk via `snapshot_to_disk(task_id, tag)`.
  - Added `list_snapshots()` and `export_replay_trace()` for UI history scrubbing.
- **Retrospective Rollback Data Contract (`docs/rollback-contract.md`)**:
  - Defined 5-pillar data contract in sync with Student 3.
  - Implemented `get_history_slice()` and `fork_simulation_blackboard()` in `Blackboard`.
  - Guarantees complete isolation between counterfactual simulation branches and the immutable live board history.
  - Verified in `test_rollback.py`.
- **High-Concurrency Thread Safety (`test_concurrency.py`)**:
  - Verified `threading.RLock` synchronization using 8 concurrent worker threads with barriers performing 160 simultaneous writes with zero lost entries.

---

## 4. How the System Works

### Architectural Flow

```text
Task Ingestion -> Scheduler selects agent turn (next_agent)
               -> Agent reads Blackboard history slice
               -> Local LLM generates structured PXP output
               -> Scheduler validates turn ownership & submits (submit_entry)
               -> Blackboard validates schema, agent registry, target links
               -> Blackboard updates state under RLock protection
               -> Intelligibility / Deadlock status recomputed
               -> BoardEvent deltas emitted to WebSocket listeners
               -> Snapshots persisted to disk for UI replay & debugging
```

### Component Interaction Details

1. **Turn Management & Submission**:
   The `Scheduler` queries active agents from `Blackboard.get_agents(active_only=True)` and enforces strict round-robin sequencing. Out-of-turn submissions are rejected with `ValueError`.
2. **Event Publication**:
   WebSocket listeners subscribe via `board.subscribe(callback)`. Any state mutation immediately emits a structured `BoardEvent` payload containing the task ID, timestamp, and JSON delta.
3. **Consensus & Intelligibility**:
   The classifier evaluates the latest entry from every active agent. If all active agents have submitted `RATIFY` with matching predictions, consensus is established. If `REVISE` was used earlier in the trace, the session is classified as `ULTRA_STRONG`; otherwise `STRONG`. A streak of 4 negative tags (`REFUTE`/`REJECT`) triggers a `DeadlockEvent` and sets status to `DEADLOCKED`.
4. **Counterfactual Forking**:
   When a deadlock occurs, Student 3 can call `board.fork_simulation_blackboard(cutoff_entry_id)`. This creates an isolated sandbox board populated with history up to the cutoff point, allowing replay without mutating live history.

---

## 5. Testing status

All 34 automated tests pass cleanly:

```text
============================= test session starts ==============================
platform darwin -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: pxp_blackboard
plugins: anyio-4.15.1
collected 34 items

test_blackboard.py .....                                                 [ 14%]
test_classification.py ...                                               [ 23%]
test_concurrency.py .                                                    [ 26%]
test_emitter.py ...                                                      [ 35%]
test_integration.py .                                                    [ 38%]
test_models.py .....                                                     [ 52%]
test_parser.py ....                                                      [ 64%]
test_rollback.py .                                                       [ 67%]
test_scheduler.py ........                                               [ 91%]
test_store.py ...                                                        [100%]

============================== 34 passed in 0.15s ==============================
```

---

## 6. Next Steps for Week 3

1. **Counterfactual Engine Integration**: Pair with Student 3 to run live LLM replays in the forked simulation blackboard.
2. **Deadlock Injection Harness**: Pair with Student 2 to automate deadlock injection and test counterfactual recovery rates.
3. **Ablation & Trial Results Logging**: Implement automated trial result exporters (CSV/JSON) for the 20-30 task pilot evaluation.
=======
# Progress report

Updated 30 September 2026 for `3-Dhruva`. This branch contains four personas, an explanation self-check, and runnable two- and three-agent local inference paths. The counterfactual mechanism and full product integration are still pending.

## Implemented

| Area | Current behavior |
| --- | --- |
| Shared contract | Pydantic models define agents, PXP-tagged BoardEntry records, target references, UTC timestamps, deadlocks, and session state. Empty predictions and explanations are rejected. |
| Blackboard and storage | Registered agents can post entries to a lock-protected, chronological board. The board validates agent and target IDs. An in-memory store supports JSON snapshots. |
| Scheduler | Round-robin turns select an agent and stop after the board reports agreement or four consecutive `REFUTE`/`REJECT` tags. The conversation runner also has a configurable turn limit. |
| Local inference | Ollama uses `qwen3:4b-instruct-2507-q4_K_M` with context 4096, temperature 0, seed 42, and a 256-token output cap. Raw responses and timing are available. |
| Structured output | PEX requires `prediction` and `explanation`; PXP adds a tag. Strict validation rejects malformed, missing, extra, or blank fields. Up to five retries can be configured, with two by default. The same budget covers rejected explanation reviews. |
| Personas and mapping | Aggressive proposer, cautious verifier, evidence auditor, and counterexample challenger have distinct prompts and share the PEX/PXP contracts. Application code supplies agent ID, target ID, entry ID, timestamp, and simulation flag when it builds a BoardEntry. |
| Explanation self-check | A separate call reviews the prediction and explanation against the original task. Unsupported explanations or malformed reviews trigger bounded regeneration before an entry is created. Every review, failure, and generation attempt is retained; usage includes both generation and review calls. |
| Live sessions | The runner supports two agents or a seeded three-agent disagreement case. Each model agent receives the task and current board history. Validated entries pass through the scheduler; transcripts and snapshots preserve partial runs. |
| Demo | `make demo` checks a live three-turn `REVISE`, `RATIFY`, `RATIFY` sequence, matching predictions, one valid attempt per turn, and no recorded failures. |

The storage design uses an append-only entry list for chronological replay and a simple in-memory JSON store for short local sessions. No database service is required.

## Evidence and prompt revision

- Five initial PEX sample tasks returned the expected predictions: arithmetic, deduction, insufficient evidence, contradiction, and a constrained selection. This was a smoke check, not a benchmark.
- All four personas passed 12 live checks covering deduction, insufficient evidence, and contradiction. Every candidate and explanation review passed on its first attempt, with the expected prediction. The prompts distinguish existential from universal claims and reserve RATIFY for supported predictions and reasoning.
- The self-check rejected both an unsupported universal claim and an irrelevant explanation attached to a correct prediction. Controlled checks verified correction feedback, bounded rejection, malformed-review handling, and preservation of the board and failure records.
- On 29 September 2026, the new challenger generated a self-checked BoardEntry through WSL, and `make demo RETRIES=0` completed `REVISE`, `RATIFY`, `RATIFY` with `yes`. The demo used six model calls: one generation and one review per turn. Its local record is `results/conversations/conversation-20260929T075505206911Z/transcript.json`.
- On 30 September 2026, a seeded false claim about ticket stamps produced three-agent disagreement. The first run reached the correct answer in five turns but the auditor mislabeled its correction `REFUTE`. After clarifying the `REVISE` rule, the repeated run followed `REVISE`, `REVISE`, `RATIFY`, `RATIFY`, `RATIFY` and ended at `C: insufficient information`. All four model turns passed their explanation checks on the first attempt, with no recorded failures. The local log is under `results/conversations/`.
- On the RX 6800S, a prior Windows Vulkan check reported the model fully on GPU and about 54 output tokens per second on a fixed prompt; the earlier CPU baseline was about 16. Placement and latency vary by machine and session.

Generated transcripts and inference reports are excluded from Git. They contain raw responses, explanation reviews, attempts, total call/token usage, timing, prompt settings, entries, and the final board state.

## Current limits

- The board's agreement status is based on recent RATIFY tags. It does not independently prove that distinct agents agree on the same reasoning or that a prediction is correct. The runner flags different RATIFY prediction text for review. The self-check can repeat the same model's mistakes; independent correctness and semantic evaluation remain required.
- The scheduler does not enforce that `submit_entry()` receives the agent selected by `next_agent()`. The current runner follows that order, but the shared interface needs turn ownership checks before broader integration.
- The current branch has no connected UI, benchmark execution pipeline, or counterfactual replay. Counterfactual fields in the data model are placeholders for planned behavior.
- The two-agent demonstration covers a simple deductive task. It does not establish recovery from disagreement, benchmark accuracy, or the proposed counterfactual benefit.

The next integration steps are additional three-agent sessions, enforceable turn and consensus rules, and isolated counterfactual replay before the planned evaluations. The [milestones](PROJECT_MILESTONES.md) describe those checkpoints.
>>>>>>> origin/3-Dhruva
