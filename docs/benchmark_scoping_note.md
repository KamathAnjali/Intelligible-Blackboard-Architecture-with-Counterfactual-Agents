# Benchmark Scoping Note — Proposed Evaluation

**Owner:** Student 4 (UI & Benchmarking)
**Status:** Plan only. No official pilot has been run.

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

This project currently has no KramaBench `System` implementation. Its local conversation runner solves a task through PXP turns; it does not load the task's dataset files, execute a data pipeline, or return the SUT response schema. The parser preserves selected workload fields but is not an adapter. `trial_runner.py` uses fixed simulations in `example`/`dry_run` and blocks `pilot`/`full_study`. Connecting the evaluator therefore means implementing the SUT/data-pipeline path, passing its actual outputs to the official harness and evaluator, and preserving the harness's per-task metrics and aggregate output in our results. It is more than importing an evaluation script.

The evaluator's optional pipeline-code check uses an LLM judge (`GPTInterface(model="gpt-5-mini")`) and the harness enables it by default unless `--no_pipeline_eval` is passed. The pilot plan must decide and document whether to include that evaluation, how to configure its credentials/cost, and how its results relate to answer metrics. Do not substitute this project's arithmetic summaries for the official harness output.

**Ownership is unassigned:** the milestones identify Student 4 as the owner of the scoping note and trial-results workflow, and Student 3 as the agent workstream, but they do not assign the KramaBench SUT adapter or evaluator integration. The task needs an explicit owner/team split before pilot enablement; the repo does not support a more specific ownership claim.

The ignored local checkout at `bench/data/krama_raw/` is not a reliable proof of the canonical source revision: its Git remote is `Unsupervisedcom/KramaBench`, while project documentation points to `mitdbg/KramaBench`. Pin/verify the intended upstream revision and evaluator before recording results.

Other Week 3 blockers visible in this checkout:

- **Counterfactual runner:** the Blackboard can fork an isolated sandbox and has rollback unit tests, but `agents/conversation.py` does not detect a live deadlock, choose a cutoff, rerun downstream agents in the fork, score the alternative, or promote a successful alternative to the live board. The score contract deliberately allows `delta_score=null` until a real evaluator and adoption rule exist.
- **Deadlock experiment harness:** core deadlock detection and tests exist, but there is no pilot harness that deterministically pairs baseline/counterfactual runs on identical KramaBench tasks and preserves each failed attempt.
- **Split UI source:** a split timeline control/layout exists, but the live tap forwards only `ENTRY_POSTED` from the primary conversation board. The conversation runner does not yet emit real sandbox entries or rollback results for the right-hand panel.
- **Token-cost reporting:** Ollama usage counts are present in conversation transcripts, but the UI and trial runner still report board-entry text estimates or simulated values. Pilot accounting must sum provider-reported prompt/completion usage across generation, self-check, retries, and counterfactual calls.
- **Run provenance:** pin the benchmark source revision, task IDs, model tag, prompt/config versions, cache policy, evaluator options, and failed-run records. Keep answers/reference code out of the SUT context.
