# Benchmark Scoping Note — Proposed Evaluation

**Owner:** Student 4 (UI & Benchmarking)
**Status:** Phase 2 adapter groundwork implemented. The pinned official evaluator and a genuine smoke run remain blocked; no official benchmark result has been produced.

## Dataset and scoring

KramaBench is an end-to-end benchmark for data-science agents. Its records describe tasks and expected outputs; the official evaluator applies metrics according to answer type and supports evaluation of benchmark subtasks/pipelines. It is not a symbolic-logic benchmark. See the [official KramaBench repository](https://github.com/mitdbg/KramaBench) and the parser at [`bench/ingest/krama_parser.py`](../bench/ingest/krama_parser.py).

The parser can load task records, but loading a dataset is not an evaluation. The current trial runner does not call the agent conversation or the official evaluator. Its `example` and `dry_run` modes are simulation/fixture modes, while `pilot` and `full_study` are disabled. No agreement rate from the simulator should be reported as benchmark accuracy.

## Proposed design (not yet executed)

- Use a fixed, documented set of valid KramaBench tasks, recording source revision and task IDs.
- Compare counterfactual participation settings at 0%, 33%, 66%, and 100% on the same tasks.
- Run actual agent turns without exposing reference answers to the agents; score final outputs with the official evaluator.
- Record evaluator metric and score, agent/model/version, prompts and responses or reproducible logs, configuration, and run metadata.
- Report internal agreement, deadlocks, intelligibility, token estimates, and runtime separately from official benchmark scores. Tokens are estimates unless model usage metadata is available; local simulator time is not agent latency.

The proposed 20–30 tasks per setting and 4-task dry run are planning targets, not completed runs. See [`bench/results/README.md`](../bench/results/README.md) for the status of existing local outputs.

## Verified KramaBench integration contract and remaining work

The official repository documents a system adapter in [`benchmark/benchmark_api.py`](https://github.com/mitdbg/KramaBench/blob/main/benchmark/benchmark_api.py): a SUT implements `process_dataset(dataset_directory)` and `serve_query(query, query_id, subset_files)`, returning an answer and generated pipeline code. The top-level [`evaluate.py`](https://github.com/mitdbg/KramaBench/blob/main/evaluate.py) runs the workload, saves detailed metrics, and aggregates them; [`benchmark/evaluator.py`](https://github.com/mitdbg/KramaBench/blob/main/benchmark/evaluator.py) chooses metrics from each task's `answer_type`, handles subtasks, and can evaluate pipeline code.

Before Phase 2, this project had no KramaBench `System` implementation. Its local conversation runner solved a task through PXP turns; it did not load the task's dataset files, execute a data pipeline, or return the SUT response schema. The parser preserves selected workload fields but is not an adapter. `trial_runner.py` uses fixed simulations in `example`/`dry_run` and blocks `pilot`/`full_study`. The Phase 2 adapter now covers the SUT boundary, while official-harness execution remains pending the exact pinned checkout and evaluator runtime.

The evaluator's optional pipeline-code check uses an LLM judge (`GPTInterface(model="gpt-5-mini")`) and the harness enables it by default unless `--no_pipeline_eval` is passed. The pilot plan must decide and document whether to include that evaluation, how to configure its credentials/cost, and how its results relate to answer metrics. Do not substitute this project's arithmetic summaries for the official harness output.

**Ownership is unassigned:** the milestones identify Student 4 as the owner of the scoping note and trial-results workflow, and Student 3 as the agent workstream, but they do not assign the KramaBench SUT adapter or evaluator integration. The task needs an explicit owner/team split before pilot enablement; the repo does not support a more specific ownership claim.

The ignored local checkout at `bench/data/krama_raw/` is not a reliable proof of the canonical source revision: its Git remote is `Unsupervisedcom/KramaBench`, while project documentation points to `mitdbg/KramaBench`. Pin/verify the intended upstream revision and evaluator before recording results.

## Phase 2.2 implementation and validation status

The authoritative repository is `https://github.com/mitdbg/KramaBench` at `b2e0d77540263f8b6119f977fe79a8c4386b5a02`. A separate temporary checkout at `/private/tmp/krama-phase2_1-authoritative` is detached at that exact commit; Git blob hashes for `benchmark/benchmark_api.py`, `benchmark/executor.py`, `benchmark/evaluator.py`, and `evaluate.py` match the commit tree. The ignored checkout under `bench/data/krama_raw/` remains unchanged at `a9d002317f405b2bfd83c7b8ef104a516c11dd84` with its unrelated `Unsupervisedcom/KramaBench` remote.

[`bench/kramabench_adapter.py`](../bench/kramabench_adapter.py) implements the official `System` boundary over the PXP conversation runner. [`bench/kramabench_data.py`](../bench/kramabench_data.py) fully parses only paths supplied through `subset_files` into a temporary SQLite store. The model receives metadata and calls a constrained interface for profiles, explicit preview/filter pages, full-table aggregation, text search/read, and JSON paths. Cross-file aggregation requires compatible columns. It cannot send SQL or Python to the data service. Source files are read-only, generated pipeline code is syntax-checked but not executed, and this is separate from the Blackboard fork/rollback mechanism. Per-task records include each available path, SHA-256, size, format, table schema/row counts, and PDF page counts. File-size, file-count, processing-time, result-page, group-count, and evidence-context limits fail explicitly instead of silently dropping records. The model's data-tool loop is bounded to 12 calls; complete input files remain queryable through the approved operations and are never copied wholesale into prompts.

Validated input formats are CSV, TSV, JSON, JSONL, TXT/Markdown, static HTML tables and text, XLSX, and text-extractable PDF. Empty cells are represented as missing values; UTF-8/BOM/UTF-16/Windows-1252 decoding is explicit; malformed rows/JSON, unreadable files, unsupported types, nonnumeric aggregate values, output limits, and timeouts raise errors. Ragged HTML rows are padded with explicit missing cells; `rowspan` and `colspan` are not expanded. XLSX formulas are not recalculated: cached values are read, and formulas without cached values remain formula text. Scanned PDFs require OCR and are rejected when no text can be extracted. `requirements.txt` declares `openpyxl` and `pdfplumber`. They are missing from the project `.venv`; package installation could not reach PyPI because DNS/network access was unavailable. Their XLSX/PDF tests passed under the machine's base Python where both packages are installed. They remain unavailable to the project `.venv` until those declared dependencies are installed. The authoritative workload inventory also contains `.cdf`, `.dat`, `.gpkg`, `.npz`, `.lst`, `.sp3`, `.tle`, and `.hdr` inputs; those formats are not implemented, so tasks requiring them fail preflight explicitly.

The isolated evaluator environment is `/private/tmp/krama-phase2_1-eval-venv`; it contains numpy 2.5.4, pandas 3.0.6, typeguard 4.6.0, rouge-score 0.1.2, nltk 3.10.3, openai 1.72.0, untruncate-json 1.1.0, and ollama 0.6.3, with `pip check` reporting no broken requirements. The pinned `Evaluator` constructor accepts no client/factory/configuration parameter and unconditionally creates `GPTInterface(model="gpt-5-mini")`. With `OPENAI_API_KEY` absent, the exact pinned `Evaluator(..., evaluate_pipeline=False)` raises `OpenAIError` at client construction. An HTTP-send guard observed zero sends during that probe. `--no_pipeline_eval` disables the pipeline judge call only; it does not bypass GPT client construction. `numeric_exact` maps to pinned metric `success`; an instrumented direct call returned `(1, 0, 0, 0)` with zero GPT method calls and zero HTTP sends. This checks the metric implementation only and is not a successful `Evaluator` run. The answer types `string_approximate` (`llm_paraphrase`) and `list_approximate` (`f1_approximate`) can also invoke the LLM judge; exact string/list and numeric metrics are deterministic.

Pinned-data preflight fully parsed `environment-easy-1` (answer type `numeric_exact`) from its one allowlisted CSV: `water-body-testing-2013.csv`, 1,469,864 bytes, SHA-256 `911d1ef8bf290494ace9ff1785b5e9789a50de21606aaef8d4b45a7ab695becb`, 15,388 rows and 11 columns. It also parsed `legal-hard-1`'s 53 allowlisted files (678,557 bytes total; CSV plus HTML). The legal workload names `State MSA Identity Theft Data/*`, while the pinned data tree nests the directory under `csn-data-book-2024-csv/CSVs/` and spells `data` in lowercase. The adapter's case-insensitive path-suffix fallback resolves those 53 tracked files; static tables in the legal HTML are indexed too. The official README's Hugging Face instructions are `load_dataset("eugenie-y/kramabench")`; no HF download was needed because selected assets are tracked in the pinned checkout. The ignored checkout at `bench/data/krama_raw/` was not modified.

The runner now accepts `--evaluator-python` (or `KRAMABENCH_EVALUATOR_PYTHON`) and starts the pinned evaluator in that isolated interpreter; the System bootstrap imports this project's adapter in the child process. It checks evaluator dependencies and key presence before creating a run directory or calling Ollama. No key is currently configured. No dummy key, fake key, evaluator patch, external LLM call, official score, or official result file was created. The complete synthetic path remains blocked before the pinned `Evaluator` can be instantiated; the earlier pinned `Executor`/adapter/metric contract check remains a partial contract test only. A genuine one-task evaluation was not run.

Synthetic data-access tests cover large CSVs beyond the former excerpt cap, full-row aggregation, CSV/TSV, JSON/JSONL, text, encoding fallback, missing cells, XLSX, PDF, HTML, file association, timeouts, malformed inputs, explicit paging, allowlist isolation, and reference-answer isolation. The XLSX/PDF cases were exercised with installed parsers under base Python; the project `.venv` skipped that combined dependency test because those packages could not be installed without network access. Generated pipeline code is still not executed and no benchmark answer is claimed from these synthetic tests.

## Phase 2.3 evaluator validation and readiness

**Status: BLOCKED for a genuine task evaluation.** The current process has no `OPENAI_API_KEY`, and no key was found in the checked project or conventional local config paths. The exact pinned `Evaluator(..., evaluate_pipeline=False)` was retried under the isolated evaluator Python. It raised `OpenAIError` while constructing `GPTInterface`; an HTTP send guard recorded zero sends. No key value was displayed, no fake key was used, and no external completion was attempted. Disabling pipeline evaluation does not avoid that constructor.

### Isolated compatibility fixture

To test the deterministic evaluator path without credentials, a separate temporary checkout at the same pinned commit was used. The authoritative checkout and its source files remain unchanged. The only compatibility edit in the temporary copy was:

```diff
- self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini")
+ self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini") if evaluate_pipeline else None
```

With pipeline evaluation disabled, the fixture ran the pinned `evaluate.py`, `Benchmark`, and `Executor`, the project `KramaBenchSystem` and PXP conversation runner, and the pinned `success` metric. The local model responses were deterministic test doubles, and HTTP guards recorded zero network attempts. The compatibility evaluator persisted the normal task-measures and aggregate CSVs; `success` was `1.0`, evaluator metric token usage was `0`, and a direct call to the unchanged pinned metric returned `(1, 0, 0, 0)`. Reserved evaluator-only fields were absent from every agent prompt. This is a **compatibility-patched synthetic integration test**, not an unmodified official Evaluator run, not a genuine task, and not a KramaBench score. The one-line patch is not part of this repository and has not been accepted for benchmark use.

The temporary run emitted an existing Python 3.14 `SyntaxWarning` in the pinned `benchmark/llm_tools/prompts.py`; it did not fail the fixture.

The unmodified official evaluator therefore remains unvalidated in this environment. The project's one-task runner still requires a key and does not enable the compatibility route. A genuine task may use the unmodified evaluator after a valid key is configured, or a compatibility-patched route only after that exact change is explicitly reviewed and accepted and a provenance-checked runner mode is implemented. Either route still requires approval before the first genuine local model call.

### Selected task and data access

Pinned preflight for `environment-easy-1` passed with answer type `numeric_exact` and one allowlisted, tracked CSV: `water-body-testing-2013.csv` (1,469,864 bytes, SHA-256 `911d1ef8bf290494ace9ff1785b5e9789a50de21606aaef8d4b45a7ab695becb`, 15,388 rows, 11 columns). The evaluator maps this answer type to deterministic `success`. The task asks for a percentage; the toolbox can query complete rows with filters and counts, but division and three-decimal rounding are model reasoning rather than an explicit calculator operation. The input is available without truncation, but no model run has verified the task's interpretation or arithmetic.

The input interface enforces up to 2,048 files, 128 MiB per file, 512 MiB total, 120 seconds for data processing, 50 rows per page, 18,000 characters per tool result, 5,000 aggregate groups, 32,000 evidence characters, and 12 data-tool calls by default. Oversized results fail instead of being silently shortened. SQLite stores tabular rows in a temporary disk-backed database; decoded text and JSON documents are retained in process memory, so these byte limits are not a hard operating-system RAM quota. The data deadline is checked during parsing and queries, but not during every file hash/read operation. Model requests have a 300-second per-request timeout and bounded call/retry counts, but there is no single end-to-end task deadline.

The pinned main-workload inventory also includes unsupported formats. Source-pattern matching identifies these affected tasks: `.cdf` and `.lst`: `astronomy-hard-8`; `.dat`: `astronomy-hard-9`, `astronomy-hard-11`; `.gpkg`: `wildfire-easy-2`, `wildfire-easy-3`; `.npz`: `astronomy-hard-12`; `.sp3`: `astronomy-hard-10`; `.tle`: `astronomy-easy-5`, `astronomy-hard-9`. Those task IDs fail preflight explicitly. No selected `environment-easy-1` file uses an unsupported format. XLSX and PDF are implemented and covered by the focused tests, but `openpyxl` and `pdfplumber` are absent from both `.venv` and the isolated evaluator environment. The selected CSV task does not need them. No package source is configured, and the earlier package-index DNS failure was not retried.

### Phase 2.3 checks and gate

- `env -u RUN_LIVE_OLLAMA_TEST .venv/bin/python -m pytest -q -rs`: **146 passed, 18 skipped, 1 existing Starlette/httpx deprecation warning**. The skips are 17 Ollama-dependent live tests and the combined XLSX/PDF test because `openpyxl` is absent from `.venv`.
- `python -m pytest -q tests/test_kramabench_data.py tests/test_kramabench_adapter.py tests/test_kramabench_preflight.py tests/test_kramabench_runner.py`: **36 passed** under base Python, including XLSX and PDF cases.
- `python -m py_compile bench/kramabench_adapter.py bench/kramabench_data.py bench/kramabench_preflight.py bench/kramabench_runner.py bench/kramabench_subprocess.py agents/conversation.py agents/llm_client.py bench/ingest/krama_parser.py`: passed.
- `git diff --check`: passed.
- The one-task runner revalidated the pinned task and evaluator CLI, then stopped at the missing-key check. Its isolated output directory was not created. This session could not recheck the local Ollama endpoint because the sandbox denied access to `127.0.0.1:11434`; a prior user terminal log showed the local model installed, but availability was not verified by this run.

No genuine task, paid API operation, external model completion, or benchmark result was created. Phase 2 remains **BLOCKED** on the unmodified evaluator credential requirement or explicit acceptance of the compatibility route, plus a verified local model endpoint and an approved first model call. Phase 3 counterfactual scoring should wait until this one-task path is accepted and exercised.

## Phase 2.4 evaluation-path decision and one-task readiness

**Status: BLOCKED.** The compatibility route remains unimplemented and unaccepted. No genuine task, generation request, or evaluator API request was made in this phase.

### Evaluator path decision

Recommend **Option A, the unmodified pinned evaluator**, for the first genuine task because it preserves direct comparability with KramaBench's published harness. The selected task uses `numeric_exact`, which maps to deterministic `success`; pipeline evaluation and subtasks can remain disabled. Even in that configuration, the pinned `Evaluator` constructs `GPTInterface(model="gpt-5-mini")`, so a configured `OPENAI_API_KEY` is required at initialization. No key value was read or logged, and no API request was used to test a key. The current evaluator environment check reports the key absent. A configured key's validity remains unverified until the approved task run.

Option B remains a possible fallback only after explicit project acceptance. The isolated checkout is at the same upstream commit, `b2e0d77540263f8b6119f977fe79a8c4386b5a02`, and the complete compatibility diff is one line in `benchmark/evaluator.py`:

```diff
- self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini")
+ self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini") if evaluate_pipeline else None
```

The SHA-256 of that isolated `git diff --binary -- benchmark/evaluator.py` is `39dc1ea82ad6d7a75ca3a18de7b3efcbeb58250b307b09c45541286d20394897`. This identifies the experiment only; the patch is not accepted or enabled by the project runner.

The isolated synthetic fixture demonstrated that this defers GPT client construction when pipeline evaluation is disabled. The patch does not change the pinned metric implementation, metric selection, aggregate computation, or CSV persistence in the inspected diff; the fixture exercised only a synthetic `numeric_exact`/`success` case. It does not validate approximate-answer metrics, pipeline judging, or a genuine task. A result from that code would be a compatibility-evaluator result, not an unmodified official-Evaluator result. The authoritative checkout remains unchanged, and the project runner rejects local edits to tracked files under `evaluate.py` and `benchmark/`.

### Readiness gates and provenance

The runner now requires `--approve-genuine-run`; its default is off, and the isolated subprocess also requires an approval marker. Before making a run directory it checks the pinned revision and tracked evaluator source integrity, workload/data preflight, evaluator dependencies and key presence, evaluator CLI options, and Ollama health plus exact model-tag availability through GET requests only. It never pulls a model. The manifest distinguishes `official_unmodified` provenance, records the evaluator-tree fingerprint, task answer type, data-file hashes, model and options, prompt version, agent-config hash, evaluator options, timestamps, readiness details, and failed/success status. Compatibility mode has no runner flag and cannot be mislabeled as this mode.

The current sandbox could not connect to `http://127.0.0.1:11434`; this means local Ollama readiness is **unverified here**, not that Ollama is unavailable on the user's machine. The required project tag is `qwen3:4b-instruct-2507-q4_K_M`. No generation endpoint was called. The selected evaluator interpreter `/private/tmp/krama-phase2_1-eval-venv/bin/python` exists, its required modules are present, and the pinned evaluator's `--help` preflight passes. `KRAMABENCH_EVALUATOR_PYTHON` is unset in this shell, so the run command must select that interpreter explicitly. The runner stops at the missing-key gate before creating output.

### Selected-task preflight

Non-generative preflight passed again against the clean pinned checkout at `b2e0d77540263f8b6119f977fe79a8c4386b5a02`. `environment-easy-1` has answer type `numeric_exact` and official metric `success`. Its sole allowlisted input, `water-body-testing-2013.csv`, is 1,469,864 bytes with SHA-256 `911d1ef8bf290494ace9ff1785b5e9789a50de21606aaef8d4b45a7ab695becb`; parsing found 15,388 rows and 11 columns: `Community Code`, `Community`, `County Code`, `County Description`, `Year`, `Sample Date`, `Beach Name`, `Beach Type Description`, `Organism`, `Indicator Level`, and `Violation`. No subtasks were included. This establishes input availability and scoring configuration only; it does not establish the task answer, the model's arithmetic, or benchmark accuracy.

### Phase 2.4 validation

- Focused benchmark tests under `.venv`: **43 passed, 1 skipped** because `openpyxl` is absent. The base Python focused suite: **44 passed**.
- Full project suite under `.venv`: **154 passed, 18 skipped, 1 Starlette/httpx deprecation warning**. Skips: 2 `test_entry_live.py` tests require `--run-ollama`; 1 combined XLSX/PDF test lacks `openpyxl`; 3 live WebSocket tests require `RUN_LIVE_OLLAMA_TEST=1`; 12 persona tests require `--run-ollama`.
- `py_compile` and `git diff --check`: passed. Unit tests verify default-off approval, missing-key and missing-model gates before output creation, rejection of a modified evaluator source, health/model-list-only checks, and distinct unmodified-evaluator manifest fields.
- The direct non-generative Ollama probe could not reach the endpoint. No compatibility fixture was rerun, no package was installed, and no model/API call or genuine task was started.

The exact command for a future approved run is:

```sh
KRAMABENCH_EVALUATOR_PYTHON=/private/tmp/krama-phase2_1-eval-venv/bin/python \
.venv/bin/python -m bench.kramabench_runner \
  --kramabench-root /private/tmp/krama-phase2_1-authoritative \
  --workload environment \
  --task-id environment-easy-1 \
  --output-directory /private/tmp/kramabench-phase2_4-results \
  --evaluator-python /private/tmp/krama-phase2_1-eval-venv/bin/python \
  --model qwen3:4b-instruct-2507-q4_K_M \
  --base-url http://127.0.0.1:11434 \
  --approve-genuine-run
```

Do not run it until a valid key is configured locally, the exact Ollama tag is reachable, and the user explicitly approves one genuine local model run using the unmodified pinned evaluator with pipeline evaluation and subtasks disabled. The command was not executed. Phase 2 remains **BLOCKED**; do not begin Phase 3.

Other Week 3 blockers visible in this checkout:

- **Counterfactual runner:** the Blackboard can fork an isolated sandbox and has rollback unit tests, but `agents/conversation.py` does not detect a live deadlock, choose a cutoff, rerun downstream agents in the fork, score the alternative, or promote a successful alternative to the live board. The score contract deliberately allows `delta_score=null` until a real evaluator and adoption rule exist.
- **Deadlock experiment harness:** core deadlock detection and tests exist, but there is no pilot harness that deterministically pairs baseline/counterfactual runs on identical KramaBench tasks and preserves each failed attempt.
- **Split UI source:** a split timeline control/layout exists, but the live tap forwards only `ENTRY_POSTED` from the primary conversation board. The conversation runner does not yet emit real sandbox entries or rollback results for the right-hand panel.
- **Token-cost reporting:** Ollama usage counts are present in conversation transcripts, but the UI and trial runner still report board-entry text estimates or simulated values. Pilot accounting must sum provider-reported prompt/completion usage across generation, self-check, retries, and counterfactual calls.
- **Run provenance:** pin the benchmark source revision, task IDs, model tag, prompt/config versions, cache policy, evaluator options, and failed-run records. Keep answers/reference code out of the SUT context.

## Phase 2.5 — no-key compatibility path

The Phase 2.4 recommendation to use the unmodified evaluator assumed a configured OpenAI key. The user decided not to configure one, so the runner now offers an explicit `compatibility-no-key` mode while leaving `official` as the default. The unmodified pinned `Evaluator` still constructs `GPTInterface(model="gpt-5-mini")` even when pipeline evaluation is disabled; the compatibility copy changes only that constructor to run conditionally on `evaluate_pipeline`. With the supported task's pipeline evaluation disabled, no GPT client is created. The evaluator subprocess also removes any inherited `OPENAI_API_KEY`. No dummy key was used and no OpenAI request was made.

The patch is applied only to a sparse, isolated copy of exact upstream commit `b2e0d77540263f8b6119f977fe79a8c4386b5a02`. Before execution, the runner checks the expected original source context, exact binary diff SHA-256 `39dc1ea82ad6d7a75ca3a18de7b3efcbeb58250b307b09c45541286d20394897`, commit, changed-file set, and patched file hash. The copy is moved under the run directory; the authoritative checkout at `/private/tmp/krama-phase2_1-authoritative` remains unchanged. It cannot silently fall back to another evaluator. `--approve-genuine-run` still gates both evaluator modes.

Compatibility mode is intentionally limited to `environment-easy-1`: exact workload and CSV Git blobs, answer type `numeric_exact`, the deterministic `success` metric, one allowlisted CSV (1,469,864 bytes; SHA-256 `911d1ef8bf290494ace9ff1785b5e9789a50de21606aaef8d4b45a7ab695becb`; 15,388 rows × 11 columns), configured Qwen tag `qwen3:4b-instruct-2507-q4_K_M`, and `http://127.0.0.1:11434`. Subtasks and pipeline evaluation must be off. Other tasks, answer types, evaluator revisions, input hashes, model tags, or evaluator options fail closed. The adapter exposes the allowlisted CSV through its constrained data interface; evaluator answers are not sent to the agent. Generated pipeline code is syntax-checked and never run.

### Phase 2.5 validation status

The standalone synthetic integration fixture passed through the project adapter/PXP flow, pinned `evaluate.py`, Executor and Evaluator, the unchanged deterministic `success` metric, and task-level plus aggregate CSV output. The model client used six deterministic fake responses. Network guards recorded zero HTTP attempts, there were zero real Ollama generations, and reference-answer/reference-pipeline markers did not appear in prompts. The fixture's `success=1.0` is synthetic only: it is not an official unmodified Evaluator result, a genuine task result, or a benchmark score. A Python 3.14 `SyntaxWarning` in the pinned upstream prompt module was emitted during the fixture, without affecting its pass result.

Focused KramaBench tests: **56 passed, 1 skipped** because `openpyxl` is absent for the XLSX/PDF test. Full project suite: **167 passed, 18 skipped, 1 existing Starlette/httpx deprecation warning**. The 18 skips cover 17 opt-in live Ollama tests and the XLSX/PDF test. The isolated evaluator Python ran the synthetic fixture. The current sandbox could not reach the local Ollama endpoint for a non-generative health/model-list check, so live model readiness remains unverified. No genuine task, model-generation request, Phase 3 scoring, package installation, or OpenAI request was performed.

Future command for the first genuine task (not executed):

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

Run it only after the local non-generative readiness check confirms the exact Ollama model tag and the user explicitly approves the real local model call. Compatibility-evaluator output must remain labeled separately from unmodified official KramaBench output. Phase 3 is not part of this implementation.
