# Project milestones

The weekly checkpoints follow the project schedule. Completion requires a runnable result and recorded evidence, not only component files.

> These sections describe targets and evidence criteria. They are not claims that every milestone was completed. Current verified status is in [PROGRESS_REPORT.md](PROGRESS_REPORT.md).

## End of Week 1: shared board and first agent interaction

- A common PXP schema, validated board API, in-memory storage, and JSON snapshot save/load are available.
- The scheduler drives two local LLM personas through real board entries. A recorded agreement sequence and a controlled deadlock case have explicit outcomes.
- The UI can display the actual interaction, either live or through labelled replay. A KramaBench sample can be parsed into the internal task format.
- Evidence: the repository includes the model conversation path, snapshot support, and a labelled UI replay. The parser has loaded one task from the local upstream Legal workload; the raw clone is Git-ignored. A full browser-to-server visual demo still needs to be recorded.

## End of Week 2: integrated baseline

- Three agents run through one scheduler and board with documented turn, consensus, deadlock, and stopping rules.
- Target: the dashboard receives real board updates and shows history and active turns. Token counts must be labelled as estimates unless actual model usage metadata is attached. Five selected KramaBench tasks should execute through the full pipeline with saved outcomes.
- The historical cutoff, snapshot, replay, scoring, and live adoption interfaces for counterfactual work are agreed.
- Evidence target: five complete session records, a live dashboard, provider-reported token totals or clearly labelled estimates, and documented disagreement cases. The five-task KramaBench execution is not verified in the current checkout.

## End of Week 3: counterfactual recovery and pilot

- A deadlocked session can try an altered earlier contribution in an isolated copy, regenerate downstream turns, score the alternative, and accept or discard it within a fixed limit.
- The live board remains unchanged during simulation and can continue after an accepted change. The UI distinguishes live and simulated history.
- Planned only: compare 0, 1, 2, and 3 counterfactual-enabled agents on the same tasks. Report official evaluator scores separately from internal agreement/deadlocks; label text-token estimates and timing scope. The proposed pilot size is 20–30 tasks per configuration.
- Evidence: a controlled recovery demonstration, paired enabled/disabled runs, pilot records, and comparison charts.

## After Week 3: full evaluation

- Verify KramaBench, MSCoRe, and MedAgentBench adapters and evaluators before the study. Keep reference answers outside agent prompts.
- Proposed scale only: run up to 500 distinct instances per configuration if supported by the selected workloads and resources. Four configurations would then mean 2,000 executions before repetitions.
- Report correct convergence, intelligibility, total computation including simulations and retries, and deadlock recovery. Preserve failed runs and distinguish agreement from correctness.
- Deliver reproducible configurations, results, charts, a final research report, and a demonstration of the baseline and counterfactual system.
