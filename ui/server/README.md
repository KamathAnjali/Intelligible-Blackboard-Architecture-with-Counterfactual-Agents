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
- `WS /ws` — WebSocket endpoint echoing received JSON data (integration point for board events).
