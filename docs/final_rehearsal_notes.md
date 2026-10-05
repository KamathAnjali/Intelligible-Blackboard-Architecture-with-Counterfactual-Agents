# Week 3 Final Rehearsal & Integration Notes

> **Historical draft warning:** This rehearsal note contains benchmark claims from the earlier synthetic simulator. The claimed accuracy gain and token overhead are withdrawn. No official KramaBench pilot has been run; do not present the old local charts or CSVs as findings. See [`bench/results/README.md`](../bench/results/README.md).

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
| 4. History Scrub & Token Estimate | 1:15 min | 1:10 min | Demonstrated replay navigation; displayed counts are entry-text estimates, not full model usage. |
| 5. Benchmark status | 1:30 min | 1:30 min | Explain that official evaluation is planned and the current runner only supports labelled simulations. |

---

## 🛠️ Rough Edges Identified & Fixed

1. **Split-Panel Transition Smoothness**:
   - *Observation*: Switching from `◻ Unified` to `◫ Split Timeline` while force simulation was moving caused a minor position jump.
   - *Fix Applied*: Re-dampened simulation alpha target to $0.35$ during view transition so nodes glide softly to left/right centroids.
2. **Scrubber Slider Width on Small Laptops**:
   - *Observation*: On narrower viewports ($<1200\text{px}$), the scrubber slider bar crowded the right-hand footer HUD.
   - *Fix Applied*: Added responsive `max-width: calc(100vw - 32px)` and shifted the footer HUD slightly.
3. **Benchmark provenance**:
   - The earlier pilot-named files came from a simulator, not actual agent runs or the KramaBench evaluator. They are not citable as benchmark results. The result loader now rejects files without official evaluator provenance.

---

## 📋 Final Pre-Flight Checklist
- [x] Backend WebSocket server starts on port `8000` with CORS enabled.
- [x] Frontend builds with zero TypeScript errors (`npm run build`).
- [x] All 10 unit tests pass (`pytest`).
- [x] Simulation modes are labelled; `pilot` and `full_study` are disabled until agent execution and official evaluation are integrated.
- [ ] Run an official KramaBench pilot after connecting the real agent runner and evaluator.
- [x] Rehearsal completed twice with 6:10 pace. Ready for final presentation!
