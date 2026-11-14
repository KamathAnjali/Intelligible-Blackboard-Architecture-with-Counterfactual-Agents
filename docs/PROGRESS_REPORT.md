# Progress report

`3-Dhruva` integrates the shared blackboard, scheduler, agents, UI and KramaBench work through `4-Tanisha`. Day 10 and 11 agent work is recorded in commit `4326124`.

## Implemented

- The board validates PXP entries, serializes snapshots, emits events and provides isolated history forks. The scheduler enforces round-robin turn ownership and detects terminal states.
- Four Ollama personas produce validated predictions and explanations, with bounded retries and an explanation self-check. Two- and three-agent conversations save transcripts and usage.
- Explanation review uses the task's facts for ordinary questions and requires supplied data-tool evidence for benchmark tasks. This fixes a regression introduced during integration; the earlier prompt-library freeze is a historical checkpoint.
- A four-case local sanity workload and two controlled deadlock fixtures are available through `make sessions` and `make deadlocks`. The prompt library and rollback request/outcome interface are documented; the interface does not implement retrospective replay.
- The React/D3 UI and FastAPI backend support board visualization and a scoped KramaBench task endpoint. The benchmark adapter and paired orchestrator are present in the integrated Student 4 work.
- The integrated conversation runner can generate one alternative proposal in a fork after repeated objections. It does not regenerate the downstream conversation or compute a counterfactual score; the full recovery mechanism remains incomplete.

## Evidence and remaining work

The 18 October six-case local agent check recorded 6/6 correct unanimous outcomes. It was a small synthetic sanity check, not a benchmark. Two controlled deadlock fixtures made no model calls. Compact records are in `results/transcripts/`.

On 14 November, 79 existing agent and adapter checks passed, and a live three-agent deduction reached consensus after the explanation-review fix. Ollama was using the CPU because the discrete GPU was not visible to Windows. The five-task KramaBench checkpoint has not yet been completed.

The Student 4 archive records a KramaBench UI task attempt that timed out using the compatibility evaluator. It does not establish a benchmark score. A real five-task pipeline checkpoint and fixes based on its observed agent failures are still required for Days 12 and 13. Counterfactual replay, scoring, adoption, and the full ablation study remain pending. See the project milestones and benchmark scoping note for scope and evaluator limitations.
