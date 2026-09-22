# Intelligible Blackboard — Week 3 Final Demo Walkthrough Script

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 3 Final Presentation  
**Duration:** ~6–7 Minutes  
**Scope:** Complete end-to-end demonstration of the Intelligible Blackboard with Counterfactual Agent recovery, Split-Panel timeline inspection, Token Cost telemetry, and empirical Pilot Ablation findings.

---

## 🎬 Run-of-Show Sequence

| Timestamp | Phase | Presenter Actions | UI Visual State | Narration Talking Points |
|---|---|---|---|---|
| **0:00 – 1:00** | **1. Architecture & Connect** | Open `http://localhost:5173`. Point to header HUD. Toggle `◎ Radial` vs `☵ Force` layout. | Top bar shows `Status: Connected`, `● LIVE` badge, and radial orbital rings with grid canvas. | *"Welcome. Here we have our real-time Intelligible Blackboard observability dashboard. Notice the radial tree layout rooted at the primary problem statement, projecting argumentative depth outward along concentric rings."* |
| **1:00 – 2:15** | **2. Baseline Deadlock Trigger** | Ingest Task with conflicting constraints. Observe Agent Gamma emit `REFUTE`. | Amber node (`REF`) and amber edge spawn. Second rebuttal triggers red bottleneck flash banner `🚨 BOTTLENECK ALERT: Cascading Rejection`. Affected nodes pulse red. | *"Here, two agents reach an argumentative deadlock over quadratic root signs. The UI's bottleneck detector immediately flags the cascading conflict, pulsing the affected nodes in red."* |
| **3:15 – 3:30** | **3. Counterfactual Recovery (Split Timeline)** | Click `◫ Split Timeline` button in header. Counterfactual sandbox agent steps in. | Screen splits into two views:<br/>- **Left**: Live Mainline Board (green indicator)<br/>- **Right**: Rollback & Sandbox Simulation (cyan indicator with dashed links and `🧪 SIMULATED` badges). | *"Instead of crashing or stalling, Student 1's counterfactual sandbox steps in. By toggling our Split-Panel Alternative Timeline, we see the isolated sandbox exploring the hypothetical negative-root branch on the right without polluting the mainline audit trail."* |
| **3:30 – 4:30** | **4. History Scrub & Token Cost Breakdown** | Click `⚡ Tokens` pill in header. Move bottom slider from Step 1 to Step 6. | Dropdown expands showing Token Cost Breakdown (Mainline: $68\text{t}$, CF Sandbox: $14\text{t}$). History scrubber moves through past states non-destructively. | *"Opening our Token Cost Analysis panel reveals exact LLM token telemetry, clearly isolating sandbox simulation overhead from the mainline. Using our manual history scrubber at the bottom, we can step through past arguments without affecting live streaming."* |
| **4:30 – 6:00** | **5. Pilot Ablation Results** | Present the 3 pilot charts generated from `bench/results/week3_pilot/charts/`. | Show `accuracy_vs_density.png`, `intelligibility_vs_density.png`, `tokens_vs_density.png`. | *"Finally, our 100-run pilot ablation study across 0%, 33%, 66%, and 100% density proves that counterfactual participation increases consensus accuracy from 64% to 100%, with 100% achieving ULTRA_STRONG post-hoc intelligibility at a modest +13.3% token overhead."* |

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
