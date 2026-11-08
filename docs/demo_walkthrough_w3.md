# Intelligible Blackboard — Week 3 Final Demo Walkthrough Script

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 3 Final Presentation  
**Duration:** ~6–7 Minutes  
**Scope:** Demonstration of the Intelligible Blackboard UI, replayed counterfactual timeline, and token estimates. Official benchmark findings are not available yet.

---

## 🎬 Run-of-Show Sequence

| Timestamp | Phase | Presenter Actions | UI Visual State | Narration Talking Points |
|---|---|---|---|---|
| **0:00 – 1:00** | **1. Architecture & Connect** | Open `http://localhost:5173`. Point to header HUD. Toggle `◎ Radial` vs `☵ Force` layout. | Top bar shows `Status: Connected`, `● LIVE` badge, and radial orbital rings with grid canvas. | *"Welcome. Here we have our real-time Intelligible Blackboard observability dashboard. Notice the radial tree layout rooted at the primary problem statement, projecting argumentative depth outward along concentric rings."* |
| **1:00 – 2:15** | **2. Baseline Deadlock Trigger** | Ingest Task with conflicting constraints. Observe Agent Gamma emit `REFUTE`. | Amber node (`REF`) and amber edge spawn. Second rebuttal triggers red bottleneck flash banner `🚨 BOTTLENECK ALERT: Cascading Rejection`. Affected nodes pulse red. | *"Here, two agents reach an argumentative deadlock over quadratic root signs. The UI's bottleneck detector immediately flags the cascading conflict, pulsing the affected nodes in red."* |
| **3:15 – 3:30** | **3. Counterfactual Recovery (Split Timeline)** | Click `◫ Split Timeline` button in header. Counterfactual sandbox agent steps in. | Screen splits into two views:<br/>- **Left**: Live Mainline Board (green indicator)<br/>- **Right**: Rollback & Sandbox Simulation (cyan indicator with dashed links and `🧪 SIMULATED` badges). | *"Instead of crashing or stalling, Student 1's counterfactual sandbox steps in. By toggling our Split-Panel Alternative Timeline, we see the isolated sandbox exploring the hypothetical negative-root branch on the right without polluting the mainline audit trail."* |
| **3:30 – 4:30** | **4. History Scrub & Token Estimate** | Click `⚡ Tokens` pill in header. Move the bottom slider through the replay. | The panel shows per-turn token estimates from fixture text; the history scrubber moves through recorded states. | *"This panel shows token estimates for the replay text. They are not provider-reported usage. The history scrubber lets us inspect past events without changing the replay."* |
| **4:30 – 6:00** | **5. Benchmark status** | Show [`bench/results/README.md`](../bench/results/README.md) and the planned evaluation note. | Explain that pilot and full-study modes are disabled pending real agent runs and the official evaluator. | *"We have not produced official KramaBench scores yet. The existing pilot-named files came from a simulator and are excluded from reporting. Our next step is to connect actual agent outputs to the benchmark's evaluator; then we can report its task scores separately from internal agreement and token estimates."* |

---

## 🚀 Quick Execution Commands

```bash
# Terminal 1 — Start UI Backend
uvicorn ui.server.main:app --reload --port 8000

# Terminal 2 — Start UI Frontend
cd ui/frontend && npm run dev

# Terminal 3 — (Optional) Live Feed Task Batch or Replay
python -m bench.ingest.krama_parser --feed-live --interval 0.8
```
