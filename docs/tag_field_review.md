# PXP Tag Field Review — Assumptions Pending Student 1/2 Schema Confirmation

**Author:** Student 4 (UI & Benchmarking)  
**Date:** September 22, 2026  
**Status:** ⚠️ DRAFT — Based on mock stream format & `blackboard/models.py` inspection.  
All assumptions must be reconciled against Student 1/2's final frozen schema at standup.

---

## 1. Fields Currently Used by the UI (from `BoardEntry` in `blackboard/models.py`)

These fields exist in the **current** `blackboard/models.py` and the mock stream fixture:

| Field | Type | UI Purpose | Confirmed? |
| :--- | :--- | :--- | :--- |
| `entry_id` | `str` (UUID hex) | Unique node ID in the graph. Used for edge `source`/`target` linking. | ✅ From models.py |
| `agent_id` | `str` | Node label, displayed below node circle and in detail drawer. | ✅ From models.py |
| `tag` | `PXPTag` enum | Node color coding, legend entry, tag badge text. | ✅ From models.py |
| `prediction` | `str` | Displayed in node drawer as "What" field. | ✅ From models.py |
| `explanation` | `str` | Displayed in node drawer as "Why" field. | ✅ From models.py |
| `target_entry_id` | `Optional[str]` | Determines directed edge: if set, draw edge `entry_id -> target_entry_id`. | ✅ From models.py |
| `is_counterfactual_sim` | `bool` | Renders dashed edge stroke, shows ★ badge in drawer. | ✅ From models.py |
| `timestamp` | `datetime` | Show on node tooltip or drawer for temporal ordering. | ✅ From models.py |

---

## 2. PXP Tag Enum Values (from `blackboard/models.py`)

```python
class PXPTag(str, Enum):
    RATIFY = "RATIFY"   # aligns with prediction and explanation
    REVISE = "REVISE"   # adjusts own view
    REFUTE = "REFUTE"   # rejects position, logs roadblock
    REJECT = "REJECT"   # full conflict
```

> ⚠️ **Assumption — PROPOSE tag:**  
> The mock stream uses `"tag": "PROPOSE"` to represent the initial board hypothesis entry.  
> However, `PXPTag` in `models.py` only defines `RATIFY`, `REVISE`, `REFUTE`, `REJECT`.  
> **→ Action Required at Standup:** Confirm with Student 1/2 whether:
> - `PROPOSE` will be added to `PXPTag` (preferred), OR  
> - Initial board entries use a different field/mechanism (e.g. a separate `ProposalEntry` type), OR  
> - `PROPOSE` is a UI-only synthetic tag not present in the real schema.

---

## 3. Session-Level Fields (from `BlackboardState` in `blackboard/models.py`)

| Field | Type | UI Purpose | Confirmed? |
| :--- | :--- | :--- | :--- |
| `task_id` | `str` | Session identifier shown in header HUD. | ✅ From models.py |
| `intelligibility` | `IntelligibilityLevel` | Displayed as session outcome badge (`STRONG`, `ULTRA_STRONG`, `DEADLOCKED`). | ✅ From models.py |
| `entries` | `list[BoardEntry]` | Ordered list of all nodes rendered in the graph. | ✅ From models.py |
| `agents` | `dict[str, AgentRecord]` | Agent registry; used to display persona and model name in node drawer. | ✅ From models.py |
| `deadlocks` | `list[DeadlockEvent]` | Could be shown as a visual warning overlay on the graph. | ✅ From models.py |

---

## 4. Additional Fields Desired by UI — Not Yet in Schema

These fields are not currently in `blackboard/models.py` but would improve UI richness. Flag at standup:

| Desired Field | Suggested Location | Reason |
| :--- | :--- | :--- |
| `confidence_score` | `BoardEntry` | Show confidence level on node badge or slider. Useful for counterfactual delta comparison. |
| `turn_index` | `BoardEntry` | Sequential integer for rendering nodes in temporal order (layout hint for DAG). |
| `persona` | `BoardEntry` or via `AgentRecord` lookup | Display agent persona string directly on node tooltip. Currently available via `agents` dict. |

---

## 5. WebSocket Event Envelope Format

The UI currently expects all WebSocket messages to match this envelope:

```json
{
  "type": "board_entry" | "session_summary" | "stream_complete" | "connection_ack",
  "entry": { /* BoardEntry fields — only when type == "board_entry" */ },
  "summary": { /* BlackboardState summary — only when type == "session_summary" */ }
}
```

> ⚠️ **Assumption — Event envelope format:**  
> The `type` discriminator pattern and `entry` / `summary` keys are currently defined only in  
> the mock stream fixture. Confirm with Student 2 that their board event publisher uses the same  
> envelope structure, OR we align on a shared `BoardEvent` Pydantic model in `blackboard/models.py`.
