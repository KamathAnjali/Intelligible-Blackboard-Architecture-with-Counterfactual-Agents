# Project progress report

**Updated:** 2 October 2026

The repository now combines the shared blackboard and scheduler, local agent workflows, and Student 4's UI and benchmarking components. The Python suite currently passes in the local environment, and the frontend production build completes.

## Implemented

| Area | Current behavior |
| --- | --- |
| Blackboard and scheduler | Pydantic board contracts, thread-safe state, snapshots, replay traces, round-robin scheduling, and turn validation. |
| Agents | Ollama client, four agent personas, strict PEX/PXP validation, bounded retries, explanation review, and conversation runners. |
| UI | FastAPI health endpoints and WebSocket event streaming; Vite/React graph uses D3 with radial and force layouts, history scrubbing, and token tally display. |
| UI event sources | `LOG_REPLAY` replays the checked-in session by default; `/ws/mock` exposes the canned stream. In `LIVE_TAP`, `POST /api/conversation/start` launches the actual local conversation runner in-process and forwards its Blackboard `ENTRY_POSTED` events to `/ws`. |
| KramaBench ingest | JSON and JSONL loaders map task IDs, query text, answers, and subtasks into `TaskFormat`. The parser supports the upstream `id`/`query`/`answer`/`subtasks` shape as well as the project's earlier normalized field names. |
| Token accounting | The UI counts prediction and explanation text only. It uses a rough whitespace heuristic unless a model tokenizer is registered; the current UI path does not pass model usage metadata. These are entry-text estimates, not full prompt/completion tokens. |

## Student 4 workflow status

- FastAPI and Vite/React scaffolds, D3 selection note, static/live graph UI, WebSocket mock stream, recorded replay, parser, and token tracker are present.
- The shared `PXPTag` enum is `RATIFY`, `REVISE`, `REFUTE`, and `REJECT`. The UI fixtures now use that same enum; `PROPOSE` is not a board tag. Initial hypotheses use `REVISE` under the current contract.
- `bench/data/recorded_session.json` is a project replay fixture, not an upstream KramaBench workload sample. The parser's built-in self-check uses synthetic tasks. A local, Git-ignored upstream clone at `bench/data/krama_raw/` was parsed successfully; its source data is not checked into Git.
- The project model is `qwen3:4b-instruct-2507-q4_K_M`; it is installed in the tested environment. `agents/llm_client.py` and `run.ps1` use that same default. No `.env` model file is used; setup instructions are in the root README.
- `LIVE_TAP` attaches to the in-process conversation runner's Blackboard. One real turn passed at the prior 256-token cap. Three additional varied real turns passed with the permanent 768-token cap; all six generation/self-check calls ended with `done_reason=stop` (39–42 generated tokens per turn generation) and no truncation.
- The earlier 8B `qwen3:latest` attempt truncated at 256 tokens. That result did not reproduce with the standardized 4B Instruct model.

## Benchmark result validity

- No official KramaBench performance results have been produced by this repository. The current runner does not execute the agent conversation or invoke the official evaluator.
- `example` and `dry_run` generate synthetic fixture/simulation records. The runner now disables `pilot` and `full_study`, and the plot loader rejects legacy or simulated CSVs without official evaluator provenance.
- Older local files named `pilot_run_*.csv` and their charts are simulator outputs; their agreement rates, token counts, and timings are not benchmark findings. The former 64%–100% accuracy and +13.3% token overhead claims are withdrawn. Those git-ignored files are left in place but cannot be loaded by the current plotter.
- The upstream [KramaBench repository](https://github.com/mitdbg/KramaBench) defines answer-type-dependent evaluation. Internal blackboard agreement is not the benchmark score. A real agent execution and evaluator adapter are still required.
- The official evaluator reports per-task metrics and aggregates supported metrics at workload level. The current project has no adapter to that output; its legacy simulator's arithmetic summaries are not equivalent to KramaBench aggregation.

## Verification

- `pytest -q`: 104 passed, 17 skipped. The skipped cases include opt-in live inference tests.
- Opt-in real-agent WebSocket smoke test: 3 passed using `qwen3:4b-instruct-2507-q4_K_M` and the production 768-token cap; a separate first run also passed at 256.
- `npm run build` in `ui/frontend`: TypeScript and Vite production build passed.
- `python -m bench.ingest.krama_parser`: built-in parser self-check loaded two synthetic tasks.

## Remaining work

- Run a model-generated conversation through `/api/conversation/start` with Ollama available, then confirm its actual entries arrive in the browser WebSocket.
- Run a full browser-to-server WebSocket walkthrough in the target local environment before presenting the UI.
- Complete the multi-agent benchmark pipeline, counterfactual evaluation, and planned benchmark study described in [Project milestones](PROJECT_MILESTONES.md).
- Implement and validate a real KramaBench agent/evaluator path before enabling pilot/full-study result generation.
- The KramaBench adapter's owner is not assigned in the current planning docs. The official repository interface to implement is described below in [benchmark_scoping_note.md](benchmark_scoping_note.md).
- `tests/test_live_websocket.py` verifies board/scheduler transport with a fixed entry. `tests/test_live_llm_websocket.py` is the separate opt-in check that calls the standard Ollama model through the real conversation runner and verifies its generated entry reaches `/ws`.
