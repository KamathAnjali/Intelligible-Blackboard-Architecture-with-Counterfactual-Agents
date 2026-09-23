# Project Progress Report — 23 September 2026

## Project status

The repository contains substantial component work, but the complete product is not yet verified end to end. Blackboard foundations, PXP validation, UI scaffolding, benchmark utilities, and a local Ollama adapter exist. They are distributed across separate branches and are not yet connected into one reproducible execution path.

**Overall assessment:** 🟠 Partially implemented; integration and counterfactual execution remain the primary blockers.

Audit basis:

- Branches inspected: `main`, `1-Anjali`, `2-Lopez`, `3-Dhruva`, and `4-Tanisha`.
- Current audit branch: `4-Tanisha`.
- Python verification on the audit branch: `10 passed in 0.27s` using `.venv/bin/pytest`.
- No files were modified during the audit itself.
- Live Ollama, FastAPI/WebSocket, and frontend build execution were not independently verified.

## Executive summary

### Implemented

- Pydantic Blackboard models and PXP tags.
- Thread-protected Blackboard state.
- In-memory storage and JSON snapshot helpers.
- Basic consensus and deadlock heuristics.
- Round-robin scheduler.
- Strict PEX/PXP response validation and retry handling.
- Ollama client and persona prompt files.
- FastAPI/WebSocket UI server scaffolding.
- React/D3 graph, history scrubber, split timeline, and token display.
- KramaBench parser, token utilities, plotting, and trial-runner scaffolding.

### Not yet proven as an integrated system

- Real scheduler → agent → Blackboard execution.
- Live UI connected to the active session Blackboard.
- Real LLM benchmark execution.
- Counterfactual rollback and replay.
- Reproducible ablation evidence.
- MSCoRe and MedAgentBench execution.

The largest risk is that Student 4’s branch contains UI and benchmark code but does not contain Student 3’s `agents/` package or the scheduler. Student 3’s LLM work is therefore not integrated into the UI/benchmark branch or current `main`.

## Team contribution map

| Branch | Owner | Main responsibility | Implemented | Partial/missing | Dependencies | Integration status |
|---|---|---|---|---|---|---|
| `1-Anjali` | Anjali | Blackboard models, core, storage | Models, validation, snapshots, tests | Stronger semantics and event API | Pydantic | Foundation complete |
| `2-Lopez` | Lopez | Protocol and scheduler | Round-robin selection and terminal handling | Registration sync, turn enforcement, event publishing | Blackboard | Scheduler exists; not fully integrated |
| `3-Dhruva` | Dhruva | Agents, prompts, local Ollama, PXP mapping | Strict PEX/PXP validation, retries, `BoardEntry` mapping | Complete multi-agent runner | Blackboard, scheduler, Ollama | Good isolated component; not in main |
| `4-Tanisha` | Tanisha | UI, WebSocket, ingestion, metrics | UI, mock/replay stream, Krama parser, token utilities, synthetic runner | Real agent/scheduler connection and real benchmark execution | Blackboard, scheduler, agents | Large disconnected workstream |

Ownership is inferred from branch names and commit authors.

## Component status

| Component | Status | Evidence | Next action |
|---|---|---|---|
| Blackboard models | 🟢 Implemented + verified | `blackboard/models.py`, tests | Freeze the shared contract |
| Blackboard validation | 🟢 Implemented + verified | `blackboard/core.py`, `test_blackboard.py` | Validate prediction and participant agreement |
| Persistence | 🟡 Implemented but partially verified | `blackboard/store.py` | Add integrated save/reload evidence |
| Scheduler | 🟠 Partially implemented | `blackboard/scheduler.py` | Synchronize registration and enforce turns |
| PXP validation | 🟢 Offline verified | Student 3 `agents/pex.py`, `agents/pxp.py`, tests | Merge and run through scheduler |
| Ollama adapter | 🟡 Implemented but live-unverified | Student 3 `agents/llm_client.py` | Pin one model and connect a session runner |
| Counterfactual engine | 🔴 Missing | No rollback/replay implementation found | Implement isolated replay and scoring |
| Event bus/WebSocket | 🟡 Implemented but integration-unverified | `ui/server/` | Connect to the active session board |
| React/D3 UI | 🟡 Implemented but real-data-unverified | `ui/frontend/` | Resolve schema mismatch and build-test |
| Token accounting | 🟠 Partial | `bench/metrics/token_counter.py` | Use actual model usage, not heuristics |
| KramaBench | 🟡 Parser exists | `bench/ingest/krama_parser.py` | Execute parsed tasks through real agents |
| Trial runner | 🟠 Synthetic | `bench/metrics/trial_runner.py` | Replace hard-coded entries with real calls |
| MSCoRe | 🔴 Missing | No adapter found | Implement or formally scope out |
| MedAgentBench | 🔴 Missing | No adapter/FHIR environment found | Define feasibility and adapter |

## Milestone status

### Milestone 1 — Blackboard and scheduler

**Status: 🟠 Partially complete.**

The Blackboard models, validation, storage, simple consensus, and deadlock tests exist. The scheduler provides round-robin selection and terminal status handling. However, it does not register agents with the Blackboard and does not verify that a submitted entry belongs to the agent selected by `next_agent()`. The demo posts entries manually and is not a complete scheduler-driven session.

Remaining:

- Synchronize scheduler and Blackboard registration.
- Enforce turn ownership.
- Add a scheduler-driven RATIFY/REJECT integration test.
- Define maximum-turn/time-limit behavior.
- Strengthen consensus so it checks agreement rather than only recent RATIFY tags.

### Milestone 2 — Local models, prompts, and WebSocket

**Status: 🟡 Components implemented; end-to-end behavior unverified.**

Student 3 has a local Ollama adapter, persona prompts, strict structured output validation, retries, and BoardEntry mapping. Student 4 has a FastAPI/WebSocket server and React frontend. These are not connected in one branch or one runtime path. The README describes 7B candidate models, while Student 3 uses `qwen3:4b-instruct-2507-q4_K_M` and other files use `mistral-7b-instruct`.

Remaining:

- Agree on one exact model and configuration.
- Add the complete two-agent runner.
- Connect the active Blackboard to the WebSocket event stream.
- Run a real local model session and preserve its evidence.

### Milestone 3 — Counterfactual simulation

**Status: 🔴 Missing as a real feature.**

The schema includes `CounterfactualResult` and `is_counterfactual_sim`, and the UI can display simulated-looking nodes. No engine was found that copies a historical state, changes an earlier entry, reruns downstream agents, scores the alternative, or applies/rejects it in the live session.

### Milestone 4 — Benchmarks and evaluation

**Status: 🟠 Synthetic/partial.**

KramaBench parsing, token utilities, plotting, and a trial runner exist. The trial runner directly constructs entries and uses hard-coded recovery behavior; it does not invoke the real Ollama agents, scheduler, or counterfactual engine. No MSCoRe or MedAgentBench adapter was found. Claimed pilot CSVs/charts are not present as auditable tracked result artifacts.

## Critical issues

### P0 — No integrated execution branch

`main` does not contain the agent implementation or Student 4 UI. Student 4’s branch does not contain Student 3’s `agents/` package or the scheduler. No single branch currently provides a verified complete system.

### P0 — Counterfactual engine is absent

There is no implementation for historical cutoff, isolated snapshot, alternate execution, delta scoring, or live adoption. This blocks the project’s main research contribution.

### P1 — Benchmark runner is synthetic

`bench/metrics/trial_runner.py` creates `BoardEntry` objects directly. Its outputs cannot be treated as real LLM or agent performance measurements.

### P1 — PXP vocabulary is inconsistent

The authoritative Python enum defines `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`. UI mock data, recorded sessions, and documentation also use `PROPOSE`. Student 3’s initial-response schema requires `REVISE`.

Affected locations include:

- `blackboard/models.py`
- `ui/server/mock_stream.py`
- `bench/data/recorded_session.json`
- `ui/frontend/src/constants/tagColors.ts`
- `docs/demo_script.md`

The team must decide whether `PROPOSE` is a real protocol tag or a UI-only label.

### P1 — UI is not connected to the active board

`ui/server/board_tap.py` creates its own `ui_demo_session` Blackboard and polls it. It does not receive the Blackboard used by the scheduler or agent runner.

### P1 — Consensus and deadlock rules are heuristic

Consensus is based mainly on the latest entries being RATIFY entries. It does not verify matching predictions, distinct agent participation, explanation agreement, or target-chain coherence. Deadlock detection treats any four consecutive negative tags as a deadlock regardless of deeper protocol context.

### P2 — Environment setup is not unified

The root `requirements.txt` contains only Pydantic and pytest. FastAPI dependencies are in `ui/server/requirements.txt`, and frontend dependencies are in `ui/frontend/package-lock.json`. There is no `.env.example`, `pyproject.toml`, or unified setup lockfile.

### P2 — Documentation claims are not fully reproducible

Several documents claim full-pipeline runs, pilot results, charts, and rehearsals, but the generated result files are absent from the tracked repository. These claims are **UNVERIFIED — evidence not found**.

## Cross-branch inconsistencies

| Location | Expected behavior | Actual behavior | Impact | Severity |
|---|---|---|---|---|
| Python schema vs UI/mock data | One PXP vocabulary | `PROPOSE` exists only in UI/mock paths | Replay/schema validation fails | P1 |
| Scheduler | Selected agent submits next | Any board-registered agent can submit | Turn order is unenforced | P1 |
| UI board tap | Read active session | Creates separate demo board | UI may show unrelated data | P0 |
| Trial runner | Execute real agents | Constructs synthetic entries | Results are not model evidence | P1 |
| Model configuration | One pinned model | Qwen, Mistral, and README candidates differ | Reproducibility failure | P2 |
| Student 4 branch | Include integrated agents/scheduler | Contains neither Student 3 agents nor scheduler | Branch integration missing | P0 |
| Runtime dependencies | One reproducible install | Python and UI requirements are split | Server import can fail | P2 |

## Missing interfaces to agree on

1. **Session ownership:** one shared session must expose Blackboard, Scheduler, agents, event bus, and task metadata.
2. **Agent interface:** define the exact task/state/agent input and `BoardEntry` output contract.
3. **Scheduler interface:** define registration ownership, turn enforcement, terminal outcomes, and event publication.
4. **Event schema:** define events for entries, consensus, deadlock, counterfactual start/result, summaries, and stream completion.
5. **Snapshot/replay API:** define historical cutoff, modified entry, downstream replay, score, and adoption.
6. **Benchmark API:** require `TaskFormat -> Session -> Scheduler -> Agent -> Blackboard -> Metrics` rather than synthetic direct entry creation.

## Testing status

### Verified

The checked-out Student 4 branch passes 10 tests covering:

- Blackboard validation;
- unknown agents and targets;
- simple consensus and deadlock;
- token counting and tallying;
- KramaBench parsing;
- synthetic live-board feeding.

Student 3’s branch contains additional offline tests for malformed model output, retries, PXP tags, metadata isolation, BoardEntry mapping, and scheduler submission. Its documented 61-test result was not independently rerun from the current checkout.

### Missing or unverified

- Complete scheduler-agent-board loop
- Wrong-turn submission rejection
- Real WebSocket session
- UI replay of validated BlackboardState
- Counterfactual isolation and replay
- Real Ollama benchmark execution
- MSCoRe and MedAgentBench
- Concurrent scheduler writes
- Reconnection and initial-state synchronization
- Actual model-token accounting
- Frontend production build

## Repository hygiene

### Good

`.gitignore` excludes virtual environments, Python caches, pytest caches, `.env`, model caches, logs, generated results, `node_modules`, build output, and raw benchmark data. No tracked API keys or obvious secrets were found.

### Issues

- `.env.example` is referenced by README but does not exist.
- UI dependencies are not included in root installation instructions.
- No unified Python lock/configuration file exists.
- Documentation includes machine-specific `file:///Users/tanishasinghal/...` paths.
- Generated benchmark artifacts are absent, preventing result auditing.
- `bench/data/recorded_session.json` uses `PROPOSE`, which is invalid under the current Python enum.

## Immediate next steps

1. Create one integration branch from current `main`.
2. Add Student 3’s agent package and Student 4’s UI/benchmark package.
3. Freeze the PXP vocabulary and update every producer/consumer consistently.
4. Pin one Ollama model identifier and update all setup/configuration files.
5. Create a shared session object containing Blackboard, Scheduler, agents, and event bus.
6. Synchronize scheduler registration with Blackboard registration.
7. Enforce that submissions come from the currently selected agent.
8. Add a mocked two-agent end-to-end test reaching consensus and saving a snapshot.
9. Connect the UI board tap to that active session instead of creating `ui_demo_session`.
10. Replace synthetic benchmark execution with real scheduler/agent calls.
11. Implement isolated counterfactual replay before claiming Milestone 3 completion.
12. Run real Ollama and UI verification, preserve logs/results, and only then run the pilot evaluation.

## Final audit conclusion

The project has a credible foundation and several useful independent components. It is not yet a verified multi-agent Blackboard system with counterfactual reasoning. The immediate priority is integration and protocol agreement, followed by implementation of the actual counterfactual engine. Benchmark and final-result claims should remain labelled **UNVERIFIED** until they are reproducible from a single integrated branch.
