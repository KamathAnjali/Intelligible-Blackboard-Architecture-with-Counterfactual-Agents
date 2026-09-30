# Data Contract: Retrospective Rollback & Counterfactual Sandbox

**Author**: Student 1 (Data Model & Storage) in sync with Student 3 (Agents & Counterfactual)  
**Milestone**: Week 2 Checkpoint  
**Status**: Agreed & Implemented

---

## 1. Overview & Purpose

When agents encounter a deadlock (`DEADLOCK_WINDOW` consecutive negative `REFUTE`/`REJECT` entries), counterfactual-capable agents can trigger a **retrospective rollback simulation**. 

To preserve experimental integrity and prevent live session pollution:
1. The historical timeline prior to the disputed decision must remain **strictly immutable**.
2. Simulations must execute in an **isolated sandbox blackboard**.
3. Evaluated alternatives produce a quantifiable **`delta_score`** before any modification or recommendation is promoted to the live board.

---

## 2. Five Pillars of the Rollback Contract

### Pillar 1: Historical Cutoff Point
- **Definition**: The specific historical `entry_id` ($E_{\text{cutoff}}$) where an earlier agent contribution is selected for modification.
- **Contract**: `cutoff_entry_id: str`.
- **Constraint**: Must exist within the live board's chronological history.

### Pillar 2: Board Snapshot & History Slice
- **Definition**: The exact sub-slice of board history $[E_0, E_1, \dots, E_{\text{cutoff}}]$.
- **Contract Function**: `board.get_history_slice(cutoff_entry_id, include_counterfactual=False)`
- **Sandbox Factory**: `board.fork_simulation_blackboard(cutoff_entry_id)` produces an isolated `Blackboard` instance populated with state up to $E_{\text{cutoff}}$.
- **Constraint**: All live entries created after $E_{\text{cutoff}}$ are excluded in the simulation sandbox. Live board state is completely isolated and unaffected by writes in the sandbox.

### Pillar 3: Relevant Agent Context & Prompt Injection
- **Definition**: The context provided to the counterfactual agent's local LLM prompt to seed the alternative path.
- **Payload Structure**:
  ```json
  {
    "task_id": "task_123",
    "cutoff_entry_id": "entry_002",
    "history_slice": [...],
    "target_agent_id": "agent_alpha",
    "divergent_hypothesis_prompt": "Re-evaluate the auscultation findings to propose an alternative explanation."
  }
  ```

### Pillar 4: Simulation Output & Delta Scoring
- **Definition**: The alternate entries produced inside the sandbox tagged with `is_counterfactual_sim=True` and scored against convergence metrics.
- **Contract Model**: `CounterfactualResult`
  - `agent_id` (`str`): Agent executing the simulation.
  - `original_entry_id` (`str`): The historical entry rewritten.
  - `simulated_entry` (`BoardEntry`): The alternative proposal generated.
  - `delta_score` (`float`): Metric measuring probability of downstream consensus (e.g. $[0.0, 1.0]$ or $[-1.0, 1.0]$).
  - `applied_to_live` (`bool`): Whether this outcome was promoted to live execution.
  - `timestamp` (`datetime`): UTC timestamp.

### Pillar 5: Proposed Live Update Mechanism
- **Rule**: Counterfactual entries **NEVER** overwrite previous entries in the live history.
- **Adoption**: If $\text{delta\_score} > \theta_{\text{threshold}}$, the agent creates a **new live entry** (`tag=PXPTag.REVISE`) citing the revised rationale, allowing downstream agents to ratify and escape the deadlock naturally.
- **Audit Trail**: The `CounterfactualResult` is appended to `BlackboardState.counterfactual_events` via `board.record_counterfactual_result(...)` for full visibility and benchmarking.
