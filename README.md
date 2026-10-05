# Intelligible Blackboard Architecture with Counterfactual Agents

This project investigates whether multi-agent reasoning over a shared blackboard architecture—augmented with PXP (Prediction-Explanation Protocol) and retrospective counterfactual rollback—enables autonomous agents to resolve deadlocks, avoid hallucinated consensus, and achieve verifiable, intelligible outcomes.

---

## 1. Workstream Overview & Team Ownership

The project integrates four core workstreams into a unified execution pipeline:

- **Student 1 (Data Model & Storage - `1-Anjali`)**:
  - Central `Blackboard` state engine with thread-safe `RLock` synchronization.
  - PXP message schema contracts (`BoardEntry`, `AgentRecord`, `BlackboardState`, `PXPTag`, `BoardEvent`).
  - In-memory storage with JSON disk snapshotting and replay trace export (`InMemoryJSONStore`).
  - Real-time Board Event Emitter hooks for WebSocket streaming.
  - Refined unanimous Intelligibility Classification (`STRONG` vs `ULTRA_STRONG`) with anti-impersonation logic.
  - Retrospective rollback history slicing and isolated simulation sandbox forking.

- **Student 2 (Protocol & Scheduler - `2-Lopez`)**:
  - Deterministic round-robin turn management (`Scheduler.next_agent()`).
  - Strict turn ownership enforcement and out-of-turn submission rejection (`Scheduler.submit_entry()`).
  - Terminal state transitions (`UNRESOLVED`, `STRONG`, `ULTRA_STRONG`, `DEADLOCKED`).
  - Live session stop criteria and registration synchronization.

- **Student 3 (Agents & Counterfactual Reasoning - `3-Dhruva`)**:
  - Local LLM inference integration using the project-wide Ollama model `qwen3:4b-instruct-2507-q4_K_M`.
  - Library of 4 distinct reasoning personas: `aggressive_proposer`, `cautious_verifier`, `evidence_auditor`, `counterexample_challenger`.
  - Strict PEX/PXP grammatical parsing, schema validation, and automatic retries with model self-checks.
  - Multi-agent conversation loop driving consensus and structured disagreement resolution.

- **Student 4 (UI & Benchmarking - `4-Tanisha`)**:
  - FastAPI WebSocket event stream and React/D3 graph interface.
  - KramaBench JSON/JSONL task parser and per-turn token estimates.
  - Recorded-session replay and an in-process LIVE_TAP path that streams the actual conversation runner's Blackboard through the WebSocket.
  - Benchmark results: no official KramaBench scores have been produced yet. `example` and `dry_run` are simulations, and `pilot`/`full_study` stay disabled until real agent execution and the official evaluator are connected. See [`bench/results/README.md`](bench/results/README.md).

---

## 2. Integrated Architecture Flow

```text
  [Task Input]
       │
       ▼
 ┌────────────────────────────────────────────────────────┐
 │                 Scheduler (Student 2)                  │
 │   - Round-robin turn selection (next_agent())          │
 │   - Turn ownership enforcement (submit_entry())        │
 └───────────────────────────┬────────────────────────────┘
                             │ Selected Agent Turn
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │               Local LLM Agents (Student 3)             │
 │   - Ollama / local model prompt generation             │
 │   - Personas: Proposer, Verifier, Auditor, Challenger  │
 │   - Strict PXP validation & retry with self-check      │
 └───────────────────────────┬────────────────────────────┘
                             │ Submits BoardEntry
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │             Blackboard Core (Student 1)                │
 │   - Thread-safe RLock mutation                         │
 │   - PXP schema & target entry verification             │
 │   - Unanimous consensus & deadlock streak classifier   │
 └─────┬─────────────────────┬──────────────────────┬─────┘
       │                     │                      │
       │ Emits state deltas  │ Slices/forks sandbox │ Saves state
       ▼                     ▼                      ▼
┌───────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│ Event Emitter Bus │ │ Counterfactual Fork  │ │  InMemoryJSONStore   │
│ (WebSocket / UI)  │ │  (Student 3 Sandbox) │ │ (Snapshots & Replay) │
│ - Real-time stream│ │  - Isolated timeline │ │ - snapshot_to_disk   │
│ - Node & edge sync│ │ - isolated CF fork  │ │ - export_replay      │
└───────────────────┘ └──────────────────────┘ └──────────────────────┘
```

---

## 3. Getting Started & Installation

### Prerequisites
- Python 3.10+ (macOS, Linux, or Windows/WSL)
- [Ollama](https://ollama.com) (for local LLM agent execution)

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/KamathAnjali/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents.git
cd Intelligible-Blackboard-Architecture-with-Counterfactual-Agents

# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Pull Local LLM Model (Ollama)
```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

This is the model used by the Python conversation runner and the Windows helper script. There is no `.env` model override; install this exact Ollama tag before running a live conversation.
The agent generation cap is `768` tokens (`agents/llm_client.py`); repeated live WebSocket smoke calls completed with valid PXP JSON below that cap.

---

## 4. Running Demos & Test Suite

### 1. Run Complete Offline Integration Demo (No LLM Required)
```bash
python demo.py
```
This is a scripted software demonstration, not a live model or benchmark run. It demonstrates:
- Blackboard consensus classification on fixed example entries.
- Deadlock detection with a fixed negative-tag sequence.
- Isolated counterfactual sandbox forking. No counterfactual score evaluator is connected; the demo records a null score.

### 2. Run Local LLM Multi-Agent Conversation (Live Ollama)
```bash
python -m agents.conversation --turns 6 --retries 2
```

### 3. Run Disagreement Resolution Scenario
```bash
python -m agents.conversation --scenario disagreement --turns 6
```

### 4. Run Full Test Suite
```bash
pytest -v
```

### 5. Run the UI replay
Start the backend from the repository root (recorded replay is the default):
```bash
uvicorn ui.server.main:app --reload --port 8000
```
In another terminal, start the frontend:
```bash
cd ui/frontend
npm install
npm run dev
```
Open `http://localhost:5173`. The backend reads `bench/data/recorded_session.json`; the current `PXPTag` enum is `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`.

---

## 5. Repository Structure

```text
pxp_blackboard/
├── blackboard/              # Student 1 & 2 — Data model, store, core, scheduler
│   ├── models.py            # PXP message schema, BoardEvent, BoardEntry
│   ├── store.py             # In-memory dict store with disk snapshotting
│   ├── core.py              # Thread-safe Blackboard core & event emitters
│   └── scheduler.py         # Round-robin turn management & enforcement
├── agents/                  # Student 3 — Local LLM agents, prompts, PXP parser
│   ├── llm_client.py        # Ollama HTTP API client & token accounting
│   ├── conversation.py      # Multi-agent session driver & report checkpointing
│   ├── pxp.py               # PXP response validation contracts
│   ├── pex.py               # PEX schema models
│   └── prompts/             # Persona prompts & task templates
├── architecture/            # Architecture decision records
│   ├── ADR-001-Storage-Architecture.md
│   └── backing-store.md
├── docs/                    # Documentation & Milestone Tracking
│   ├── Schema_Draft.md      # BlackboardState & BoardEntry draft
│   ├── API_USAGE.md         # Public API guide for teammates
│   ├── rollback-contract.md # Retrospective rollback data contract
│   ├── INTEGRATION_REPORT.md# Complete 3-workstream integration report
│   ├── PROGRESS_REPORT.md   # Current project progress report
│   └── PROJECT_MILESTONES.md# Weekly milestone breakdown
├── tests/                   # Automated unit & integration tests
│   ├── test_agent_contract.py
│   ├── test_blackboard.py
│   ├── test_classification.py
│   ├── test_concurrency.py
│   ├── test_conversation.py
│   ├── test_emitter.py
│   ├── test_entry_mapping.py
│   ├── test_integration.py
│   ├── test_llm_client.py
│   ├── test_models.py
│   ├── test_parser.py
│   ├── test_rollback.py
│   ├── test_scheduler.py
│   └── test_store.py
├── demo.py                  # Integration demonstration runner
├── requirements.txt
└── README.md
```
