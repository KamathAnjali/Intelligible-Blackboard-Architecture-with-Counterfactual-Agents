# Intelligible Blackboard — UI Server

FastAPI backend server providing REST health checks and a WebSocket endpoint for live graph streaming and interactive blackboard updates.

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt` (`fastapi`, `uvicorn`, `websockets`, `pydantic`)

## Quickstart

1. Install backend requirements:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the server from the root directory:
   ```bash
   uvicorn ui.server.main:app --reload --port 8000
   ```

   Or from inside `ui/server/`:
   ```bash
   cd ui/server
   uvicorn main:app --reload --port 8000
   ```

## Endpoints

- `GET /` — API root information and available endpoints.
- `GET /health` — Health check endpoint returning status.
- `WS /ws` — streams board entries from the selected board tap mode; `LOG_REPLAY` is the default.
- `POST /api/conversation/start` — starts the actual local agent conversation in `LIVE_TAP` mode and attaches its Blackboard to the WebSocket stream. Body may include `prompt`, `turns` (1–12), `retries` (0–5), and `scenario` (`default` or `disagreement`).
- `GET /api/tasks` — lists only task IDs and safe metadata from the hash-verified pilot scope; it never returns task queries or reference answers.
- `POST /api/tasks/run` — starts one real scoped KramaBench task. The body accepts `task_id`, optional `scope_version`, `counterfactual_fraction` (0, .33, .66, 1), and `mode: "LIVE"`.
- `GET /api/tasks/{session_id}/status` — returns lifecycle state; evaluator score fields appear only after completion.
- `WS /ws/mock?delay=1` — streams the canned synthetic sequence for frontend development.

## Genuine task sessions

Start the backend in `LIVE_TAP` mode and explicitly enable genuine UI runs:

```bash
BOARD_MODE=LIVE_TAP KRAMA_UI_APPROVE_GENUINE_RUN=1 \
  uvicorn ui.server.main:app --reload --port 8000
```

The route uses the frozen `bench/scope/pilot_scope_v1.json`; the task's frozen evaluator mode is not caller-selectable. `KRAMA_UI_APPROVE_GENUINE_RUN` defaults to disabled. `KRAMABENCH_ROOT`, `KRAMABENCH_EVALUATOR_PYTHON`, `OLLAMA_BASE_URL`, and `KRAMA_UI_OUTPUT_ROOT` configure the runner; defaults use `$HOME/kramabench-pinned`, `$HOME/kramabench-eval-venv/bin/python`, the local Ollama endpoint, and `$HOME/krama-results/ui`. Runs are archived under `archive/ui-<session_id>` in the same artifact layout as CLI runs, including the board transcript and per-entry stream log.

Only one task session may run in a server process at a time. The event bus and live board bridge are process-wide. The backend bridges actual board events from the isolated evaluator subprocess through a run-scoped JSONL file; it does not copy task gold or reference fields into the bridge. The session status endpoint withholds evaluator score until the child runner and evaluator have exited.

Token usage uses the `UsageRecord` callback contract in [`docs/contracts/llm_usage_events.md`](../../docs/contracts/llm_usage_events.md). Provider prompt/completion counts are kept separate from entry-text estimates, and the task `usage_ledgers/` artifact is included in archives. Until Student 3 adds the optional callback, the UI uses the existing provider aggregate when available (labelled as unattributed by call/agent) and otherwise shows the estimate as “estimated, entry text only.”

The frontend uses `VITE_WS_URL` to override the WebSocket endpoint and `VITE_API_URL` to override the REST API base. By default, the WebSocket URL derives from `window.location`; same-origin deployments need no override. For Vite development, `VITE_API_PROXY_TARGET` configures the `/api` and `/ws` proxy target (default `http://127.0.0.1:8000`). Set `UI_CORS_ORIGINS` to a comma-separated origin allowlist; its development default is `http://localhost:5173,http://127.0.0.1:5173`. Credentials are not enabled for CORS.

Frontend reducer tests use Node's built-in test runner: `npm --prefix ui/frontend run test:reducer`. They require Node 22 or later for built-in TypeScript stripping; no frontend test dependencies were added.

For a live run, start the server with `BOARD_MODE=LIVE_TAP`, open the frontend, then POST to the conversation endpoint. For example:

```bash
BOARD_MODE=LIVE_TAP uvicorn ui.server.main:app --reload --port 8000
curl -X POST http://localhost:8000/api/conversation/start \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"All tulips are plants. This item is a tulip. Is it a plant? Answer yes or no.","turns":6}'
```

The actual conversation runner, Blackboard, and Scheduler execute in this server process. Ollama must be reachable at the configured local endpoint for model-generated turns. Without `BOARD_MODE=LIVE_TAP`, the endpoint returns HTTP 409. See [`docs/demo_script.md`](../../docs/demo_script.md) for the replay workflow.

The board-to-WebSocket transport test can be run with `python -m pytest -q tests/test_live_websocket.py`; it submits a fixed test entry through the real Blackboard and Scheduler. To make a real local inference call and verify the generated entry arrives over `/ws`, run:

```bash
RUN_LIVE_OLLAMA_TEST=1 OLLAMA_TEST_MODEL=qwen3:4b-instruct-2507-q4_K_M \
  python -m pytest -s -q tests/test_live_llm_websocket.py
```

The runner and live test both use the project default `qwen3:4b-instruct-2507-q4_K_M`; the production generation cap is 768. Three varied real calls passed with this cap and ended normally without truncation. The shared model setup is documented in the root README and Windows helper script; no `.env` file is used for model selection.
