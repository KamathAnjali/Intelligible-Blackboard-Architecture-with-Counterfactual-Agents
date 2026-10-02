# UI fields aligned with the shared board contract

The UI consumes `BoardEntry` objects from `blackboard/models.py`. The tag enum is frozen as `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`; `PROPOSE` is not a valid board tag. Initial hypotheses are represented with `REVISE` under the current contract.

| Field | UI use |
| --- | --- |
| `entry_id` | Node identity and graph links. |
| `agent_id` | Node label and details panel. |
| `tag` | Node color, label, and tally category. |
| `prediction`, `explanation` | Entry details. |
| `target_entry_id` | Directed link to the referenced entry. |
| `is_counterfactual_sim` | Marks simulated entries and dashed links. |
| `timestamp` | Entry ordering and details. |

The WebSocket stream wraps entries in a `board_entry` envelope and uses `connection_ack`, `session_summary`, and `stream_complete` messages. The FastAPI server emits this UI transport envelope; it is distinct from the core `BoardEvent` model.

Fields such as confidence and turn index are not in the shared schema. The UI should continue to derive ordering from the append-only entry stream and persona from the `BlackboardState.agents` map when that state is provided.
