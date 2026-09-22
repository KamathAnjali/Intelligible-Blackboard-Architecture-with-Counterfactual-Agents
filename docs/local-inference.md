# Student 3: local inference, Days 1 through 3

Use **Windows PowerShell** on Dhruva's laptop. Ollama runs natively on Windows
at `http://127.0.0.1:11434`. WSL is not needed. GNU Make is already installed
on Dhruva's laptop. The Windows Makefile explicitly invokes PowerShell so scripts
execute instead of opening in Notepad. The harness requires Python 3.10+ and
uses the repo's Pydantic dependency for Day 3 validation. Set up dependencies
once from the repo root (the runner automatically uses this virtual environment):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run `make help` for available commands.

You can also run these Make targets from WSL while in the repo under
`/mnt/c/Users/user/Desktop/AI/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents`.
The Makefile uses WSL's shell for quoting and invokes `powershell.exe` through
Windows interoperability. The script still runs Windows Python and connects to
Windows Ollama, so no separate WSL server or network configuration is needed.
Direct `python3 -m agents.llm_client` inside WSL is a separate Linux client and
does not use this bridge. On macOS, use the Python commands below instead.

## 1. Query the model

```powershell
cd "$HOME\Desktop\AI\Intelligible-Blackboard-Architecture-with-Counterfactual-Agents"
make start
make chat
```

Ask questions normally. Enter `/set parameter num_ctx 4096` to match the
harness context size and `/bye` to leave chat. The Makefile sets execution policy
only for its PowerShell process; no persistent policy change is needed.

For a single request through the Python harness:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 query -Prompt "What is a shared blackboard in a multi-agent system?"
```

You can also use `ollama run qwen3:4b-instruct-2507-q4_K_M` directly in a fresh
PowerShell window. The runner locates the usual install path if an older
terminal has not picked up the updated PATH. All teammates should use this
exact model tag. Loaded memory usage is higher than its 2.5 GB download.

## 2. Verify GPU inference

After enabling the RX 6800S, connect power, quit Ollama from its tray menu,
and reopen it so it discovers the GPU. Then run:

```powershell
make query
make gpu
```

The query keeps the model loaded for ten minutes. If the model table is empty,
run a query again. In `ollama ps`, `100% GPU` means the model is fully loaded
on GPU; `100% CPU` means CPU inference; mixed `CPU/GPU` means partial offload.
These are model placement percentages, not live GPU utilization.

The table does not identify which GPU is selected. Check the latest inference
compute/device and offload lines in the printed Ollama log for the RX 6800S.
Discovery lines alone only prove detection. During a request, Task Manager's
compute activity and dedicated memory for the RX 6800S provide additional
evidence; its default 3D chart may not reflect inference activity.

Vulkan is enabled by default when its backend is installed; `GGML_VULKAN=1`
is not needed. If CPU inference persists, verify Windows lists the RX 6800S,
update its AMD Windows driver, restart Ollama, and inspect the latest log.
If device selection is needed, set `GGML_VK_VISIBLE_DEVICES` on the Ollama
server to the discrete GPU index reported in its logs, then restart Ollama.
Do not assume the index is 0 or 1.

Sources: [GPU support](https://docs.ollama.com/gpu),
[model placement and Windows server settings](https://docs.ollama.com/faq).

## 3. Measure latency

Close other model clients and run:

```powershell
make latency RUNS=3
make gpu
```

`make latency` defaults to three warm runs. Change `RUNS` to an integer from 1
to 100, for example `make latency RUNS=5`.

This unloads this model, sends one first request, then three identical warm
requests. The report under `results/ollama/` records raw responses, timings,
token counts, model digest from the loaded model list, Ollama version, and
settings. Results are ignored by Git.

- `wall_seconds`: time your client waits for the complete response.
- `load_seconds`: server-reported model loading time.
- `prompt_seconds`: prompt processing time.
- `tokens_per_second`: output tokens divided by generation time.
- Warm median/min/max: typical latency and observed range in this short test.

The first request includes reloading, but OS file caches may still be warm.
Repeated prompts may benefit from Ollama's prompt cache. This measures full
response latency, not time to first token. `done_reason: length` means the
response hit the output cap. Settings: 4096 context tokens, temperature 0,
seed 42, maximum 256 output tokens. Seed and temperature do not guarantee
identical answers across hardware.

Run once on CPU and repeat after confirming GPU placement. Compare the same
laptop and settings. Different laptops' latency is not evidence of reasoning
quality. This is a setup check, not the project's benchmark evaluation.
Source: [Ollama timing fields](https://docs.ollama.com/api/generate).

## 4. Day 2 harness and PEX prompt

`agents/llm_client.py` exposes `OllamaClient.generate(prompt, system="")`.
Its returned dictionary contains raw text in `response` plus timing metadata.
`agents/prompts/pex_template.txt` requests JSON with string fields
`prediction` and `explanation`. Run the five handwritten tasks:

```powershell
make samples
```

The prompt alone requests JSON, so formatting failures remain visible.
The saved report logs invalid JSON, missing/extra fields, empty or incorrectly
typed values, wrong expected answers, and truncation. Failed sample checks
exit with code 1 after saving the report. Inspect all explanations for faulty
reasoning, unsupported claims, and irrelevance. Each record starts with
`explanation_review: pending`; write your observations in the failure log.
Five simple cases are a smoke test, not proof of benchmark quality.

Day 3 adds a separate validated call, described below; Day 5 maps output into
`BoardEntry`. The client currently uses defaults or explicit CLI options;
it does not read `.env`. Context size is supplied as API `options.num_ctx`;
`OLLAMA_NUM_CTX` is not an Ollama server setting.

Mac teammates can run the same Python harness from the repo root:

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama run qwen3:4b-instruct-2507-q4_K_M
python3 -m agents.llm_client query --prompt "Explain a shared blackboard."
python3 -m agents.llm_client latency --runs 3
ollama ps
python3 -m agents.llm_client samples
```

On Windows, `make stop` unloads the model to free memory while leaving
the Ollama server available.

## 5. Day 3: validated PEX and persona prompts

```powershell
make pex
make pex PERSONA=aggressive_proposer RETRIES=2
```

For a custom task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 pex -Persona cautious_verifier -Prompt "All tulips are plants. This is a tulip. Is it a plant?"
```

`OllamaClient.generate_pex(prompt, persona=None, max_retries=2)` sends the
Pydantic-generated JSON schema to Ollama and validates the returned text locally.
Exactly two nonempty string fields are accepted: `prediction` and `explanation`.
Extra fields, wrong types, malformed JSON, and incomplete/truncated responses
are rejected. Surrounding whitespace in field values is trimmed.

The default budget is one initial call plus two retries. Each retry preserves
the task and persona and adds feedback describing the formatting error.
Connection/server errors propagate immediately. A schema-valid answer may still
be factually wrong; answer evaluation remains separate from format validation.

Successful calls return `parsed`, final `raw`, and `attempts` (including raw
responses and timings for failed attempts). Exhaustion raises `PEXGenerationError`
with the attempts attached. The `pex` command saves a report for success or
validation exhaustion to `results/ollama/`; retries therefore remain visible
when counting tokens and measuring cost. No model output is posted to the board.

The two draft personas are `cautious_verifier` (check evidence and uncertainty)
and `aggressive_proposer` (propose and test a concrete answer). Both use the same
PEX output contract and require evidence-grounded answers. Persona testing on
three tasks each and review of the frozen BoardEntry contract are Day 4 work.

Run the offline tests, including malformed-output recovery and retry exhaustion:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

On macOS, install requirements into a virtual environment and use
`python3 -m agents.llm_client pex --persona aggressive_proposer --retries 2`.
