# Project milestones

The weekly checkpoints follow the project schedule. Completion requires a runnable result and recorded evidence, not only component files.

## End of Week 1: shared board and first agent interaction

- A common PXP schema, validated board API, in-memory storage, and JSON snapshot save/load are available.
- The scheduler drives two local LLM personas through real board entries. A recorded agreement sequence and a controlled deadlock case have explicit outcomes.
- The UI can display the actual interaction, either live or through labelled replay. A KramaBench sample can be parsed into the internal task format.
- Evidence: a runnable two-agent demo, transcript, snapshot, graph replay, and integrated release. The model-only conversation demo is available on `3-Dhruva`; cross-branch UI integration is pending.

## End of Week 2: integrated baseline

- Three agents run through one scheduler and board with documented turn, consensus, deadlock, and stopping rules.
- The dashboard receives real board updates and shows history, active turns, and measured token use. Five selected KramaBench tasks execute through the full pipeline with saved outcomes.
- The historical cutoff, snapshot, replay, scoring, and live adoption interfaces for counterfactual work are agreed.
- Evidence: five complete session records, a live dashboard, measured token totals, and documented disagreement cases.

## End of Week 3: counterfactual recovery and pilot

- A deadlocked session can try an altered earlier contribution in an isolated copy, regenerate downstream turns, score the alternative, and accept or discard it within a fixed limit.
- The live board remains unchanged during simulation and can continue after an accepted change. The UI distinguishes live and simulated history.
- A pilot compares 0, 1, 2, and 3 counterfactual-enabled agents on the same selected tasks, with accuracy, agreement, deadlocks, iterations, tokens, and elapsed time recorded. The planned pilot has 20-30 tasks per configuration.
- Evidence: a controlled recovery demonstration, paired enabled/disabled runs, pilot records, and comparison charts.

## After Week 3: full evaluation

- Verify KramaBench, MSCoRe, and MedAgentBench adapters and evaluators before the study. Keep reference answers outside agent prompts.
- Run 500 distinct test instances per configuration across the selected benchmarks, with the same model, task set, personas, and stopping rules. Four configurations require 2,000 trial executions before repetitions.
- Report correct convergence, intelligibility, total computation including simulations and retries, and deadlock recovery. Preserve failed runs and distinguish agreement from correctness.
- Deliver reproducible configurations, results, charts, a final research report, and a demonstration of the baseline and counterfactual system.
