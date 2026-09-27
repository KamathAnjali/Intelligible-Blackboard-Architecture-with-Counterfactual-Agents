# Intelligible Blackboard with Counterfactual Agents

This project studies whether agents that can revisit an earlier decision help a group reach a correct, explainable answer. Agents share a blackboard and post a prediction, an explanation, and a PXP tag: `RATIFY`, `REVISE`, `REFUTE`, or `REJECT`. The planned counterfactual component will test alternative earlier contributions in an isolated replay before any change reaches the live session.

The current `3-Dhruva` branch runs two local Ollama personas through the scheduler and blackboard. It records every turn and can demonstrate a three-turn agreement sequence. Counterfactual replay, a connected UI, and benchmark evaluation remain future integration work. See [progress](docs/PROGRESS_REPORT.md) and [milestones](docs/PROJECT_MILESTONES.md).

## Run locally

Use Python 3.10 or newer and Ollama. The shared model is `qwen3:4b-instruct-2507-q4_K_M`; all agents in one session use that tag. Start Ollama and pull the model before running inference.

Windows PowerShell setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

With GNU Make in PowerShell or WSL, run:

```text
make start
make demo
make conversation TURNS=6
```

The Make targets use Windows PowerShell, Python, and Ollama. Without Make, invoke the wrapper explicitly from PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 demo
```

On macOS or Linux, start Ollama, then run:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
ollama pull qwen3:4b-instruct-2507-q4_K_M
python -m agents.conversation --demo
```

The direct Python CLI also accepts `--prompt`, `--turns`, and `--retries` for a general conversation.

| Command | Purpose |
| --- | --- |
| `make chat` | Query the model interactively. |
| `make query` / `make pex` / `make entry` | Inspect raw output, validated PEX, or a mapped BoardEntry. |
| `make demo` | Require proposer `REVISE`, verifier `RATIFY`, proposer `RATIFY`, with one valid attempt per turn. |
| `make conversation TURNS=6` | Run a bounded two-agent session on the default task. |
| `make latency RUNS=5` / `make gpu` | Measure local inference or inspect Windows model placement. |
| `make test` | Run the existing offline test suite. |

`make demo` exits with an error if the required sequence does not occur. Each conversation saves a transcript and board snapshot under `results/conversations/`; Ollama reports are under `results/ollama/`. These generated files are ignored by Git. For another task in PowerShell, use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 conversation -Prompt "Your task with supplied facts"`.

## Code layout

- `blackboard/` defines the PXP contract, synchronized board, JSON snapshot store, and round-robin scheduler.
- `agents/` contains the Ollama client, strict PEX/PXP validation, persona prompts, and live conversation runner.
- `tests/` contains existing checks; `demo.py` is a board-only example.

The board's agreement label is a prototype tag heuristic. A successful demo confirms the interaction path and the checked sequence, not answer accuracy across tasks. Predictions and explanations need independent evaluation for the planned study.
