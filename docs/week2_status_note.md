# Week 2 Historical Status & Integration Checkpoint

**Owner:** Student 4 (UI & Benchmarking)
**Status:** Archived draft, corrected 2 October 2026

> The earlier version claimed five KramaBench runs and live UI outcomes. No source run logs were preserved, the listed task IDs and reasoning examples were not verified against the upstream workload, and several listed tags predate the shared PXP enum. Those claims are withdrawn; this note is not evidence of completed benchmark runs.

## Verified current state

- The checked-in session is replay/demo data, not a real KramaBench agent run.
- The KramaBench parser can read upstream task records; parsing a record does not run agents or score their answers.
- Token counts in the UI cover prediction and explanation text. They are estimates unless a model tokenizer is explicitly registered, exclude the full system/user prompt, and are not benchmark scores.
- The runner's example and dry_run modes are synthetic. Its pilot and full_study modes are disabled until real agent execution and the official evaluator are connected.
- The current LIVE_TAP path starts the in-process conversation runner and forwards events from its actual Blackboard. Four real-agent smoke cases passed with `qwen3:4b-instruct-2507-q4_K_M`; see [PROGRESS_REPORT.md](PROGRESS_REPORT.md) for the tested token caps and setup source.

See [PROGRESS_REPORT.md](PROGRESS_REPORT.md) for the current audit and [bench/results/README.md](../bench/results/README.md) for benchmark result validity.

## Planned evaluation

The former 20–30 task target and 0%, 33%, 66%, and 100% counterfactual settings are planning parameters only. They have not been executed as an official pilot. Do not use the legacy CSVs or charts as results.
