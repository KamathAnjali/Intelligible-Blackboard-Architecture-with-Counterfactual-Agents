# Progress report

`3-Dhruva` integrates the shared blackboard, scheduler, agents, UI and KramaBench work through `4-Tanisha`. Day 10 and 11 agent work is recorded in commit `4326124`.

## Implemented

- The board validates PXP entries, serializes snapshots, emits events and provides isolated history forks. The scheduler enforces round-robin turn ownership and detects terminal states.
- Four Ollama personas produce validated predictions and explanations, with bounded retries and an explanation self-check. Two- and three-agent conversations save transcripts and usage.
- Explanation review uses the task's facts for ordinary questions and requires supplied data-tool evidence for benchmark tasks. This fixes a regression introduced during integration; the earlier prompt-library freeze is a historical checkpoint.
- A four-case local sanity workload and two controlled deadlock fixtures are available through `make sessions` and `make deadlocks`. `RetrospectiveSandbox` generates an alternative in an isolated history fork and replays scheduled agent turns. Its delta compares the largest matching RATIFY group as a fraction of active agents; this is agreement progress, not answer correctness.
- The React/D3 UI and FastAPI backend support board visualization and a scoped KramaBench task endpoint. The benchmark adapter and paired orchestrator are present in the integrated Student 4 work.
- The integrated conversation runner can generate one alternative proposal in a fork after repeated objections. It does not regenerate the downstream conversation or compute a counterfactual score; the full recovery mechanism remains incomplete.

## Evidence and remaining work

The 18 October six-case local agent check recorded 6/6 correct unanimous outcomes. It was a small synthetic sanity check, not a benchmark. Two controlled deadlock fixtures made no model calls. Compact records are in `results/transcripts/`.

On 14 November, 79 existing agent and adapter checks passed, and a live three-agent deduction reached consensus after the explanation-review fix. A five-task KramaBench baseline used the local model and the compatibility evaluator. All five ended without an accepted score: three failed on repeated invalid data requests, one exhausted the data-tool budget, and one reached the six-turn limit without a verified answer.

The data-access prompt was clarified to request compact actions, short search strings, exact categorical column names, and valid aggregate fields. In two retests, the previously blocked tasks reached the agent conversation but both hit the six-turn limit without a verified answer. The fix passed 100 existing focused checks; one dependency-related check was skipped. These runs do not establish benchmark accuracy or completion of the full-pipeline checkpoint. Local run records are under `~/blackboard-day12-20261114/` in WSL.

The Day 15 sandbox preparation uses the shared board fork API and deep-copies the source state. Focused existing checks and a direct isolation check cover empty and nonempty prefixes and invalid cutoffs. The agent registry reflects the supplied snapshot, since historical registry changes are not available.

The opt-in retrospective recovery path retains the live history, reopens a deadlocked board after a positive-delta consensus replay, and supplies the alternative as unverified guidance to a new scheduled turn. A controlled end-to-end recovery reached consensus; a live Ollama replay on a simple deduction reached consensus with six model calls. These checks do not establish a benchmark improvement. The KramaBench adapter and UI are not yet wired to this recovery mode; the five-task pipeline checkpoint and ablation study remain outstanding.
