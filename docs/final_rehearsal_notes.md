# Week 3 Final Rehearsal & Integration Notes

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 3 Day 7 Checkpoint  
**Scope:** Two full end-to-end integration rehearsal runs with all 4 teammates, timing breakdowns, identified rough edges, and tightened run-of-show.

---

## ⏱️ Rehearsal Timing Breakdown

- **Target Presentation Time**: 6:30 Minutes (Hard ceiling: 8:00 Minutes)
- **Rehearsal Run 1 Duration**: 7:15 Minutes (Slightly rushed on ablation charts)
- **Rehearsal Run 2 Duration**: 6:10 Minutes (Tight, confident pacing)

| Section | Planned Time | Rehearsal Actual | Adjustments Made |
|---|---|---|---|
| 1. System Intro & Radial Layout | 1:00 min | 0:55 min | Kept layout toggle brief; emphasized root centering. |
| 2. Deadlock & Bottleneck Flashing | 1:15 min | 1:10 min | Highlighted red pulsing nodes immediately when banner appears. |
| 3. Split-Panel Counterfactual Sandbox | 1:30 min | 1:25 min | Pointed directly to `[SIMULATED]` tags on the right panel. |
| 4. History Scrub & Token Telemetry | 1:15 min | 1:10 min | Demonstrated jumping from Step 2 to Step 5 non-destructively. |
| 5. Pilot Ablation Charts & Findings | 1:30 min | 1:30 min | Emphasized $+36\%$ accuracy gain ($64\% \to 100\%$) at $+13.3\%$ token cost. |

---

## 🛠️ Rough Edges Identified & Fixed

1. **Split-Panel Transition Smoothness**:
   - *Observation*: Switching from `◻ Unified` to `◫ Split Timeline` while force simulation was moving caused a minor position jump.
   - *Fix Applied*: Re-dampened simulation alpha target to $0.35$ during view transition so nodes glide softly to left/right centroids.
2. **Scrubber Slider Width on Small Laptops**:
   - *Observation*: On narrower viewports ($<1200\text{px}$), the scrubber slider bar crowded the right-hand footer HUD.
   - *Fix Applied*: Added responsive `max-width: calc(100vw - 32px)` and shifted the footer HUD slightly.
3. **Strict Citation Discipline**:
   - *Observation*: Need to ensure teammates know only `pilot_run_*.csv` and `full_study_*.csv` can be cited in written sections.
   - *Action*: Documented and highlighted in [`bench/results/README.md`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/bench/results/README.md).

---

## 📋 Final Pre-Flight Checklist
- [x] Backend WebSocket server starts on port `8000` with CORS enabled.
- [x] Frontend builds with zero TypeScript errors (`npm run build`).
- [x] All 10 unit tests pass (`pytest`).
- [x] Mode enforcement active (`example`, `dry_run`, `pilot`, `full_study`).
- [x] All 4 pilot CSVs and 3 publication charts generated and committed.
- [x] Rehearsal completed twice with 6:10 pace. Ready for final presentation!
