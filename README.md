# Intelligible Blackboard Architecture with Counterfactual Agents

This project investigates whether multi-agent reasoning over a shared blackboard architecture—augmented with PXP (Prediction-Explanation Protocol) and retrospective counterfactual rollback—enables autonomous agents to resolve deadlocks, avoid hallucinated consensus, and achieve verifiable, intelligible outcomes.

---

## 1. Workstream Overview & Team Ownership

The project integrates three core workstreams into a unified execution pipeline:

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
  - Local LLM inference integration (Ollama / `qwen3:4b-instruct-2507-q4_K_M` / `mistral:7b-instruct`).
  - Library of 4 distinct reasoning personas: `aggressive_proposer`, `cautious_verifier`, `evidence_auditor`, `counterexample_challenger`.
  - Strict PEX/PXP grammatical parsing, schema validation, and automatic retries with model self-checks.
  - Multi-agent conversation loop driving consensus and structured disagreement resolution.

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
│ - Node & edge sync│ │  - delta_score eval  │ │ - export_replay      │
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
# or
ollama pull mistral:7b-instruct
```

---

## 4. Running Demos & Test Suite

### 1. Run Complete Offline Integration Demo (No LLM Required)
```bash
python demo.py
```
Demonstrates:
- 3-agent convergence reaching `ULTRA_STRONG` consensus.
- Deadlock detection with negative tag streak.
- Isolated counterfactual sandbox forking and delta score evaluation.

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
