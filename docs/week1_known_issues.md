# Student 4 Week 1 Known Issues and Verification Gaps

Status checked: 2026-10-02. This log records limitations that affect interpreting the UI, live stream, or benchmark output.

## 1. LIVE_TAP scope is limited to the in-process conversation runner

`BOARD_MODE=LIVE_TAP` waits for a conversation started by this FastAPI server and attaches to that run's Blackboard. It is not a general tap into a separate teammate process or an arbitrary shared board. The bridge currently forwards `ENTRY_POSTED` events; it does not expose every scheduler/board lifecycle event or a full external-board subscription contract. The supported path is `POST /api/conversation/start` in the same server process (`ui/server/main.py`, `ui/server/board_tap.py`).

## 2. The 768 output-token cap has limited reliability coverage

The configured Ollama generation cap is 768 (`agents/llm_client.py`). The opt-in live WebSocket test in `tests/test_live_llm_websocket.py` has three short task cases, one model-generated turn per case. This is useful smoke coverage, but it does not establish reliability over long tasks, multi-turn runs, retries, or all supported response shapes. Treat 768 as the tested project setting, not a statistically validated safe maximum.

## 3. UI token totals are estimates over board-entry text

The UI and board tap count prediction/explanation entry text. These totals do not represent full prompt tokens, self-check prompts, retry traffic, or authoritative Ollama usage. Do not report them as actual model or billing token usage (`ui/server/board_tap.py`, `bench/metrics/token_counter.py`).

## 4. Trial-runner output is not benchmark evidence

`example` and `dry_run` modes are synthetic fixtures/simulations and write only under `bench/results/synthetic/` with `SYNTHETIC` labels. They cannot produce evaluator scores. Real paired KramaBench execution is now handled by `bench.orchestrator`; see `docs/orchestrator_design.md` and the frozen scope. Availability of a compatible local model, pinned checkout, and official evaluator credentials still determines whether a genuine run can execute.

## 5. Missing-record replay fallback can show synthetic content

In `LOG_REPLAY`, if the configured recorded-session file is missing or unrecognized, the backend falls back to the mock event stream. The server logs a warning, but a presenter should check startup logs and the mode/source labels before describing the content as the recorded fixture (`ui/server/board_tap.py`).

## 6. Visual browser verification was unavailable in this audit

On 2026-10-02 the FastAPI and Vite servers started, and the backend logged replay events from `bench/data/recorded_session.json`. The in-app browser runtime reported no available browser instances, so this audit could not confirm the rendered graph by visual inspection. The frontend source includes a replay-mode badge, but the end-to-end visual result remains unverified here.
