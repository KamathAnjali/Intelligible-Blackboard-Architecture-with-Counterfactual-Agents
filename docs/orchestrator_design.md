# Paired KramaBench Ablation Orchestrator

The maintained CLI reads the hash-verified frozen scope and pairs every selected task across fractions 0, 0.33, 0.66, and 1.0. `plan` is read-only; `dry-run` executes 3–5 frozen tuning tasks/configuration; `pilot` executes all held-out tasks/configuration. A real execution requires `--approve-genuine-run`. Runtime paths, evaluator Python, output root, process timeout, repetition count, and seeds come from CLI/environment; model and fixed limits come from the scope. A seeded shuffle records run order, while the one-enabled-agent assignment rotates across the frozen agent roster.

The current scope freezes two agents. Therefore the runner's `ceil(agent_count * fraction)` rule yields effective enabled counts **0/1/2/2**, not 0/1/2/3. The 66% and 100% configurations are equivalent at this agent count. This CLI will not silently add a third agent or change the frozen scope.

Each experiment lives at `bench/results/<run_id>/`: `results_manifest.json`, append-only `attempts.jsonl`, regenerated joined `results.csv`, and `raw/<workload>/<task>/cf-<pct>/rep-<n>/attempt-<n>/` containing runner outputs and driver logs. The unique run ID plus scope hash/config fingerprint prevents mixing experiments. Re-running skips any key with a completed attempt; failed keys are retried only with `--retry-failed`, in a new attempt directory. No prior attempt is deleted or overwritten.

Outcomes are `AGREEMENT`, `DEADLOCK`, `ITERATION_LIMIT`, `TIME_LIMIT`, or `EXECUTION_FAILURE`. Each row separately records process completion, evaluator score(s), and `correct`; correctness is derived only from the scope's per-task pass rule, never the subprocess return code. Missing/malformed results and timeouts still create rows and count as failures. `failed_before_scoring` is explicit. Tokens use provider usage when available and are labelled estimated/unavailable otherwise; live and simulation token/time fields remain separately traceable, with unknowns left empty rather than fabricated.

Aggregation reports attempted/completed/failed/correct counts, failed-before-scoring and deadlock rates, recovery attempts/success, iterations, tokens, and time by fraction, plus paired task win/loss/tie against 0%. Wilson intervals are provided for correct-convergence proportions. Compatibility-evaluator and adapted rows stay labelled and are not represented as unmodified official output. Synthetic trial-runner CSVs are quarantined under `bench/results/synthetic/` with `SYNTHETIC` names; the plotting provenance gate is unchanged and must reject them.


## Commands

From the repository root, use `plan` to inspect the frozen matrix without launching a model:

```sh
./.venv/bin/python -m bench.orchestrator plan --scope bench/scope/pilot_scope_v1.json
```

A genuine tuning dry-run of five tasks across four fractions (20 task/config cells) uses explicit local paths and approval:

```sh
KRAMABENCH_ROOT="$HOME/kramabench-pinned" \
KRAMABENCH_EVALUATOR_PYTHON="$HOME/kramabench-eval-venv/bin/python" \
OLLAMA_BASE_URL="http://127.0.0.1:11434" \
./.venv/bin/python -m bench.orchestrator dry-run --approve-genuine-run --run-id tune-v1
```

Use `pilot` instead of `dry-run` only after the official evaluator prerequisites are configured. `status --run-id tune-v1` inspects saved attempts; `aggregate --run-id tune-v1` writes paired summaries. `--retry-failed` adds a new attempt only for failed keys. The frozen roster currently has two agents, yielding effective counts 0/1/2/2; a three-agent comparison requires a separate approved scope revision.
