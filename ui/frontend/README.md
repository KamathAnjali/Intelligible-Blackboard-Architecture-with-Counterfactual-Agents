# Intelligible Blackboard — UI Frontend

React + Vite + TypeScript frontend for graph visualization of the Intelligible Blackboard multi-agent reasoning flow.

## Prerequisites

- Node.js (v18+ recommended)
- npm (v9+ recommended)

## Setup & Running

1. **Install dependencies:**
   ```bash
   npm install
   ```

2. **Start the Vite development server:**
   ```bash
   npm run dev
   ```

3. **Build for production:**
   ```bash
   npm run build
   ```

## Features

- **`<Canvas />` Component:** Full-viewport SVG visualizer with scalable grid pattern, prepared for node-graph layout rendering.
- **WebSocket Auto-Connect:** Connects to `ws://localhost:8000/ws` backend endpoint with real-time status indicator.
