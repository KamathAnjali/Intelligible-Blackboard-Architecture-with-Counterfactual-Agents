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
- `WS /ws/mock?delay=1` — streams the canned synthetic sequence for frontend development.

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
