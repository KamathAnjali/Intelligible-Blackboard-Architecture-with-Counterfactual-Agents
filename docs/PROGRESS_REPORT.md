# Project Progress and Week 1 Integration Report

**Updated:** 22 September 2026
**Basis:** latest visible local and `origin/*` branch state

## Executive summary

The project now has a working foundation: shared blackboard models, validation, persistence, consensus/deadlock handling, a scheduler, and validated PXP-to-board mapping. Student 2's scheduler is merged into `origin/main`, and Student 3's latest branch can convert validated model output into a real `BoardEntry`.

The remaining Week 1 gap is end-to-end evidence: one repeatable two-agent conversation where the scheduler selects turns, agents generate entries, the board records them, a terminal outcome is reached, and the session is saved/reloaded. No Student 4 UI/benchmarking branch is visible yet.

## Branch status

| Student | Branch | Latest work | Status |
|---|---|---|---|
| Student 1 — Anjali | `1-Anjali` | Blackboard foundation and storage | Week 1 foundation complete |
| Student 2 — Lopez | `2-Lopez` | Round-robin scheduler and terminal handling | Merged into `origin/main` at `0384777` |
| Student 3 — Dhruva | `3-Dhruva` | Ollama/PXP validation and `BoardEntry` mapping | Integration-ready; full runner required |
| Student 4 — UI/Benchmarking | No visible branch | No committed implementation found | Must be assigned and started |

The checked-out local `main` is at `2bc84c6`, while `origin/main` is at `0384777`. Refresh local main before integration testing, while preserving uncommitted work.

## How the branches connect

```text
Task -> scheduler selects agent -> agent reads board history
     -> Ollama produces PXP -> output maps to BoardEntry
     -> scheduler submits -> blackboard validates/stores
     -> consensus/deadlock status -> session saved/replayed in UI
```

Student 1 provides the shared contract and state. Student 2 controls turns and terminal status. Student 3 supplies validated agent output. Student 4 will display the resulting history and benchmark/session information.

## Student 1 — Data model and storage

**Work completed**

- `blackboard/models.py`: `PXPTag`, `AgentRecord`, `BoardEntry`, `BlackboardState`, deadlock/counterfactual records, and intelligibility levels.
- `blackboard/core.py`: thread-safe registration, entry validation, append-only history, consensus classification, and deadlock detection.
- `blackboard/store.py`: in-memory state and JSON snapshot save/load.
- Tests for malformed entries, unknown agents, invalid targets, consensus, deadlock, snapshots, and corrupted files.

**Connections**

Student 2 uses the board types and terminal status. Student 3 uses the same schema to validate tags, agent IDs, target IDs, and generated entries. Student 4 will read history/snapshots for the graph and replay view.

**Changes/improvements**

- Freeze `models.py` as the Week 1 shared contract and review later changes with all students.
- Define one source of truth for agent registration.
- Document the event-publication contract for the Week 2 dashboard.
- Keep simulated entries isolated from live history for Week 3.

## Student 2 — Protocol and scheduler

**Work completed**

`blackboard/scheduler.py` provides round-robin selection, no-agent and terminal checks, submission, deadlock transition, and stopping after `STRONG` or `ULTRA_STRONG` consensus.

**Connections**

The scheduler depends on Student 1's `Blackboard`, `AgentRecord`, `BoardEntry`, and intelligibility definitions. Student 3 hands generated entries to `Scheduler.submit_entry()`. Student 4 will display active agent, turn order, and terminal state.

**Changes/improvements**

- Make registration update both scheduler and board, or document the single owner.
- Track the result of `next_agent()` and reject submissions from the wrong agent.
- Add a multi-turn integration test covering consensus, deadlock, and stop.
- Define a maximum-turn/time-limit outcome.

## Student 3 — Agents and PXP mapping

**Work completed**

- Local Ollama client, model configuration, persona prompts, sample tasks, and latency reporting.
- Strict PEX/PXP validation in `agents/pex.py` and `agents/pxp.py`.
- `OllamaClient.generate_entry(...)` validates context, sends task/history to the model, retries invalid output, and maps it to `BoardEntry`.
- Application-owned fields (`agent_id`, target, entry ID/timestamp, simulation flag) stay outside model output.
- Offline and opt-in live tests cover valid output, bad tags, extra metadata, unknown agents/targets, retries, and scheduler submission.

**Connections**

Student 3 depends on Students 1 and 2. The adapter does not mutate the board; the caller submits its result during the scheduler-selected turn. Student 4 consumes the resulting entries and metadata.

**Changes/improvements**

- Build the complete loop: select agent -> generate -> submit -> repeat until terminal.
- Record retries, latency, token usage, and failure reasons.
- Verify turn ownership and termination using the real scheduler.
- Add a deterministic mock demo for CI and a separate opt-in live Ollama demo.
- Add context-budget handling; the current adapter sends full history and is intended for short Week 1 sessions.

## Student 4 — UI and benchmarking

**Current status:** no branch or committed implementation is visible.

**Required work:** branch from integrated main; create a minimal graph/replay view using actual board history; display agents, targets, tags, turns, and terminal status; add a documented benchmark sample/parser; and add a labelled token-counter stub.

**Connections:** the UI reads Student 1's snapshots/history, Student 2's scheduler state, and Student 3's generated entries. History inspection must be read-only and replayed sessions must be labelled as replay.

## Changes required before the Week 1 release

1. Refresh local main from the current integrated remote main.
2. Agree on registration, turn ownership, opening-tag, and terminal-state rules.
3. Add the mocked two-agent end-to-end runner.
4. Run one live Ollama session if the agreed model is available; save its log and JSON snapshot.
5. Add the minimal graph/replay view, or document replay output if UI work is not ready.
6. Run the full suite, record known issues, and tag the integrated Week 1 baseline.

## Expected end-of-Week-1 state

The repository should demonstrate:

- two registered local LLM agents with scheduler-controlled round-robin turns;
- validated PXP predictions/explanations stored as linked board entries;
- one consensus example and one controlled deadlock example;
- JSON snapshot save/reload;
- a graph or replay of the actual recorded interaction;
- a documented benchmark sample/parser result;
- test results, setup instructions, known issues, and a Week 1 release tag.

```text
schema/storage -> scheduler -> two agents -> PXP validation
              -> shared history -> consensus/deadlock outcome
              -> JSON snapshot + graph/replay evidence
```

Live streaming, accurate token accounting, three-agent execution, and counterfactual replay are Week 2/3 improvements, not Week 1 prerequisites.

## Next milestones

Week 2 should add board events, a live dashboard, three-agent sessions, real token totals, concurrency checks, history inspection, and benchmark execution. Week 3 should use Student 1 snapshots, Student 2 deadlock/scheduling controls, and Student 3 regeneration to implement isolated counterfactual replay.
