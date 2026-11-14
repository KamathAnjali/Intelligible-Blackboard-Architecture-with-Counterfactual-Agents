# Full-Pipeline Dry-Run & Rendering Bugfix Log — Day 12

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 2 Day 5  
**Scope:** Full-pipeline dry-run against integrated scheduler + blackboard + agents stream, documenting edge-case rendering bugs identified and fixed.

---

## 🛠️ Summary of Issues Found & Fixed

### 1. High-Frequency WebSocket Race Conditions & Dropped Nodes
- **Symptom**: When agents produced turns in rapid succession (<50ms apart during parallel verification), successive React `setNodes` state updates triggered race conditions and dropped intermittent link connections or caused flickering D3 simulation restarts.
- **Root Cause**: Unbatched state dispatches caused D3 force links to evaluate before both source and target nodes were reconciled in the DOM.
- **Fix Implemented**: Added an `incomingBufferRef` in [`App.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/App.tsx) and synchronized state updates via `requestAnimationFrame`. Events are now dequeued and appended in unified atomic batches per animation frame.

---

### 2. Idempotent Token Tally & Node Deduplication
- **Symptom**: Reconnecting WebSockets or replaying recorded streams multiple times resulted in inflated token tallies and duplicate SVG node groups.
- **Root Cause**: State accumulation appended incoming entries without checking against a persistent session `seenNodeIds` set.
- **Fix Implemented**: Added `seenNodeIdsRef` and `seenLinkKeysRef` in [`App.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/App.tsx) guaranteeing strict idempotency for both graph elements and running token tallies.

---

### 3. Tree Depth Clashing in Radial Layout
- **Symptom**: Complex counterfactual branches spanning $\ge 4$ depth levels collided with sibling nodes on identical concentric radius coordinates.
- **Root Cause**: Radial angle allocation divided the full circle evenly without grouping nodes by depth tier.
- **Fix Implemented**: Implemented `nodesByDepth` tier mapping in [`Canvas.tsx`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/ui/frontend/src/components/Canvas.tsx) calculating angular spans partitioned per depth ring $(r = \text{depth} \times 150\text{px})$.

---

### 4. Bottleneck Detection False Positives on Solitary Rebuttals
- **Symptom**: Single isolated `REFUTE` tags correctly answered by a subsequent `REVISE` incorrectly triggered global bottleneck alert banners.
- **Root Cause**: Threshold checked only tag existence rather than cascading cluster density.
- **Fix Implemented**: Refined `detectBottlenecks()` to trigger only when $\ge 2$ negative tags converge on the same parent entry or appear consecutively without revision, ensuring alerts only trigger on true reasoning roadblocks.
