# Intelligible Blackboard with Counterfactual Agents

This project studies whether agents sharing predictions and explanations can reach correct, intelligible answers. Contributions use four PXP tags: `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`. The planned counterfactual mechanism will explore altered earlier contributions in an isolated replay before updating a live session.

This branch combines the four workstreams:

| Source branch | Integrated contribution |
| --- | --- |
| `1-Anjali` | Board contracts, synchronized state, events, consensus classification, snapshots, history slicing, and isolated board forks. |
| `2-Lopez` | Event-driven round-robin scheduling, turn ownership checks, and terminal-state handling. |
| `3-Dhruva` | Local Ollama inference, four personas, structured output validation, retries, explanation self-checks, conversations, and published run records. |
| `4-Tanisha` | FastAPI/WebSocket backend, React/D3 interface, replay, task ingestion, token estimates, and benchmark infrastructure. |

See the [progress report](docs/PROGRESS_REPORT.md) for implemented behavior and the [milestones](docs/PROJECT_MILESTONES.md) for planned work. Counterfactual scoring, live adoption, and official benchmark evaluation remain pending.

`agents.counterfactual.RetrospectiveSandbox` can generate an alternative and replay later turns on an isolated board. Its agreement-progress delta does not measure answer correctness; live deadlock handoff and adoption remain pending.

## Setup from WSL

Use the project checkout. For a new checkout of this branch:

```sh
git clone --branch 3-Dhruva https://github.com/KamathAnjali/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents.git
cd Intelligible-Blackboard-Architecture-with-Counterfactual-Agents
```

The existing Make recipes run Windows PowerShell, Windows Python, and Windows Ollama from WSL. Install GNU Make in WSL and Python 3.10+, Ollama, and Node.js/npm on Windows. Run from the repository root:

```sh
sudo apt install -y make
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'py -3 -m venv .venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r ui/server/requirements.txt httpx'
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'npm.cmd --prefix ui/frontend ci'
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen3:4b-instruct-2507-q4_K_M'
```

The Ollama command uses its default Windows installation path. The frontend's supported Node versions are specified in its lockfile. Native macOS/Linux environments can install the same dependencies and invoke the Python modules and npm directly.

## Local agent commands

```sh
make start
make conversation TURNS=6 RETRIES=2
make disagreement TURNS=6 RETRIES=2
```

`conversation` runs two agents on the default task. `disagreement` registers a proposer, evidence auditor, and counterexample challenger against a marked synthetic incorrect claim; subsequent responses come from the model. Its check requires correction to the expected answer and agreement by all three agents.

| Recipe | Purpose |
| --- | --- |
| `make help` | List commands. |
| `make start` / `make stop` | Start Ollama / unload the model. |
| `make chat` / `make query` | Interactive chat / one prompt. |
| `make gpu` | Display model placement and device logs. |
| `make latency RUNS=5` | Measure load and warm-request latency. |
| `make samples` | Run five structured PEX examples. |
| `make pex PERSONA=evidence_auditor RETRIES=2` | Generate a validated prediction and explanation. |
| `make entry PERSONA=counterexample_challenger` | Generate a BoardEntry without posting it. |
| `make conversation TURNS=6 RETRIES=2` | Run a bounded two-agent session. |
| `make conversation AGENTS=3 TURNS=6 RETRIES=2` | Run an unseeded three-agent session. |
| `make sessions TURNS=6 RETRIES=2` | Run the four-case three-agent sanity workload. |
| `make deadlocks` | Produce two controlled deadlock fixtures without model calls. |
| `make disagreement TURNS=6 RETRIES=2` | Run the three-agent scenario; turns must be 5-12. |
| `make demo` | Check the existing three-turn live sequence. |
| `make personas` / `make test` | Run existing live persona checks / offline checks. |

`PERSONA` also accepts `aggressive_proposer` and `cautious_verifier`. General conversations allow 1-12 turns; retries allow 0-5. A valid candidate receives a separate explanation review using the same model. Generation and review usage are retained in the transcript.

## UI and backend

Start the backend from WSL in one terminal:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '$env:BOARD_MODE="LIVE_TAP"; & .\.venv\Scripts\python.exe -m uvicorn ui.server.main:app --port 8000'
```

Start the frontend in another terminal and open `http://localhost:5173`:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'npm.cmd --prefix ui/frontend run dev'
```

To start a conversation owned by this backend, use a third terminal:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/conversation/start -ContentType "application/json" -Body (@{scenario="disagreement"; turns=6; retries=2} | ConvertTo-Json)'
```

The standalone Make conversation runs in a separate process. The backend endpoint runs the conversation whose actual board events are sent to the UI. Omitting `BOARD_MODE` starts recorded replay using `bench/data/recorded_session.json`, which is a synthetic fixture. The UI's token panel estimates board-entry text; full inference usage is recorded separately in transcripts.

The UI also has a frozen-scope KramaBench picker at `GET /api/tasks` and `POST /api/tasks/run`. To enable genuine task runs, start the backend with `BOARD_MODE=LIVE_TAP KRAMA_UI_APPROVE_GENUINE_RUN=1`; configure `KRAMABENCH_ROOT`, `KRAMABENCH_EVALUATOR_PYTHON`, `OLLAMA_BASE_URL`, and `KRAMA_UI_OUTPUT_ROOT` as needed. Only one task can run per server process. See [`ui/server/README.md`](ui/server/README.md) and [the UI event contract](docs/contracts/ui_events.md) for score withholding, CORS, archive behavior, and endpoint details.

## Saved records and benchmarks

Published records are tracked in `results/transcripts/<conversation-id>.json` and `results/snapshots/<conversation-id>.json`. Two partial runs have recovered snapshots; their original empty files are retained in `results/snapshots/originals/`. Raw records preserve the observed model responses and do not establish answer correctness.

New runs still write to `results/conversations/`; inference reports use `results/ollama/`. These generated directories, benchmark results, and local raw datasets remain ignored. Existing files are preserved when records are copied into the published archive.

`bench/ingest/` loads task records with a gold-free agent view and a separate evaluator reference. The default parser sample is a format sample, not benchmark data; run `./.venv/bin/python -m bench.ingest.krama_parser --report` to save its load report. `bench/metrics/trial_runner.py` supports only `example` and `dry_run` fixtures; their outputs are explicitly named `SYNTHETIC` under `bench/results/synthetic/` and are not benchmark evidence. Real paired KramaBench execution is managed by `bench.orchestrator` against the hash-verified scope. Start with `./.venv/bin/python -m bench.orchestrator plan`; real `dry-run`/`pilot` commands require `--approve-genuine-run` and paths supplied by flags or environment. `bench/run_benchmark_chunk.py` was retired in favor of the paired orchestrator.
# KramaBench checkout setup

The runner expects the pinned KramaBench revision at `$HOME/kramabench-pinned` and
the evaluator environment at `$HOME/kramabench-eval-venv`. Recreate both with:

```sh
bench/setup_krama_checkout.sh
```

The script refuses to overwrite either path, verifies the pinned revision and
all allowlisted input hashes, and leaves the checkout clean. The reviewed
compatibility evaluator patch is stored in `bench/kramabench_runner.py` and is
applied automatically to an isolated staged copy for each compatibility run;
the pinned checkout is not patched.
