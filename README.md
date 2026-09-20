# Intelligible-Blackboard-Architecture-with-Counterfactual-Agents

## PXP Blackboard

## Environment Setup & Team Guidelines

This README covers the development environment, local LLM setup, repository layout, and team conventions for working on PXP Blackboard.

---

## 1. Development Environment

### Recommended: Develop locally

Each teammate should run the blackboard, agents, and UI on their own machine and work from the shared GitHub repository.

| Environment | Guidance |
|---|---|
| **Local machine** | Recommended for daily development and demos. Use Ollama to run the agreed local model. |
| **Google Colab** | Use only for one-off experiments, such as testing a prompt. Colab runtimes can disconnect or recycle, so it is not suitable for the persistent server, scheduler, or stateful agents. |
| **Shared cloud VM** | Optional for the Week 3 ablation batch if local runs are too slow. A teammate with suitable GPU access may run the benchmark locally instead. |

### Team environment agreement

Before development gets underway, agree on and document:

- Python version
- Node.js version (for the frontend)
- Ollama model name and exact tag
- Model quantization, where applicable
- Required environment variables and default ports

Keep these choices consistent across machines to reduce setup issues and make benchmark results comparable.

---

## 2. Local LLM Setup

For the current Student 3 setup, use the pinned model
`qwen3:4b-instruct-2507-q4_K_M` and follow the
[local inference guide](docs/local-inference.md) for chat, GPU checks,
latency measurement, and the Day 2 harness.

### Ollama (team default)

Ollama is the recommended local runtime for macOS, Windows, and Linux.

1. Install Ollama:
   - **Linux:**  
     ```bash
     curl -fsSL https://ollama.com/install.sh | sh
     ```
   - **macOS / Windows:** Download and install it from [ollama.com](https://ollama.com).
2. Pull the shared model:
   ```bash
   ollama pull qwen3:4b-instruct-2507-q4_K_M
   ```
3. Ollama serves its local API at:
   ```text
   http://localhost:11434
   ```
4. Configure the agent wrapper to use the local Ollama endpoint.
5. Confirm that all teammates are using the same model identifier and tag.

> **Important:** Choose one model as a team and pin its exact name/tag and quantization in `.env.example` and this README. Do not silently switch models during comparable experiments.

### Optional: vLLM

Use vLLM only if a teammate has an NVIDIA GPU and needs higher throughput for the Week 3 benchmark batch. It generally requires Linux and compatible NVIDIA/CUDA setup, so it is not the default for daily development.

Example:

```bash
pip install vllm
vllm serve mistralai/Mistral-7B-Instruct-v0.3
```

---

## 3. Repository Structure

Use a single monorepo, organized around the team's responsibility boundaries.

```text
pxp-blackboard/
├── blackboard/          # Student 1 — schema, store, scheduler
│   ├── models.py        # PXP tags and shared message contract
│   ├── store.py
│   ├── core.py
│   └── scheduler.py
├── agents/              # Student 2 — LLM wrappers, prompts, sandbox
│   ├── llm_client.py
│   ├── prompts/
│   └── counterfactual.py
├── ui/                  # Student 3 — interface
│   ├── server/          # FastAPI + WebSocket
│   └── frontend/        # React + D3/Vis.js
├── bench/               # Student 3 — dataset ingest and evaluation
│   ├── ingest/
│   └── metrics/
├── tests/
├── docs/
│   └── pxp-message-contract.md
├── .env.example
├── .gitignore
├── requirements.txt     # or pyproject.toml
├── docker-compose.yml   # optional
└── README.md
```

### Shared contract: `blackboard/models.py`

All three teammates depend on `blackboard/models.py`. Treat it as the shared interface between components.

- Freeze the message schema around **Week 1, Days 3–4**.
- Document the agreed contract in `docs/pxp-message-contract.md`.
- Any change after the freeze must be discussed with both teammates before merging.
- PRs that modify `models.py` must explicitly tag both other teammates for review.

---

## 4. Getting Started

### Prerequisites

Install:

- Git
- The agreed Python version
- Node.js and npm (for the React frontend)
- Ollama and the agreed model

### Clone the repository

```bash
git clone <REPOSITORY_URL>
cd pxp-blackboard
```

### Set up Python

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

If the project uses `pyproject.toml` instead of `requirements.txt`, follow the install command documented there.

### Configure environment variables

```bash
cp .env.example .env
```

On Windows, copy `.env.example` to `.env` using your file manager or PowerShell.

Fill in local paths, ports, and model settings as required. **Never commit `.env` or secrets.** Keep `.env.example` updated with placeholder values only.

Example settings to document in `.env.example`:

```dotenv
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=<team-agreed-model-tag>
```

### Run the system

Commands below assume the repository uses the paths shown in the structure above. Adjust them if the implementation differs.

```bash
# Backend
uvicorn ui.server.main:app --reload
```

In another terminal:

```bash
# Frontend
cd ui/frontend
npm install
npm run dev
```

Run the core sanity-check demo, if present:

```bash
python demo.py
```

---

## 5. Git and Pull Request Guidelines

### Branching

- Keep `main` runnable at all times.
- Create a branch for each feature or fix.
- Do not push directly to `main`.
- Open a pull request (PR) and get at least **one teammate review** before merging.

Suggested branch names:

```text
infra/scheduler
agents/rollback-sandbox
ui/graph-view
```

### Commits

- Make small, focused, frequent commits.
- Use clear commit messages that describe the change.
- Avoid one large end-of-week dump.

### Pull requests

Every PR should:

- Explain what changed and why.
- Link the relevant milestone or sprint task.
- Mention how the change was tested.
- Call out any interface, environment, or dependency changes.
- Tag both other teammates if `blackboard/models.py` changes.

---

## 6. Team Coordination

- **Daily async check-in:** Post what you completed, what you plan next, and any blockers in the team's Slack/Discord thread.
- **Schema freeze:** Hold a short team sync around Week 1, Days 3–4 to agree on `blackboard/models.py`.
- **Data-contract review:** Schedule the Day 10 retrospection/data-contract review on the shared calendar.
- **Weekly demo:** Demo progress every Friday, even if the implementation is still rough.
- **Cross-boundary changes:** Discuss changes that affect another teammate's interface before merging them.

---

## 7. Week 1 Setup Checklist

- [ ] Agree on Python and Node.js versions.
- [ ] Select and pin the shared Ollama model/tag and quantization.
- [ ] Create the monorepo and confirm ownership boundaries.
- [ ] Add `.env.example` and ensure `.env` is gitignored.
- [ ] Confirm every teammate can install dependencies and run the core demo.
- [ ] Agree on branch naming, PR review, and commit conventions.
- [ ] Freeze `blackboard/models.py` and document the contract.
- [ ] Schedule the Day 10 review and Friday demos.

---

*PXP Blackboard — Team working guide*
