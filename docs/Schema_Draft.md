# Blackboard Schema Draft

## BlackboardState Schema

The overall state of the blackboard is defined by the following schema:

- **task_id** (`str`): Unique identifier for the current task being executed.
- **entries** (`list[BoardEntry]`): An append-only list of all entries posted to the blackboard.
- **agents** (`dict[str, AgentRecord]`): A registry mapping agent IDs to their respective records.
- **intelligibility** (`IntelligibilityLevel`): The classification of the session's intelligibility (e.g., 'UNRESOLVED', 'STRONG', 'ULTRA_STRONG', 'DEADLOCKED').
- **deadlocks** (`list[DeadlockEvent]`): A list of deadlock events encountered during the session.
- **counterfactual_events** (`list[CounterfactualResult]`): Output of retrospective rollback simulations.

## Entry-History Structure Justification

**Structure Choice: Append-only List**

An append-only list is chosen over an indexed dict for `entries` because the blackboard acts as an event ledger where preserving the strict chronological sequence of interactions is critical for replay and visualization. While an indexed dictionary would provide faster O(1) lookups by `entry_id`, we frequently need to slice the history (e.g., tail evaluation) or replay events in exactly the order they occurred. Because the volume of messages per session is relatively small (typically hundreds), a list provides the natural semantics of an immutable, sequential history without significant performance penalties during target resolution.

## BoardEntry Pydantic Contract

- **entry_id** (`str`): Unique identifier for the entry (defaults to a UUID hex).
- **agent_id** (`str`): The ID of the agent making the entry.
- **tag** (`PXPTag`): The PXP move ('RATIFY', 'REVISE', 'REFUTE', 'REJECT').
- **prediction** (`str`): The "what" (cannot be empty).
- **explanation** (`str`): The "why" (cannot be empty).
- **target_entry_id** (`Optional[str]`): Points to the specific prior entry this reacts to, enabling UI graph edges.
- **is_counterfactual_sim** (`bool`): True if from a rollback sandbox, false if from live run.
- **timestamp** (`datetime`): The UTC timestamp of creation.

## 3 Example Valid BoardEntry JSON Payloads

### Example 1: Initial Prediction (No target)
```json
{
  "entry_id": "msg_001",
  "agent_id": "agent_alpha",
  "tag": "REVISE",
  "prediction": "The patient has acute bronchitis.",
  "explanation": "Based on the symptoms of cough and mild fever lasting for 3 days without underlying chronic conditions.",
  "target_entry_id": null,
  "timestamp": "2026-09-22T10:00:00Z",
  "is_counterfactual_sim": false
}
```

### Example 2: Agreement with previous prediction
```json
{
  "entry_id": "msg_002",
  "agent_id": "agent_beta",
  "tag": "RATIFY",
  "prediction": "The patient has acute bronchitis.",
  "explanation": "I agree with agent_alpha. The absence of severe respiratory distress rules out pneumonia.",
  "target_entry_id": "msg_001",
  "timestamp": "2026-09-22T10:01:30Z",
  "is_counterfactual_sim": false
}
```

### Example 3: Counterfactual Simulation Alternative
```json
{
  "entry_id": "msg_003_cf",
  "agent_id": "agent_alpha",
  "tag": "REVISE",
  "prediction": "The patient might have early-stage pneumonia.",
  "explanation": "Re-evaluating the slight crackles heard on auscultation, we should consider a mild pneumonia presentation despite the short duration.",
  "target_entry_id": "msg_002",
  "timestamp": "2026-09-22T10:05:00Z",
  "is_counterfactual_sim": true
}
```
