# Blackboard Public API Usage Guide

This guide details how other workstreams (**Student 2: Protocol & Scheduler**, **Student 3: Agents & Counterfactual**, and **Student 4: UI & Benchmarking**) interact with the core blackboard and storage layers.

---

## 1. Quick Overview

```python
from blackboard import Blackboard, AgentRecord, BoardEntry, PXPTag, InMemoryJSONStore
from blackboard.scheduler import Scheduler

# 1. Initialize backing store and blackboard
store = InMemoryJSONStore(snapshot_dir="./snapshots")
board = Blackboard(task_id="task_med_001", store=store)

# 2. Register participating agents
board.register_agent(AgentRecord(agent_id="agent_alpha", persona="proposer", model_name="mistral:7b"))
board.register_agent(AgentRecord(agent_id="agent_beta", persona="verifier", model_name="mistral:7b"))

# 3. Attach scheduler (Student 2)
scheduler = Scheduler(board)

# 4. Agent generates entry and submits via scheduler turn
agent = scheduler.next_agent()
entry = BoardEntry(
    agent_id=agent.agent_id,
    tag=PXPTag.REVISE,
    prediction="Acute Bronchitis",
    explanation="3-day cough presentation without underlying chronic issues.",
)
deadlock_event = scheduler.submit_entry(entry)
```

---

## 2. API Reference for Teammates

### A. For Student 2 (Scheduler & Control)

- **`board.register_agent(agent: AgentRecord) -> None`**: Registers an agent in the blackboard's central registry.
- **`board.get_agents(active_only: bool = False) -> dict[str, AgentRecord]`**: Returns registered agents.
- **`board.current_intelligibility -> IntelligibilityLevel`**: Returns current status (`UNRESOLVED`, `STRONG`, `ULTRA_STRONG`, `DEADLOCKED`) without performing deep copies.
- **`board.post_entry(entry: BoardEntry) -> DeadlockEvent | None`**: Validates schema, checks agent/target registration, appends to history, recomputes intelligibility, and returns a `DeadlockEvent` if a negative loop is detected.

### B. For Student 3 (Agents & Counterfactual)

- **`board.get_history(agent_id: str | None = None) -> list[BoardEntry]`**: Retrieves chronological entries.
- **`board.get_history_slice(cutoff_entry_id: str | None, include_counterfactual: bool = False) -> list[BoardEntry]`**: Returns historical slice up to `cutoff_entry_id` (used to prime counterfactual prompts).
- **`board.fork_simulation_blackboard(cutoff_entry_id: str) -> Blackboard`**: Creates an isolated sandbox blackboard for replaying counterfactual turns without modifying live history.
- **`board.record_counterfactual_result(result: CounterfactualResult) -> None`**: Attaches counterfactual scoring and evaluation metadata.

### C. For Student 4 (UI & Benchmarking)

- **`board.subscribe(listener: Callable[[BoardEvent], None]) -> None`**: Subscribes to real-time events (`AGENT_REGISTERED`, `ENTRY_POSTED`, `DEADLOCK_DETECTED`, `INTELLIGIBILITY_CHANGED`, `SNAPSHOT_SAVED`, `ROLLBACK_FORKED`).
- **`board.get_state() -> BlackboardState`**: Returns a deep copy snapshot of the current blackboard state.
- **`store.snapshot_to_disk(task_id: str, tag: str | None) -> Path`**: Persists state to a JSON file.
- **`store.load_snapshot_from_disk(task_id: str, filepath: Path | None) -> BlackboardState`**: Loads snapshot.
- **`store.export_replay_trace(task_id: str) -> list[dict]`**: Generates step-by-step history trace for the UI inspector.

---

## 3. Real-Time Event Subscription Example (WebSocket Layer)

```python
from blackboard import BoardEvent, BoardEventType

def websocket_event_forwarder(event: BoardEvent):
    # Serializes directly to JSON for WebSocket broadcast
    payload_json = event.model_dump_json()
    print(f"[WS OUT] Event {event.event_type}: {payload_json}")

board.subscribe(websocket_event_forwarder)
```
