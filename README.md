# Intelligible Blackboard with Counterfactual Agents

This project studies whether agents sharing predictions and explanations can reach correct, intelligible answers. Contributions use four PXP tags: `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`. The planned counterfactual mechanism will explore altered earlier contributions in an isolated replay before updating a live session.

This branch combines the four workstreams:

| Source branch | Integrated contribution |
| --- | --- |
| `1-Anjali` | Board contracts, synchronized state, events, consensus classification, snapshots, history slicing, and isolated board forks. |
| `2-Lopez` | Event-driven round-robin scheduling, turn ownership checks, and terminal-state handling. |
| `3-Dhruva` | Local Ollama inference, four personas, structured output validation, retries, explanation self-checks, conversations, and published run records. |
| `4-Tanisha` | FastAPI/WebSocket backend, React/D3 interface, replay, task ingestion, token estimates, and benchmark infrastructure. |

See the [progress report](docs/PROGRESS_REPORT.md) for implemented behavior and the [milestones](docs/PROJECT_MILESTONES.md) for planned work. Official benchmark evaluation remains pending.

`agents.counterfactual.RetrospectiveSandbox` can generate an alternative and replay later turns on an isolated board. Its agreement-progress delta does not measure answer correctness. The opt-in conversation recovery path provides an accepted alternative as unverified guidance on a new live turn.

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

To enable retrospective recovery during a conversation from WSL, run:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& .\.venv\Scripts\python.exe -m agents.conversation --turns 12 --counterfactual-fraction 1 --retrospective-recovery'
```

Recovery requires a deadlock, an eligible earlier proposal, and an isolated replay that reaches consensus with a positive agreement delta. It is opt-in and is not yet enabled by the KramaBench adapter.

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
| `make deadlock-ab AB_TURNS=10 RETRIES=1` | Compare the same injected deadlock with recovery off and on; save transcripts and a summary under `results/sessions/`. |
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

For a standard blackboard baseline on the five KramaBench tuning tasks, start
Windows Ollama, then in WSL run `bash bench/baseline_pilot.sh` (optional:
`TURNS=12 bash bench/baseline_pilot.sh`). This uses the no-key compatibility
evaluator, disables counterfactuals and saves individual run records under
`~/blackboard-baseline-*`. The launcher uses the evaluator environment above for
preflight so XLSX and PDF parsers are available. Results can include
unfinished tasks; they are not full official benchmark scores. Run `make stop`
afterwards to unload the model.

The no-key runner accepts 76 tasks whose pinned inputs passed preflight.
`bash bench/baseline_pilot.sh --list-tasks` prints their `workload:task-id`
pairs without connecting to Ollama. Pass selected pairs as positional arguments
to the launcher when ready to run them.

The local client requests a 16,384-token context. Oversized prompts may still
be truncated by Ollama; run manifests record the actual model settings.
The default pilot scope uses a 16,384-token context. The original 4,096-token
scope remains available for reference; results from the two settings are separate.

`python -m bench.monitor_runs <results-directory>` reports task outcomes,
answer scores, errors, and counterfactual activity from saved manifests and
transcripts. The paired orchestrator uses retrospective recovery and refuses
to run if its frozen model settings differ from the local client.

### Colab model comparison

Select an NVIDIA GPU runtime in Colab. Clone the `3-Dhruva` branch, then
run these commands from the repository root in separate notebook cells:

```sh
bash bench/colab_pilot.sh setup
bash bench/colab_pilot.sh run qwen3:8b
bash bench/colab_pilot.sh run qwen3:14b-q4_K_M
```

Setup checks the pinned data and runs offline checks without model weights.
Each run downloads its model into the Colab VM, verifies full GPU placement
at the 16,384-token context, and runs the same five tuning tasks with
counterfactual recovery disabled. Each result folder under
`/content/blackboard-results` includes `summary.jsonl`, model and GPU
metadata, and task records. Download the results before resetting the VM.

### MATH-500 recovery pilot

The text-only pilot sends pinned integer-answer problems directly to the model and the blackboard. It runs the first 12 by default, with 24 available. Each problem has one solo answer and four six-agent runs with 0, 2, 3, or 4 agents eligible for retrospective recovery (0%, 33%, 50%, and 66%, rounded labels).

```sh
bash bench/colab_pilot.sh math-setup
MATH_TASKS=3 bash bench/colab_pilot.sh math-run qwen3:8b  # optional smoke run
bash bench/colab_pilot.sh math-run qwen3:8b
bash bench/colab_pilot.sh math-run qwen3:14b-q4_K_M
```

Setup verifies the dataset and runs offline checks. To see live output in a Colab cell, use `!env MATH_TASKS=3 bash /content/blackboard/bench/colab_pilot.sh math-run qwen3:8b`. Results under `/content/blackboard-results/math500` include `progress.log`, per-run scores in `summary.jsonl`, `summary.json`, and transcripts.

Set `MATH_TASKS=24` for the extended set or use `MATH_START` (one-based) and `MATH_TASKS` to select a batch. Set `COLAB_PILOT_RESULTS` to a directory under mounted Google Drive to save scores and transcripts across Colab runtimes. Each invocation creates a separate results directory.

For a targeted follow-up, run `bash bench/colab_pilot.sh math-run qwen3:14b-q4_K_M --deadlocks-from /path/to/prior/summary.jsonl`. Pass all original summaries to select the union of their deadlocked problems. Nonzero settings give the first proposer replay eligibility, and the summary records replay sources, skip reasons, and whether a replay was applied.

Set `PILOT_EXPLANATION_REVIEW=0` in the Colab launcher for either pilot, or pass `--no-explanation-review` to the Python conversation or pilot command. This disables model review before posting in live and simulated turns. Entries still follow the PXP schema, agents review each other's claims on the board, and the saved results record the setting.

Accuracy uses the final unanimous prediction, counts unresolved attempts as incorrect, and tracks earlier PXP tag warnings separately. To regrade a saved run with its transcripts, use `python -m bench.math_pilot --rescore <run-directory>` from the repository root with the math environment active.

### CounterBench pilot

The pilot runs five published [CounterBench questions](https://huggingface.co/datasets/CounterBench/CounterBench/tree/c6225dfa00b7a89ccfeae67797b9a6d80e48e44c), one per question type. Each question uses the same three agents with recovery off and with all three eligible. The model sees the supplied causal facts and question. Final predictions are scored against the dataset's yes/no answers after inference.

```sh
make counterbench-setup  # Download and verify both datasets without inference
make counterbench        # Run the ten conversations using local Ollama
```

Start Windows Ollama before the live command. The initial limit is 10 scheduled turns and 2 retries per turn. Results under `results/counterbench/` include transcripts, per-run scores, and a summary of correct consensus, deadlocks, replay attempts, and model usage.

On a Colab NVIDIA GPU runtime, run these from the `3-Dhruva` checkout:

```sh
bash bench/colab_pilot.sh counterbench-setup
bash bench/colab_pilot.sh counterbench-run
```

The Colab launcher defaults to the same Qwen3 4B model as the local pilot. It also accepts `qwen3:8b` or `qwen3:14b-q4_K_M` after `counterbench-run`. The launcher verifies GPU placement and records the Git commit, transcripts, and scores under `/content/blackboard-results/counterbench/`.
