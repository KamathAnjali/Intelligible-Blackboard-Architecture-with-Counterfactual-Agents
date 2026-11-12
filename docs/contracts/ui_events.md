# UI event contract

The WebSocket `/ws` event envelope uses `type`, optional `session_id`, `task_id`, and `source`. The frontend's discriminated union lives in `ui/frontend/src/types/sessionEvents.ts`.

Backend events currently supported include `connection_ack`, `board_entry`, `token_tally`, `session_started`, `session_summary`, `session_error`, `stream_complete`, and `heartbeat`. Scheduler-owned events are specified separately in [`scheduler_turn_events.md`](scheduler_turn_events.md).

## State parity fields

`connection_ack`, `session_started`, `board_entry`, `heartbeat`, `session_summary`, and `stream_complete` may carry:

- `entry_count`: number of canonical board entries represented by the event source so far.
- `last_entry_id`: ID of the latest canonical entry, or `null` when there are none.

The UI counts unique `board_entry.entry.entry_id` values and compares that count and last ID with the most recent backend values. A mismatch is shown as a warning; entries are deduplicated by ID after reconnect. A source must send `stream_complete` only after its final summary and board events have been published.

`session_summary.summary` contains terminal outcome/reason, iterations, deadlocks, token usage, evaluator score availability, and the evaluator mode/adaptation label. Evaluator scores may only be sent after the run and evaluator have completed. No reference answer, gold value, reference pipeline, or reference reasoning field belongs in any UI event.

## Session start and failure

`session_started` for a genuine KramaBench UI task includes `session_id`, `task_id`, `mode: "LIVE"`, `counterfactual_fraction`, configured model name, and configured agent IDs. `session_error.error` is user-visible and must describe a safe failure reason; credentials and reference fields must not be included.

The UI task endpoint requires `KRAMA_UI_APPROVE_GENUINE_RUN=1`, `BOARD_MODE=LIVE_TAP`, and permits one active task per process. CORS origins use `UI_CORS_ORIGINS`, defaulting to the two local Vite development origins with credentials disabled. The frontend can override its same-origin WebSocket/REST endpoints using `VITE_WS_URL` and `VITE_API_URL`.

