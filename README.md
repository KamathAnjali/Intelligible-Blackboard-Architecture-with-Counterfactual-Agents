# Intelligible Blackboard with Counterfactual Agents

This project studies whether agents that can revisit earlier decisions help a group reach correct, explainable answers. Agents share a blackboard and post a prediction, explanation, and PXP tag: `RATIFY`, `REVISE`, `REFUTE`, or `REJECT`. The planned counterfactual component will test an alternative earlier contribution in an isolated replay before any change reaches the live session.

The `3-Dhruva` branch runs two local Ollama personas through the scheduler and blackboard. Counterfactual replay, a connected UI, and benchmark evaluation remain future integration work. See the [progress report](docs/PROGRESS_REPORT.md) and [project milestones](docs/PROJECT_MILESTONES.md).

## Run from WSL

Type the commands below in WSL from the repository directory. GNU Make runs in WSL. Each recipe calls Windows PowerShell, which uses the Windows Python environment and Windows Ollama service. This setup does not run inference in Linux WSL.

### One-time setup

Install Make in WSL if needed, then enter the repository:

```sh
sudo apt update
sudo apt install -y make
cd /mnt/c/Users/user/Desktop/AI/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents
```

Create the Windows virtual environment, install Python packages, and download the shared model from WSL through PowerShell:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'py -3 -m venv .venv'
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen3:4b-instruct-2507-q4_K_M'
```

The third command uses Ollama's default Windows install path. If Ollama is installed elsewhere, replace it with the installed `ollama.exe` path. Keep this model tag consistent across the team.

### Start the demo

```sh
make start
sleep 5
make demo
```

The demo expects proposer `REVISE`, verifier `RATIFY`, then proposer `RATIFY`, all with matching predictions and no retries. It exits with an error if the live run does not meet those checks. A normal session uses the default tulip task:

```sh
make conversation TURNS=6 RETRIES=2
```

### Make recipes

Run `make help` to print the available recipes. All recipes and supported variables are listed here:

| Command | Purpose |
| --- | --- |
| `make help` | Print the available recipes. |
| `make start` | Start Windows Ollama if it is not running. |
| `make chat` | Open an interactive model chat. Type `/bye` to exit. |
| `make query` | Send one example prompt. |
| `make gpu` | Show Windows GPU placement and recent Ollama device logs. Run a query first to load the model. |
| `make latency RUNS=5` | Measure one request after unload and five warm requests. |
| `make samples` | Run five PEX sample prompts. |
| `make pex` | Generate a validated prediction and explanation. |
| `make pex PERSONA=aggressive_proposer RETRIES=2` | Choose a persona and retry limit for PEX. |
| `make entry` | Generate a complete BoardEntry without posting it. |
| `make entry PERSONA=cautious_verifier RETRIES=2` | Choose a persona and retry limit for the entry. |
| `make conversation TURNS=6 RETRIES=2` | Run a bounded two-agent conversation. Turns may be 1-12 and retries 0-5. |
| `make demo RETRIES=2` | Run and check the three-turn demo. |
| `make personas` | Run the existing live persona checks on three tasks per persona. |
| `make test` | Run the existing offline test suite. |
| `make stop` | Unload the model from Ollama. |

`PERSONA` accepts `cautious_verifier` or `aggressive_proposer`. `RETRIES` defaults to 2. `RUNS` defaults to 3 for `latency`; `TURNS` defaults to 6 for `conversation`.

For a custom prompt, call the same Windows wrapper from WSL:

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./run.ps1 conversation -Turns 6 -Prompt "Some birds fly. Pip is a bird. Does Pip fly? Use insufficient information, yes, or no as prediction."
```

Conversation transcripts and board snapshots are saved under `results/conversations/`. Ollama reports are saved under `results/ollama/`. Both paths are ignored by Git.

## Code layout

- `blackboard/` contains the PXP contract, synchronized board, JSON snapshot store, and round-robin scheduler.
- `agents/` contains the Ollama client, PEX/PXP validation, persona prompts, and conversation runner.
- `tests/` contains existing checks. `demo.py` is a board-only example.

The board's agreement label is a prototype heuristic. A successful demo checks the interaction path and tag sequence, not accuracy across tasks. The planned study requires independent evaluation of predictions and explanations.
