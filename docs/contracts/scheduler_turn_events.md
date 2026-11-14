# Scheduler turn event proposal

The UI supports these optional events. They are not emitted by the current Scheduler contract, which is owned by Student 2. The UI shows **“scheduler status: not provided by backend”** until it receives one of these events; it does not infer scheduler state from board tags.

```json
{"type":"turn_started","session_id":"...","agent_id":"verifier","turn":3,"reason":"review","priority_score":0.8}
{"type":"turn_finished","session_id":"...","agent_id":"verifier","turn":3,"outcome":"posted"}
{"type":"scheduler_status","session_id":"...","state":"running|consensus|deadlock|limit","agents":[{"agent_id":"proposer","persona":"..."}]}
```

`priority_score` is optional and should be omitted when the scheduler has no numeric priority. `turn_finished.outcome` is a short terminal action label, not free-form private reasoning. The roster may be supplied with `scheduler_status.agents`; `turn_started` also lets the UI add an agent it has not seen before. Every event should include a session ID so a reconnecting client can discard unrelated events.

## UI behavior implemented now

The reducer tracks scheduler state, roster, active agent, and turn count. The roster highlights the active agent. When no scheduler event arrives, the status remains explicitly unavailable. Student 2 request: emit these three event types from the scheduler/session bridge and include the active agent roster in `scheduler_status`.

No `blackboard/` or scheduler-owned file was changed for this UI-side implementation.

