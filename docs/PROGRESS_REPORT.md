# Progress report

`3-Dhruva` integrates the shared blackboard, scheduler, agents, UI and KramaBench work through `4-Tanisha`. Day 10 and 11 agent work is recorded in commit `4326124`.

## Implemented

- The board validates PXP entries, serializes snapshots, emits events and provides isolated history forks. The scheduler enforces round-robin turn ownership and detects terminal states.
- Four Ollama personas produce validated predictions and explanations, with bounded retries and an explanation self-check. Two- and three-agent conversations save transcripts and usage.
- Explanation review uses the task's facts for ordinary questions and requires supplied data-tool evidence for benchmark tasks. This fixes a regression introduced during integration; the earlier prompt-library freeze is a historical checkpoint.
- A four-case local sanity workload and two controlled deadlock fixtures are available through `make sessions` and `make deadlocks`. `RetrospectiveSandbox` generates an alternative in an isolated history fork and replays scheduled agent turns. Its delta compares the largest matching RATIFY group as a fraction of active agents; this is agreement progress, not answer correctness.
- The React/D3 UI and FastAPI backend support board visualization and a scoped KramaBench task endpoint. The benchmark adapter and paired orchestrator are present in the integrated Student 4 work.
- The conversation runner supports both the earlier single-proposal experiment and an opt-in retrospective replay with live guidance after a deadlock. Benchmark and UI integration remain incomplete.

## Evidence and remaining work

The 18 October six-case local agent check recorded 6/6 correct unanimous outcomes. It was a small synthetic sanity check, not a benchmark. Two controlled deadlock fixtures made no model calls. Compact records are in `results/transcripts/`.

On 14 November, 79 existing agent and adapter checks passed, and a live three-agent deduction reached consensus after the explanation-review fix. A five-task KramaBench baseline used the local model and the compatibility evaluator. All five ended without an accepted score: three failed on repeated invalid data requests, one exhausted the data-tool budget, and one reached the six-turn limit without a verified answer.

The data-access prompt was clarified to request compact actions, short search strings, exact categorical column names, and valid aggregate fields. In two retests, the previously blocked tasks reached the agent conversation but both hit the six-turn limit without a verified answer. The fix passed 100 existing focused checks; one dependency-related check was skipped. These runs do not establish benchmark accuracy or completion of the full-pipeline checkpoint. Local run records are under `~/blackboard-day12-20261114/` in WSL.

The Day 15 sandbox preparation uses the shared board fork API and deep-copies the source state. Focused existing checks and a direct isolation check cover empty and nonempty prefixes and invalid cutoffs. The agent registry reflects the supplied snapshot, since historical registry changes are not available.

The opt-in retrospective recovery path retains the live history, reopens a deadlocked board after a positive-delta consensus replay, and supplies the alternative as unverified guidance to a new scheduled turn. A controlled end-to-end recovery reached consensus; a live Ollama replay on a simple deduction reached consensus with six model calls. These checks do not establish a benchmark improvement. The KramaBench runner now accepts retrospective recovery and the paired orchestrator enables it; UI integration and ablation evidence remain outstanding.

On 15 November, a paired A/B run injected the same five-entry deadlock in both conditions. Without recovery the board stayed deadlocked. With recovery, the final run reached the expected `insufficient information` consensus in eight live entries, using six simulation calls and seven live model calls. Two earlier enabled runs produced the correct prediction but were flagged for an invalid RATIFY; a bounded retry now rejects changed predictions tagged RATIFY on the recovery turn. The [compact record](../results/transcripts/deadlock-ab-20261115.json) retains all three attempts. This single synthetic case does not establish a general recovery rate or benchmark accuracy.

On 20 November, the no-key allowlist grew from 13 to 76 tasks after offline input parsing, hash and metric checks. The data adapter now reads text-based TLE and DAT inputs, resolves the pinned OMNI2 documentation extension, and recognizes tab-separated exports with a CSV extension. Four deterministic tasks remain blocked at preflight:

- `astronomy-hard-12`: the pinned SP3 selector matches no files, and the task also requires an NPZ grid reader.
- `biomedical-hard-1`: its large XLSX workbook exceeded the 120-second data-operation limit.
- `wildfire-easy-2`: the adapter lacks GeoPackage support.
- `wildfire-hard-19`: the specified WeatherEvents CSV is absent from the pinned revision.

The 18 November five-task baseline with a 16,384-token context produced one correct and one incorrect compatibility-evaluator score. Two tasks exhausted the data-tool budget and one encountered a review request timeout. The v2 scope uses the same task selection and a 16,384-token context. The 20 November dry run stopped during data access in all 12 attempts, before counterfactual recovery could run. `bench.monitor_runs` summarizes saved attempts.

The data workflow skips repeated successful searches and aggregates. For repeated rate requests, it can collect the declared category counts within the existing budget. In the 21 November four-configuration rerun, environment-easy-1 scored correctly in all four settings. The other eight attempts ended without scores, and no counterfactual simulations ran. The full batch remains pending.

The Colab launcher compares Qwen3 8B and 14B on the same five tuning tasks at 0% recovery with a 16,384-token context. Offline checks cover both model configurations, pinned task preflight and evaluator integration. The live launcher checks GPU placement before running tasks.

The MATH-500 pilot supports 24 pinned, self-contained problems across levels 3 to 5 and compares solo answers with six-agent runs at 0%, 33%, 50%, and 66% replay eligibility. The follow-up selects previously deadlocked problems from saved summaries. It includes the first proposer in nonzero settings and lets eligible agents revisit their own rejected proposals when no eligible posted revision exists.
