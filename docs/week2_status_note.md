# Week 2 Status & Integration Checkpoint Note

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 2 Day 6 Checkpoint  
**Scope:** Full-pipeline verification on 5 real KramaBench tasks, current readiness review, and Week 3 preparation.

---

## 1. Joint Checkpoint Verification (5 KramaBench Tasks)

We executed 5 multi-step tasks from KramaBench through our full pipeline (`krama_parser` -> scheduler / blackboard -> event bus -> UI):

| Task ID | Domain | Reasoning Pattern | PXP Tag Transitions | Intelligibility Outcome | UI Verification |
|---|---|---|---|---|---|
| `kb_demo_101` | Multi-Agent Coordination | 3-Agent Consensus Loop | `PROPOSE` -> `RATIFY` -> `REFUTE` -> `REVISE` -> `RATIFY` | `ULTRA_STRONG` | ✅ Rendered live DAG with clean revision edge. |
| `kb_demo_102` | Modal Logic | Deductive Proof | `PROPOSE` -> `RATIFY` -> `RATIFY` | `ULTRA_STRONG` | ✅ Rapid consensus, zero bottleneck alerts. |
| `kb_pilot_001` | Mathematical Constraints | Rejection & Alternate Branch | `PROPOSE` -> `REFUTE` -> `REJECT` (CF) -> `REVISE` | `STRONG` | ✅ Dashed counterfactual edge & bottleneck alert triggered. |
| `kb_pilot_002` | Logic Puzzles | Direct Agreement | `PROPOSE` -> `RATIFY` -> `RATIFY` | `ULTRA_STRONG` | ✅ Immediate convergence, token tally accurate. |
| `kb_pilot_003` | Extended Deduction | Multi-Turn Deliberation | `PROPOSE` -> `RATIFY` -> `REVISE` -> `RATIFY` | `ULTRA_STRONG` | ✅ Smooth radial concentric layout. |

---

## 2. Component Health & Status

### 🟢 What Works
- **WebSocket Streaming & Event Bus** ([`ui/server/event_bus.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/server/event_bus.py)): Rock-solid `asyncio.Queue` pub-sub with zero dropped frames under `requestAnimationFrame` batching.
- **Radial Tree & Force Layouts** ([`Canvas.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/components/Canvas.tsx)): Clean visual hierarchy with seamless toggle between radial orbit rings and physics force-directed graph.
- **Bottleneck Visual Alerts**: Real-time detection of cascading negative tag loops (`REFUTE` / `REJECT`) with pulsating SVG glow rings and banner inspection hooks.
- **History Scrub Control** ([`HistoryScrubber.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/components/HistoryScrubber.tsx)): Non-destructive manual timeline navigation across historical turns with auto-inspected node drawers.
- **Token Counter & Tally UI** ([`TokenTally.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/components/TokenTally.tsx), [`token_counter.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/bench/metrics/token_counter.py)): Real-time prompt/completion token tracking per agent and session global totals.
- **Trial Runner & Plotting** ([`trial_runner.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/bench/metrics/trial_runner.py), [`plotting.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/bench/metrics/plotting.py)): CSV output generation compliant with benchmark rules and chart regeneration.

### 🟡 What’s Flaky / Under Watch
- **Reconnection on Server Restart**: Browser auto-reconnects after 3s, but if the backend restarts with an empty in-memory board, the UI resets state. (Acceptable for dev; in Week 3, initial board state snapshot should be requested on connect).

### ⏳ Blocked on Teammates / Standup Sync
- **Student 1 (Infra / Store)**: Finalizing persistent snapshot storage (`snapshots/`) and board rollback API.
- **Student 2 (Scheduler)**: Exposing direct push emitter to replace polling in `LIVE_TAP` mode.
- **Student 3 (LLM Client)**: Exposing the direct `get_tokenizer()` hook for the specific Ollama model instance to replace the adapter heuristic.

---

## 3. Week 3 Preparation Plan

1. **Counterfactual Rollback Contract Review**:
   - Ensure the UI handles rollback events (`{"type": "board_rollback", "pruned_entry_ids": [...]}`) by visually graying out or pruning rejected sandbox branches while preserving intelligible lineage.
2. **Sandbox Capability Tagging**:
   - Distinct visual badge styling for counterfactual sandbox agents (`is_counterfactual_sim: true`).
3. **Pilot Evaluation Execution**:
   - Run full 20-30 task pilot batches with density ablations ($0\%, 33\%, 66\%, 100\%$) via `trial_runner.py` and output official CSVs and charts.
