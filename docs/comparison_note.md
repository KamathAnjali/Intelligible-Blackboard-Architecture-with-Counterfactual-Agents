# Graph Visualization Library Spike Comparison: D3.js vs. Vis.js (vis-network)

**Project:** Intelligible Blackboard (PXP Reasoning Agent Flow)  
**Author:** Student 4 (UI & Benchmarking)  
**Date:** September 22, 2026  

---

## Executive Summary & Selection

| Dimension | D3.js (`d3-force` / SVG / Canvas) | Vis.js (`vis-network`) |
| :--- | :--- | :--- |
| **Live / Incremental Updates** | Direct access to simulation data (`alphaTarget`, node/link arrays); seamless entry/exit joins | Network `.DataSet` abstraction; supports `add`/`update`, but canvas re-layouts can feel abrupt |
| **Styling Flexibility** | Full CSS / SVG control; native gradients, glow filters, CSS keyframe animations, crisp vector scaling | Canvas-based rendering; styled via static options objects; custom node shapes require HTML Canvas API drawing callbacks |
| **Bundle Size** | Modular (`d3-force` ~12kB, full D3 ~70kB minified) | Heavier (~300kB+ bundle size for `vis-network`) |
| **React Integration** | Idiomatic declarative wrapper via `useRef` + `useEffect` syncing React state to SVG/DOM | Imperative canvas lifecycle wrapped in `ref`; React state changes require manual `.setData()` calls |
| **Force Layout Customization** | Highly fine-grained (charge, collide, link distance, radial forces, custom constraints) | Preset physics engine (Barnes-Hut, Repulsion, ForceAtlas2); configurable but less granular |

---

## Detailed Evaluation

### 1. Ease of Live & Incremental Updates
- **D3.js:** D3's `d3-force` simulation maintains nodes and links as mutable objects. When a new PXP board entry (`BoardEntry`) arrives over the WebSocket, adding the node and restarting the simulation with a low `alpha` parameter smoothly animates new agent turns into position without disrupting existing nodes.
- **Vis.js:** Vis.js uses a `DataSet` model. Calling `nodes.add()` works incrementally, but re-triggering physics stabilization on new nodes can cause existing node positions to jump unexpectedly unless physics are frozen.

### 2. Styling Flexibility for PXP Tag Colors
- **D3.js:** Uses native SVG markup. We can apply distinct CSS classes or inline SVG fills for PXP tags:
  - `RATIFY`: Emerald Green (`#10b981`)
  - `REVISE`: Indigo Blue (`#6366f1`)
  - `REFUTE`: Amber/Orange (`#f59e0b`)
  - `REJECT`: Rose Red (`#ef4444`)
  - `PROPOSE`: Purple (`#8b5cf6`)
  SVG filters allow drop-shadow glows, pulsing status rings, and crisp zoom scaling at any DPI.
- **Vis.js:** Rendered inside an HTML5 `<canvas>`. Custom badge shapes, multi-line typography, and glow effects require overriding Canvas 2D context drawing methods (`ctx.arc`, `ctx.fillStyle`), which is more verbose and harder to style cleanly.

### 3. Bundle Size & Overhead
- **D3.js:** Extremely modular. By importing only `d3-force`, `d3-selection`, `d3-zoom`, and `d3-drag`, tree-shaking keeps the bundle addition minimal (~15–20 kB gzipped).
- **Vis.js:** `vis-network` brings a large monolithic bundle (~300+ kB uncompressed) with bundled polyfills and custom canvas render engines.

### 4. React Integration Story
- **D3.js:** React owns the `<svg>` DOM node via `useRef`, while D3 handles the physics simulation math and coordinate calculations. This separation allows React state updates (e.g., node selection, hover states, filter toggles) to remain pure and reactive.
- **Vis.js:** Operates completely imperatively inside a `div` ref. Synchronizing React component state with canvas click events requires event listeners (`network.on('click')`) and manually querying internal network instance methods.

---

## Final Recommendation & Justification

**Final Pick: D3.js (`d3-force` with SVG rendering)**

> **Justification:** D3.js is selected as the primary graph visualization library for the Intelligible Blackboard UI because its vector SVG rendering model provides superior styling flexibility for PXP tag color coding, crisp typography, and glow effects without the complexity of canvas 2D rendering callbacks. Furthermore, D3's modular `d3-force` engine allows fine-grained control over incremental node placement as new agent entries stream in over WebSockets, cleanly separating physics math from React's state-driven UI lifecycle while maintaining a minimal bundle footprint.
