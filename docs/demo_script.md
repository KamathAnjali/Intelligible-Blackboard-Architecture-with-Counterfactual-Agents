# Friday Demo Script — Intelligible Blackboard Live Graph UI

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 1 Demo / Week 2 Day 1 Integration  
**Scope:** Running and narrating the live graph visualization for the PXP Blackboard multi-agent reasoning session.

---

## 1. Quick Start & Prerequisites

Ensure all dependencies are installed across Python and Node.js environments.

### Terminal 1 — Backend Server
```bash
# Start backend in LIVE_TAP mode (default)
uvicorn ui.server.main:app --reload --port 8000

# OR start in LOG_REPLAY mode (to demo recorded benchmark run with "REPLAY" UI badge)
BOARD_MODE=LOG_REPLAY uvicorn ui.server.main:app --reload --port 8000
```

### Terminal 2 — Frontend UI
```bash
cd ui/frontend
npm run dev
```

Open `http://localhost:5173` in Chrome / browser.

---

## 2. Demo Narration Walkthrough

| Step | Action | UI Behavior & Visuals | Narration Talking Points |
|---|---|---|---|
| **1. Initialization** | Load page (`http://localhost:5173`) | Top header displays **Status: Connected** with a green pulse dot.<br/>Mode badge shows `● LIVE` (green) or `⏪ REPLAY` (amber).<br/>Center canvas displays *"Waiting for board events..."* waiting state. | *"Here we have our real-time Intelligible Blackboard observability canvas. The WebSocket connects immediately to FastAPI. Notice the mode badge indicating whether we are tapped into the live agent scheduler or replaying a recorded trace."* |
| **2. Hypothesis Proposal (`PROPOSE`)** | Agent 1 posts root problem statement | Purple circular node fades in (`#8b5cf6`) with label `PRO`. Agent name `Agent_Alpha` appears underneath. Force simulation softly positions the node. | *"Agent Alpha kicks off the reasoning session with an initial `PROPOSE` entry, introducing the primary solution hypothesis to the blackboard."* |
| **3. Verification (`RATIFY`)** | Agent 2 verifies proposal | Green node (`#10b981`, `RAT`) appears and an arrow springs into place pointing back to Agent Alpha's proposal. | *"Agent Beta acts as the verifier. It evaluates the claim and posts a `RATIFY` tag in emerald green, creating a directed reference edge to the original entry."* |
| **4. Critical Roadblock (`REFUTE`)** | Agent 3 challenges assumption | Amber node (`#f59e0b`, `REF`) spawns with an amber reference edge pointing to the target entry. | *"Agent Gamma uncovers a missing condition or counter-example, emitting a `REFUTE` tag in amber. This marks a critical disagreement node on the blackboard."* |
| **5. Self-Correction (`REVISE`)** | Agent 1 updates solution | Blue node (`#3b82f6`, `REV`) attaches to the refutation node. | *"In response to the refutation, Agent Alpha publishes a `REVISE` tag in blue, updating the solution space while preserving full audit history."* |
| **6. Counterfactual Branching (`REJECT`)** | Sandbox Agent tests counterfactual branch | Red node (`#ef4444`, `REJ`) appears with a **dashed edge**, indicating counterfactual sandbox simulation. | *"Here our counterfactual sandbox agent tests an alternative hypothesis branch. Notice the dashed edge designating hypothetical reasoning, culminating in a `REJECT` tag in red."* |
| **7. Node Inspection & Intelligibility HUD** | Click on any node in the graph | Right-hand Glassmorphism sidebar slides in showing:<br/>- Full Agent ID & Tag badge<br/>- Formatted **Prediction / Solution**<br/>- Detailed **Explanation & Justification**<br/>- Target entry reference & timestamps | *"Clicking on any node opens the inspection inspector, giving immediate visibility into the agent's internal chain-of-thought, prediction, and parent references."* |
| **8. Session Completion** | Final consensus reached | `Stream Complete` badge illuminates in the top header. Bottom HUD reports total node & link counts with pan/zoom hints. | *"The graph simulation stabilizes into an intelligible DAG of the multi-agent debate, proving complete post-hoc and live intelligibility of the reasoning trajectory."* |

---

## 3. Architecture & Code Integration Points

1. **Tag Colors Single Source of Truth**:
   Defined in [`ui/frontend/src/constants/tagColors.ts`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/constants/tagColors.ts):
   - `PROPOSE`: `#8b5cf6` (Violet)
   - `RATIFY`: `#10b981` (Green)
   - `REVISE`: `#3b82f6` (Blue)
   - `REFUTE`: `#f59e0b` (Amber)
   - `REJECT`: `#ef4444` (Red)

2. **Async Pub-Sub Event Bus**:
   Defined in [`ui/server/event_bus.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/server/event_bus.py) — non-blocking fan-out via `asyncio.Queue` per connected client.

3. **Board Tap & Mode Switching**:
   Defined in [`ui/server/board_tap.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/server/board_tap.py):
   - `BOARD_MODE=LIVE_TAP`: polls/subscribes to `blackboard.core.Blackboard`.
   - `BOARD_MODE=LOG_REPLAY`: replays snapshots from `bench/data/recorded_session.json` with UI `"REPLAY"` badge.
