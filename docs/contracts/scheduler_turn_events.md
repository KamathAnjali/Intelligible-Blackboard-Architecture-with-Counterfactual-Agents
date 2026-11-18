# Scheduler turn event contract

## Status

The producer is implemented on Student 2's `2-Lopez` branch at commit `acd36b7` (`Add scheduler lifecycle events`). The Student 4 WebSocket bridge and frontend consumer are implemented in the current `4-Tanisha` worktree and were validated together with that commit in an isolated candidate worktree. The producer commit is not yet merged into `4-Tanisha`.

The events use the Blackboard event emitter. `session_id` and `task_id` are top-level fields; lifecycle fields are also top-level. Existing board events retain their `payload` envelope. The JSON event name is `type`; the producer also retains the enum in `event_type`.

## Event types

- `scheduler_status`: emitted when the scheduler starts and once on terminal state. `state` is `running`, `consensus`, `deadlock`, or `limit`. `agents` is a snapshot of `{agent_id, persona}` objects.
- `turn_started`: emitted when an agent is selected, before the runner invokes it. `turn` is 1-based. `reason` and `priority_score` are optional.
- `turn_finished`: emitted when the selected turn completes. It matches the `agent_id` and `turn` from `turn_started`; `outcome` is `posted`, `no_entry`, or `error`.

Example sequence:

```json
{"type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"running","agents":[{"agent_id":"proposer","persona":"proposer"}]}
{"type":"turn_started","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":3,"reason":"review","priority_score":0.8}
{"type":"turn_finished","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":3,"outcome":"posted"}
{"type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"deadlock","agents":[{"agent_id":"proposer","persona":"proposer"},{"agent_id":"verifier","persona":"verifier"}]}
```

`STRONG` and `ULTRA_STRONG` Blackboard outcomes map to `consensus`; `DEADLOCKED` maps to `deadlock`. A configured `max_turns` ends the scheduler with `limit` after the final selected turn completes if no Blackboard terminal outcome won. The runner calls `finish_turn("no_entry")` when an invocation returns no entry and `finish_turn("error")` when invocation fails. Successful `submit_entry()` calls emit `turn_finished` with `outcome="posted"`.

Subscribers that need the initial `scheduler_status` must subscribe to the Blackboard before constructing `Scheduler`; the UI attaches after creation in existing conversation paths, so it relies on session start metadata, `turn_started`, and the terminal status until that lifecycle order changes.

## UI behavior and validation

`board_tap` forwards the three event types over `/ws`, preserving the UI session ID and retaining the scheduler-generated ID as `scheduler_session_id` when those IDs differ. The frontend uses real events for active agent, turn, roster, and scheduler state. Board-entry-derived roster discovery is only a fallback before scheduler events arrive. When no lifecycle event arrives, the panel says `scheduler status: not provided by backend`.

The scheduler exposes terminal state but no target-specific rejection-streak count. The Canvas retains its same-target negative-entry heuristic, configurable with `VITE_REJECT_STREAK_THRESHOLD` (default `2`), and separately displays a scheduler-reported deadlock.

Validation: the real-Scheduler WebSocket tests cover `turn_started -> board_entry -> turn_finished` order and terminal `consensus`, `deadlock`, and `limit` in the isolated combination of Student 2 commit `acd36b7` and Student 4's current worktree. Browser rendering remains unverified.
