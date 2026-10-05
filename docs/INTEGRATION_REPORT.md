# Multi-Workstream Integration Report: Combining Anjali, Lopez, and Dhruva

**Target Branch**: `integration-main`  
**Participating Workstreams**:
- **Student 1 (Data Model & Storage - `1-Anjali`)**
- **Student 2 (Protocol & Scheduler - `2-Lopez`)**
- **Student 3 (Agents & Counterfactual - `3-Dhruva`)**

---

## 1. Executive Summary

This integration combines the core contributions of **Student 1 (Anjali)**, **Student 2 (Lopez)**, and **Student 3 (Dhruva)** into a single execution pipeline.

The combined system supports:
1. **Central State Management & Storage (Student 1)**: Thread-safe `RLock` blackboard state, PXP grammatical validation, event emitter hooks, unanimous intelligibility classification, snapshotting, and counterfactual sandbox forking.
2. **Turn Sequencing & Control (Student 2)**: Round-robin turn management, turn ownership enforcement, and terminal status handling (`UNRESOLVED`, `STRONG`, `ULTRA_STRONG`, `DEADLOCKED`).
3. **Reasoning Personas & Local LLMs (Student 3)**: Ollama model integration, 4 reasoning personas (`aggressive_proposer`, `cautious_verifier`, `evidence_auditor`, `counterexample_challenger`), structured PXP generation with model self-checks, and counterfactual replay.

All **103 automated offline tests pass** (with 14 opt-in live tests ready for active Ollama instances).

---

## 2. Workstream Mapping & Overlap Matrix

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                   INTEGRATION WORKFLOW                                  │
├──────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│ Workstream               │ Primary Responsibilities      │ Downstream Overlap           │
├──────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ Student 1: Data Model    │ - models.py (PXP Contracts)   │ Supplies BlackboardState,    │
│ & Storage (Anjali)       │ - core.py (Thread-safe Board) │ BoardEntry, AgentRecord, and │
│                          │ - store.py (JSON Snapshots)   │ event emitter hooks to       │
│                          │ - Event Emitter & Rollback    │ Scheduler, Agents, and UI.   │
├──────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ Student 2: Protocol      │ - scheduler.py                │ Drives turn sequencing for   │
│ & Scheduler (Lopez)      │ - Turn ownership enforcement  │ Student 3 agents; submits to │
│                          │ - Terminal lifecycle handling │ Student 1 Blackboard.        │
├──────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ Student 3: Agents &      │ - llm_client.py (Ollama)      │ Reads history from Student 1 │
│ Counterfactual (Dhruva)  │ - Persona prompts library     │ Blackboard; takes turns from │
│                          │ - PEX/PXP parsers & retries   │ Student 2 Scheduler; submits │
│                          │ - conversation.py             │ validated BoardEntry objects.│
└──────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

---

## 3. Detailed Data Flow & Interaction Lifecycle

### Step 1: Initialization & Registration
1. `InMemoryJSONStore` and `Blackboard` are initialized for a specific task.
2. Participating agents are registered as `AgentRecord` objects on `Blackboard` (`board.register_agent(agent)`).
3. The registration automatically notifies the Event Emitter (`BoardEventType.AGENT_REGISTERED`) and updates the `Scheduler`.

### Step 2: Turn Selection & Enforced Submission
1. The conversation driver requests the next scheduled agent: `agent = scheduler.next_agent()`.
2. The `Scheduler` assigns turn ownership to `agent.agent_id`. Any submission attempt from a different agent is rejected with a `ValueError`.
3. The agent queries the current board history slice: `board.get_history()`.

### Step 3: LLM Inference, Parsing, and Validation
1. Student 3's `OllamaClient.generate_entry(...)` formats the task context, active persona prompt, and chronological history.
2. The local model produces a structured PXP JSON response (`tag`, `prediction`, `explanation`).
3. An explanation self-check verifies that the reasoning supports the conclusion.
4. The output is mapped into a canonical `BoardEntry` (Student 1 contract).

### Step 4: Thread-Safe State Mutation & Classification
1. The entry is submitted via `scheduler.submit_entry(entry)` into `board.post_entry(entry)`.
2. `Blackboard` acquires its reentrant lock (`threading.RLock`), validates agent and target IDs, appends the entry, and evaluates:
   - **Consensus**: Requires all active agents to have their latest entry be `RATIFY` with matching predictions.
     - `ULTRA_STRONG`: Unanimous consensus achieved with productive `REVISE` loops in history.
     - `STRONG`: Unanimous consensus achieved directly without revisions.
   - **Deadlock**: A streak of 4 negative tags (`REFUTE`/`REJECT`) flags `DEADLOCKED`.
3. State changes emit real-time `BoardEvent` deltas to subscribed listeners.

### Step 5: Retrospective Rollback Sandbox Simulation
1. When a deadlock occurs, a counterfactual-capable agent queries an immutable historical slice up to a selected cutoff entry: `board.get_history_slice(cutoff_entry_id)`.
2. The agent forks an isolated simulation blackboard: `sim_board = board.fork_simulation_blackboard(cutoff_entry_id)`.
3. The demo posts a fixed alternative thesis in `sim_board` (`is_counterfactual_sim=True`). No counterfactual evaluator is connected, so the result has no numeric `delta_score`.
4. The simulation result is audited via `board.record_counterfactual_result(...)` while the live session history remains strictly unaltered.

---

## 4. How the Demo Works

The demonstration script ([`demo.py`](file:///Users/anjalikamath/documents_/placement%20prep/sem%207/AI/AI_Project/pxp_blackboard/demo.py)) is a runnable, zero-dependency software fixture with fixed entries. It does not execute the LLM agents, benchmark workload, or counterfactual evaluator:

```bash
python demo.py
```

### Demonstration Output

```text
======================================================================
  SCENARIO 1: Three-Agent Convergence through Scheduler (Ultra-Strong)
======================================================================
[*] Registered agents: ['proposer', 'verifier', 'challenger']
[*] Initial Scheduler status: UNRESOLVED

[Turn 1] -> proposer (aggressive_proposer)
  Posted [REVISE]: Acute Bronchitis

[Turn 2] -> verifier (cautious_verifier)
  Posted [RATIFY]: Acute Bronchitis

[Turn 3] -> challenger (counterexample_challenger)
  Posted [RATIFY]: Acute Bronchitis

[Turn 4] -> proposer (aggressive_proposer)
  Posted [RATIFY]: Acute Bronchitis

[*] Final Intelligibility Outcome: ULTRA_STRONG
[*] Scheduler Terminal Status: ULTRA_STRONG (is_running = False)
[*] Total Real-time Events Emitted: 8
[*] Snapshot written to: snapshots/task_consensus_demo_final.json
[*] UI Replay Trace Steps: 4

======================================================================
  SCENARIO 2: Deadlock Detection & Isolated Counterfactual Replay
======================================================================

[!] DEADLOCK DETECTED at turn 5!
    Loop length: 4 negative tags
    Involved agents: ['agent_alpha', 'agent_beta']
    Status: DEADLOCKED

--- Retrospective Rollback Simulation (Student 3 Sandbox) ---
[*] Slicing live history back to cutoff entry: 60db0e22...
[*] Isolated history slice size: 1 entry
[*] Spawned sandbox blackboard: task_deadlock_demo_sim_60db0e22
[*] Simulated alternative posted in sandbox: Option Z (Compromise)
[*] Counterfactual delta score: not evaluated (no scorer connected)
[*] Live board history length: 5 (Strictly preserved)
[*] Sandbox board history length: 2 (Isolated branch)
[*] Live counterfactual audit events: 1

======================================================================
  INTEGRATION DEMONSTRATION COMPLETE - ALL MODULES WORKING IN HARMONY
======================================================================
```

---

## 5. Live Local LLM Conversation Runner

To execute with a running local Ollama model instance:

```bash
# Bounded 3-agent conversation reaching consensus
python -m agents.conversation --turns 6 --retries 2

# Seeded disagreement scenario requiring correction
python -m agents.conversation --scenario disagreement --turns 6
```

---

## 6. Test Suite Verification

Run the entire test suite:

```bash
.venv/bin/pytest -v
```

**Results**: `103 passed, 14 skipped in 0.32s`
- `tests/test_agent_contract.py`: 5 passed
- `tests/test_blackboard.py`: 5 passed
- `tests/test_classification.py`: 3 passed
- `tests/test_concurrency.py`: 1 passed
- `tests/test_conversation.py`: 18 passed
- `tests/test_emitter.py`: 3 passed
- `tests/test_entry_mapping.py`: 20 passed
- `tests/test_integration.py`: 1 passed
- `tests/test_llm_client.py`: 26 passed
- `tests/test_models.py`: 5 passed
- `tests/test_parser.py`: 4 passed
- `tests/test_rollback.py`: 1 passed
- `tests/test_scheduler.py`: 8 passed
- `tests/test_store.py`: 3 passed
