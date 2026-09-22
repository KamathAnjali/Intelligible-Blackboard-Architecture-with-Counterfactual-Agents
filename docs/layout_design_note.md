# Graph Layout Design Note — Force-Directed vs. Radial Tree Layout

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 2 Day 4  
**Scope:** Evaluation and selection of optimal layout algorithms for multi-agent reasoning graphs on the Intelligible Blackboard.

---

## 1. Problem Context

In our PXP Blackboard architecture, agents post structured entries:
- Initial problem hypotheses (`REVISE` / `PROPOSE`) serve as root anchors.
- Iterative arguments (`RATIFY`, `REVISE`, `REFUTE`, `REJECT`) create directed reference edges pointing back to parent entries.
- Counterfactual simulations branch off refutations and either converge back or terminate upon rejection.

As sessions scale from 3-5 entries to 20+ multi-agent turns, clear spatial hierarchy becomes essential for debugging reasoning deadlocks and evaluating post-hoc intelligibility.

---

## 2. Layout Comparison & Evaluation

| Criteria | Force-Directed Layout (D3 `d3-force`) | Radial / Tree Layout (`d3-hierarchy` / Radial concentric) |
|---|---|---|
| **Branching Clarity** | 🟡 Organic clusters form, but long reasoning chains can tangle or loop visually. | 🟢 **Superior**: Clear directional flow radiating outward from the root hypothesis node. |
| **Hierarchical Depth** | 🟡 Depth is implicit based on edge springs, not strictly ordered. | 🟢 **Superior**: Concentric rings or downward tree levels explicitly map to reasoning turn depth. |
| **Deadlock & Cycle Visibility** | 🟢 Good at clustering mutually referencing or conflicting agents. | 🟢 Distinctly highlights aborted counterfactual sub-branches. |
| **Live Incremental Updates** | 🟢 **Superior**: New nodes smoothly drift into place without snapping the entire graph. | 🟡 Requires animated polar coordinate interpolation when new branches spawn. |
| **Screen Real Estate** | 🟢 Utilizes full 2D canvas dynamically. | 🟢 Concentric layout keeps parent context in center while branches expand radially. |

---

## 3. Final Architecture Choice: Dual Mode (Force-Directed + Radial Tree)

### Selected Default: **Radial Tree Layout (Rooted at Initial Proposal)**
**Justification:**  
Because multi-agent debates on the blackboard are structurally rooted Directed Acyclic Graphs (DAGs) starting from a single problem statement, the **Radial Tree layout rooted at the original proposal** provides the highest intelligibility. The root sits at the center $(x_0, y_0)$, and each subsequent argumentative layer occupies concentric orbital radii ($r = d \times 120\text{px}$). Counterfactual branches flare out along distinct angular wedges, making rejected or refuted pathways immediately distinguishable from the main consensus trunk.

### Interactive Hybrid Toggle
We support both modes in [`Canvas.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/components/Canvas.tsx):
- **Radial Tree Mode (Default)**: Best for structured DAG inspection and hierarchy.
- **Force Mode**: Best for free-form organic exploration and drag-and-drop debugging.
