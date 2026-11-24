# Scheduler Turn Event Contract

Scheduler lifecycle events use the Blackboard event stream and are delivered to
the same subscribers as board events. Events emitted after scheduler creation
include `session_id` and `task_id`. Callers may provide a session ID; otherwise
the scheduler generates one. Subscribers that need the initial status event
must subscribe to the Blackboard before constructing the scheduler.

The event name is available as `type` and the legacy `event_type` field.
Lifecycle fields are top-level in the JSON event. Ordinary board events retain
their `payload` envelope.

## Event types

- `scheduler_status`: emitted on start, terminal state, and resume. `state` is
  `running`, `consensus`, `deadlock`, or `limit`; `agents` contains the roster.
- `turn_started`: emitted when an agent is selected, before its invocation.
  `turn` is 1-based; `reason` and `priority_score` are optional.
- `turn_finished`: emitted when the invocation completes. It matches the
  `agent_id` and `turn` from `turn_started`; `outcome` is `posted`, `no_entry`,
  or `error`.

Example sequence:

```json
{"type":"scheduler_status","event_type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"running","agents":[{"agent_id":"proposer","persona":"proposer"}]}
{"type":"turn_started","event_type":"turn_started","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":1,"reason":"review","priority_score":0.8}
{"type":"turn_finished","event_type":"turn_finished","session_id":"session-1","task_id":"task-1","agent_id":"verifier","turn":1,"outcome":"posted"}
{"type":"scheduler_status","event_type":"scheduler_status","session_id":"session-1","task_id":"task-1","state":"consensus","agents":[{"agent_id":"proposer","persona":"proposer"},{"agent_id":"verifier","persona":"verifier"}]}
```

`STRONG` and `ULTRA_STRONG` board outcomes map to `consensus`; `DEADLOCKED`
maps to `deadlock`. A configured turn cap maps to `limit` if no board terminal
state has already won. The runner calls `finish_turn("no_entry")` when an
invocation returns no entry and `finish_turn("error")` when it fails.

## UI behavior and validation

The WebSocket bridge forwards lifecycle events and counterfactual branch events
without adding simulated branch entries to canonical live history. It preserves
the UI session ID and retains a distinct scheduler session ID when needed.
The frontend uses lifecycle events for active agent, turn, roster, and scheduler
state; if none arrive, it reports that scheduler status was not provided.

The canvas may separately show a same-target negative-entry heuristic; this is
not a scheduler-provided rejection-streak counter. Scheduler terminal state is
reported independently.
