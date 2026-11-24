# Scheduler Turn Event Contract

Scheduler lifecycle events use the Blackboard event stream and are delivered to
the same subscribers as board events. Events emitted after the scheduler starts
include both `session_id` and `task_id`. A session ID identifies one scheduler
run; the caller may supply one when constructing `Scheduler`, otherwise the
scheduler generates a UUID. Board events emitted before scheduler creation have
no session ID because the run has not started yet.
Subscribers that need the initial `scheduler_status` event should subscribe to
the Blackboard before constructing the scheduler.

The event name is available as `type` and as the legacy `event_type` field.
Lifecycle fields are top-level in the JSON event. Existing board events continue
to use their `payload` object.

## Event types

- `scheduler_status`: emitted when the scheduler starts, reaches a terminal
  state, or resumes after the board reopens. `state` is `running`, `consensus`,
  `deadlock`, or `limit`.
  `agents` is a snapshot of `{agent_id, persona}` objects.
- `turn_started`: emitted when an agent is selected, before the runner invokes
  it. `turn` is 1-based. `reason` and `priority_score` are optional.
- `turn_finished`: emitted when the selected turn completes. It matches the
  `agent_id` and `turn` from `turn_started`; `outcome` is `posted`, `no_entry`,
  or `error`.

Example sequence:

```json
{"type":"scheduler_status","event_type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"running","agents":[{"agent_id":"proposer","persona":"proposer"}]}
{"type":"turn_started","event_type":"turn_started","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":3,"reason":"review","priority_score":0.8}
{"type":"turn_finished","event_type":"turn_finished","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":3,"outcome":"posted"}
{"type":"scheduler_status","event_type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"deadlock","agents":[{"agent_id":"proposer","persona":"proposer"},{"agent_id":"verifier","persona":"verifier"}]}
```

`STRONG` and `ULTRA_STRONG` Blackboard outcomes map to `consensus`; `DEADLOCKED`
maps to `deadlock`. A configured `max_turns` ends the scheduler with `limit`
after the final selected turn completes if no Blackboard terminal outcome won.
The runner must call `finish_turn("no_entry")` when an invocation returns no
entry and `finish_turn("error")` when invocation fails. Successful
`submit_entry()` calls emit `turn_finished` with `outcome="posted"`.
