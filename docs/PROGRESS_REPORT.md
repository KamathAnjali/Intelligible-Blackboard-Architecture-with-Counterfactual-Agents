# Progress report

Updated 19 October 2026 for `integration-main`.

## Integrated sources

All four source branch tips are included through Git merge history. Existing source files, documentation, and data are retained.

| Branch | Included tip | Contribution |
| --- | --- | --- |
| `1-Anjali` | `fb79a7b` | Data contracts, board/storage, events, classification, and sandbox foundations. |
| `2-Lopez` | `9554aae` | Event-driven scheduling, turn enforcement, and scheduler lifecycle. |
| `3-Dhruva` | `6d7b9d8` | Day 9 agent workflows and the conversation archive. |
| `4-Tanisha` | `bc777ba` | UI/backend, ingestion, metrics, and benchmark infrastructure. |

## Implemented behavior

| Area | Current implementation |
| --- | --- |
| Board and storage | Pydantic contracts, synchronized mutations, JSON snapshots, replay export, and event subscriptions. Consensus requires matching RATIFY predictions from every active agent; a four-entry negative streak flags deadlock. |
| Scheduler | Uses the board registry, reserves round-robin turns, rejects submissions from the wrong agent, and stops on terminal board status. Board events can wake the orchestration loop through `wait_for_next_agent()`; the current conversation runner still uses `next_agent()`. |
| Local inference | Shared `qwen3:4b-instruct-2507-q4_K_M` model, context 4096, temperature 0, seed 42, and a 768-token generation cap. |
| Structured agents | Four personas share strict PEX/PXP schemas. A separate model call checks explanation support; malformed or rejected outputs trigger bounded retries before entry creation. |
| Conversations | Two-agent sessions and the three-agent seeded disagreement case submit entries through the scheduler. Reports contain prompts, raw responses, reviews, failures, usage, outcomes, and board checkpoints. |
| UI/backend | FastAPI health and conversation-start endpoints, WebSocket board streaming, React/D3 graph layouts, node inspection, history scrubbing, and estimated entry-text token totals. LIVE_TAP attaches to the backend's in-process conversation board; replay and mock sources remain available. |
| Ingestion and metrics | JSON/JSONL parsing supports the upstream KramaBench record shape. Synthetic trial modes produce CSVs; plotting requires official evaluator provenance. |
| KramaBench adapter and data tools | The pinned `System` adapter reuses PXP and exposes complete allowlisted data through constrained SQLite-backed operations for tabular, JSON, text, HTML, XLSX, and text-based PDF inputs. Pipeline code is validated but never executed. The unmodified evaluator remains key-gated; a separately isolated, explicit no-key compatibility mode is validated synthetically for `environment-easy-1` only. |
| Sandbox foundations | History slices and isolated board forks are implemented. Counterfactual audit records can carry a null score when no evaluator ran. Automated altered-history reasoning, scoring, and live adoption are pending. |

## Published conversation records

Five transcripts and five usable snapshots are tracked under `results/transcripts/` and `results/snapshots/`. Three runs completed with a recorded scheduler-consensus outcome; two contain partial checkpoints and retain their original `running` outcome. Consensus labels do not independently establish answer correctness.

Snapshots for `conversation-20260929T144759406083Z` and `conversation-20260929T144943919595Z` were recovered from the board state embedded in their transcripts. The original zero-byte snapshot files are retained in `results/snapshots/originals/`. All other published records are unchanged copies of their local originals.

The Day 9 records show an initial auditor correction labeled REFUTE, followed by prompt tuning and a repeated five-turn sequence ending at `C: insufficient information`. The two completed disagreement runs retain their original generation and explanation-review evidence.

New conversation checkpoints, Ollama reports, benchmark outputs, and raw dataset clones remain ignored; publication is a separate archive operation.

## Integration verification and remaining work

The merges completed without Git conflicts. Integration checks cover source ancestry, archive integrity, and absence of unresolved conflict markers. No additional runtime changes or runtime tests were performed for this merge.

The UI's token totals are estimates over prediction/explanation text, while transcript usage includes model generation and review calls. The checked-in replay fixture is synthetic. No official KramaBench performance results have been produced: the current trial runner uses synthetic data and keeps empirical modes disabled. The new adapter tests use deterministic fake responses; they are not evaluator scores.

Phase 2.2 regression validation in `.venv` (Python 3.14.0): `env -u RUN_LIVE_OLLAMA_TEST .venv/bin/python -m pytest -q -rs` completed with 146 passed, 18 skipped, and one Starlette/httpx deprecation warning. The skips include one combined XLSX/PDF data-access test because `openpyxl` is missing from `.venv`, plus the 17 existing Ollama-dependent live tests. The focused suite passed 36 tests under the base Python where `openpyxl` and `pdfplumber` are installed. Installing those declared dependencies into `.venv` was blocked by unavailable package-index DNS. Live-model tests and official evaluator execution were not enabled.

Phase 2.2 is blocked at the official Evaluator: it unconditionally constructs `GPTInterface`, which needs a locally configured key even when pipeline evaluation is disabled. The deterministic pinned `success` metric itself was instrumented and made zero GPT/HTTP calls, but that metric check did not run through the Evaluator. Complete input access now uses the constrained data tools; the selected environment CSV parsed as 15,388 rows and 11 columns. A one-task run must wait until a valid key is supplied through secure local environment configuration. Counterfactual replay/scoring/adoption remains later-phase work. The [README](../README.md) contains setup and run commands; the [milestones](PROJECT_MILESTONES.md) retain the planned work.

Phase 2.3 rechecked the exact pinned Evaluator and confirmed that it still raises `OpenAIError` without `OPENAI_API_KEY`, before any HTTP send. A separate temporary copy of the pinned checkout changed only the constructor to defer GPT client creation when pipeline evaluation is disabled. The synthetic fixture passed through that compatibility evaluator, pinned `evaluate.py` and `Executor`, the project adapter and PXP runner, the unchanged official `success` metric, and task/aggregate CSV persistence. It used deterministic fake model responses, recorded zero network attempts, and is not an official evaluator run or benchmark result. The authoritative upstream checkout and project source code were not patched.

The selected `environment-easy-1` task preflights against its complete CSV input and deterministic `numeric_exact` metric, but a genuine run remains blocked by evaluator credentials or explicit acceptance of the compatibility route. The current sandbox also could not verify local Ollama availability. Unsupported-format task IDs and data-tool resource limits are recorded in the [benchmark scoping note](benchmark_scoping_note.md). Phase 2 remains blocked; do not begin Phase 3 scoring until a genuine one-task path has been accepted and exercised.

Phase 2.3 regression checks: `.venv` suite **146 passed, 18 skipped, 1 deprecation warning**; focused data/adapter/preflight/runner suite under base Python **36 passed**; `py_compile` and `git diff --check` passed. XLSX/PDF cases passed under base Python but remain unavailable in `.venv` and the isolated evaluator environment because `openpyxl` and `pdfplumber` are missing. No network installation was retried.

Phase 2.4 kept compatibility mode unimplemented and recommends the unmodified pinned evaluator for comparability. The runner now requires an explicit `--approve-genuine-run`, rejects changes to tracked pinned evaluator sources, checks Ollama health/model tags without generation before creating output, and records distinct evaluator, task, input-hash, prompt/config, and run-status provenance. Re-preflight of `environment-easy-1` confirmed its allowlisted CSV hash, 15,388 rows, 11 columns, and deterministic `success` metric. The selected evaluator Python and CLI preflight pass, but its required `OPENAI_API_KEY` is absent; local Ollama endpoint/model readiness could not be verified from this sandbox. No genuine task or model/API call was made.

Phase 2.4 validation: `.venv` focused benchmark tests **43 passed, 1 skipped** (missing `openpyxl`); base-Python focused tests **44 passed**; full `.venv` suite **154 passed, 18 skipped, 1 deprecation warning**; `py_compile` and `git diff --check` passed. Phase 2 remains **BLOCKED** until credentials and model readiness are available and the user explicitly approves the first genuine task run. See the [benchmark scoping note](benchmark_scoping_note.md) for evaluator trade-offs, exact preflight details, and the unexecuted command.

## Phase 2.5 no-key compatibility path

The user chose not to configure an OpenAI key. The unmodified pinned evaluator still constructs `GPTInterface` even when pipeline evaluation is disabled, so the default `official` path remains unchanged and still requires a key. An explicit `--evaluator-mode compatibility-no-key` path now supports only the pinned `environment-easy-1` task (`numeric_exact` → deterministic `success`), with pipeline evaluation and subtasks disabled. This route does not create an OpenAI client for that evaluator configuration and strips any inherited `OPENAI_API_KEY` from its evaluator subprocess.

The compatibility mode creates a sparse evaluator copy from commit `b2e0d77540263f8b6119f977fe79a8c4386b5a02`; the copy receives only this one-line change in `benchmark/evaluator.py`:

```diff
- self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini")
+ self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini") if evaluate_pipeline else None
```

The binary diff SHA-256 must equal `39dc1ea82ad6d7a75ca3a18de7b3efcbeb58250b307b09c45541286d20394897`. The project checks the exact commit, patch hash, changed-file set, and patched source hash. The original KramaBench checkout is used as the read-only source and its working-tree status was unchanged by the isolated-copy test. Run provenance separates `compatibility_evaluator_output` from `unmodified_official_evaluator_output`; it records both the unmodified evaluator-tree fingerprint and patched evaluator source hash. No automatic fallback occurs, and `--approve-genuine-run` remains required.

The supported task is pinned to its workload and CSV Git blobs, the CSV SHA-256 `911d1ef8bf290494ace9ff1785b5e9789a50de21606aaef8d4b45a7ab695becb`, 15,388 rows, 11 columns, and the single `success` metric. The adapter receives the query and allowlisted data tools; reference answers stay in evaluator context and are absent from agent prompts. Pipeline output is syntax-checked and not executed. Other answer types, tasks, revisions, model tags, pipeline evaluation, and subtasks are rejected.

The synthetic fixture ran the project adapter and PXP conversation, pinned `evaluate.py`/Executor/Evaluator, deterministic `success` metric, and task plus aggregate CSV persistence using fake model responses. Its synthetic success value was `1.0`; this is a fixture assertion only, not a KramaBench score. Six fake generations were observed, zero real Ollama generations ran, and HTTP guards recorded zero network attempts. Reference-answer and reference-pipeline markers were absent from prompts. No genuine task ran, no OpenAI request was attempted, and no benchmark score is claimed. The pinned source emitted an existing Python 3.14 `SyntaxWarning` in `benchmark/llm_tools/prompts.py` during the synthetic fixture.

Phase 2.5 validation: focused KramaBench tests **56 passed, 1 skipped** (`openpyxl` missing); full `.venv` suite **167 passed, 18 skipped, 1 Starlette/httpx deprecation warning**. The 18 skips are 17 Ollama-dependent tests and the XLSX/PDF test requiring `openpyxl`. The standalone compatibility integration fixture passed under `/private/tmp/krama-phase2_1-eval-venv/bin/python`. `py_compile` and `git diff --check` passed after final code and documentation edits. Current non-generative Ollama health/model-list readiness could not be verified from this sandbox; the runner will fail before creating output if the exact local model tag is unavailable.

The future one-task command, to run only after the local Ollama health/model-list check succeeds and the user explicitly approves a genuine call, is:

```sh
.venv/bin/python -m bench.kramabench_runner \
  --kramabench-root /private/tmp/krama-phase2_1-authoritative \
  --workload environment \
  --task-id environment-easy-1 \
  --output-directory /private/tmp/kramabench-phase2_5-results \
  --evaluator-python /private/tmp/krama-phase2_1-eval-venv/bin/python \
  --evaluator-mode compatibility-no-key \
  --model qwen3:4b-instruct-2507-q4_K_M \
  --base-url http://127.0.0.1:11434 \
  --approve-genuine-run
```

This command was not run. Phase 2's compatibility path is ready for a user-approved one-task run once Ollama readiness is verified. Phase 3 remains out of scope and should wait for that genuine task path to complete.
