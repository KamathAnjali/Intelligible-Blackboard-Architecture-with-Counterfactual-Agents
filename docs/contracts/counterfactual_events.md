# Counterfactual UI event contract

Branch history is a read-only simulation. A branch entry must never be posted to, or counted as part of, canonical live history. Events below are optional; clients show **No counterfactual activity** until an explicit branch event arrives. Synthetic demonstrations must set `source: "SYNTHETIC_FIXTURE"` and remain visibly labelled synthetic.

All events carry `type`, `session_id`, `task_id`, `source` (`LIVE` for real runs), and the fields below. `branch_id` is stable for the lifetime of one sandbox.

| Event | Fields |
|---|---|
| `branch_created` | `branch_id`, `source_session_id`, `cutoff_entry_id`, `altered_entry_id`, `altered_tag`, `explanation_hash`, `triggered_by_agent` |
| `branch_entry_posted` | `branch_id`, `entry` (serialized board entry, including `entry_id`, `agent_id`, `tag`, `prediction`, `explanation`, `target_entry_id`) |
| `counterfactual_result` | `branch_id`, `delta_score` (nullable), `adopted`, `reason`, `adoption_method` (`prompt`, `vote_weight`, or `discarded`) |
| `live_adjustment_applied` | `branch_id`, `agent_id`, `method`, `session_entry_id` |

`branch_created` and `branch_entry_posted` are emitted on the canonical session's observer stream, but their entries remain branch-scoped. `branch_entry_posted` must not be represented as `board_entry`; doing so would corrupt canonical entry counts, parity checks, and live history. The UI deduplicates branch entries by branch and entry ID. A cutoff is a visual marker referencing the canonical live entry; it does not truncate or rewrite the live pane. Results and adoption events describe the branch decision and any separately posted live adjustment.

## Requests to owning workstreams

**Student 1 (blackboard):** add event-emitter notifications for branch creation, each sandbox entry, completed `CounterfactualResult`, and live adjustment. Populate branch/session/task IDs and payloads from authoritative board state; emit branch entries on the live observer emitter without inserting them into canonical `BlackboardState.entries`. Keep canonical `entry_count` and `last_entry_id` unchanged for branch-only events. Include `adoption_method` and a reason with the result, or expose a stable mapping from existing result fields.

**Student 3 (agents):** populate the altered entry/tag/explanation hash/triggering agent when creating a branch; preserve `branch_id` on every simulated entry/result; report the result reason, adoption method and the resulting live entry ID when an adjustment is applied. Ensure simulated entries are marked `is_counterfactual_sim=true` and are never submitted as canonical entries. Student 4's bridge accepts these events when present and does not infer missing lifecycle events from a generic rollback notification.

## Student 4 transport/UI behavior

The server forwards only the four event types above and preserves `branch_id`. The browser routes them to branch state, never to canonical live entry state. No events means an explicit empty state. Any fixture events carry `source: "SYNTHETIC_FIXTURE"`; they are for UI tests/demo only and cannot be presented as benchmark evidence.
