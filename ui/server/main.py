"""
Intelligible Blackboard — UI Server
ui/server/main.py

FastAPI app providing REST health checks and WebSocket board event streaming.

WebSocket endpoints:
  /ws        Live board event stream (mode controlled by BOARD_MODE env var)
  /ws/mock   Always-mock explicit endpoint (legacy / testing)

Run:
  uvicorn ui.server.main:app --reload --port 8000

  # Set mode before starting:
  BOARD_MODE=LIVE_TAP    uvicorn ui.server.main:app --reload   # default
  BOARD_MODE=LOG_REPLAY  uvicorn ui.server.main:app --reload   # replay recorded session
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ui.server.board_tap import BOARD_MODE, start_board_tap
from ui.server.event_bus import bus
from ui.server.mock_stream import mock_event_stream_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("ui-server")


# ── Lifespan: start the board tap once at server startup ─────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Intelligible Blackboard UI Server starting (mode=%s) ===", BOARD_MODE)
    await start_board_tap()
    yield
    logger.info("=== UI Server shutting down ===")


app = FastAPI(
    title="Intelligible Blackboard — UI Server",
    description="FastAPI + WebSocket backend for live board visualization and benchmarking",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "Intelligible Blackboard UI Server",
        "status": "ok",
        "mode": BOARD_MODE,
        "ws_endpoints": {
            "/ws": f"Live board event stream (mode={BOARD_MODE})",
            "/ws/mock": "Explicit mock replay endpoint with configurable delay (?delay=s)",
        },
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "ui-server", "mode": BOARD_MODE}


# ── Primary WebSocket: live event bus ─────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Primary WebSocket endpoint.

    Subscribes to the EventBus (fed by board_tap.py) and fans out events to
    this client.  Each client gets its own queue — slow clients cannot block
    others.

    The 'mode' field in connection_ack tells the frontend whether to render
    a LIVE or REPLAY badge.

    TODO (Student 2 Integration — Week 2):
      When Student 2's scheduler emits events directly (push, not poll), update
      board_tap._run_live_tap() to consume that stream.  No changes needed here.
    """
    await websocket.accept()
    q = bus.subscribe()
    logger.info("[/ws] Client connected  (mode=%s)", BOARD_MODE)

    try:
        # Handshake — lets the frontend know mode immediately
        await websocket.send_json({
            "type": "connection_ack",
            "mode": BOARD_MODE,   # "LIVE_TAP" | "LOG_REPLAY"
            "message": f"Connected to Intelligible Blackboard UI Server ({BOARD_MODE})",
        })

        async for event in bus.stream(q):
            await websocket.send_json(event)
            if event.get("type") == "stream_complete":
                break

    except WebSocketDisconnect:
        logger.info("[/ws] Client disconnected.")
    except Exception as e:
        logger.error("[/ws] Error: %s", e)
    finally:
        bus.unsubscribe(q)


# ── Legacy / testing mock endpoint ────────────────────────────────────────────

@app.websocket("/ws/mock")
async def websocket_mock_endpoint(websocket: WebSocket, delay: float = 1.0):
    """
    Explicit mock WebSocket endpoint — always replays canned synthetic stream.
    Used for isolated frontend testing when board_tap is not configured.
    Supports ?delay=<seconds> query parameter.
    """
    await websocket.accept()
    logger.info("[/ws/mock] Client connected (delay=%.1fs)", delay)
    try:
        await websocket.send_json({
            "type": "connection_ack",
            "mode": "MOCK_STREAM",
            "message": "Connected to synthetic mock event stream",
        })
        async for event in mock_event_stream_generator(delay_seconds=delay):
            await websocket.send_json(event)
        await websocket.send_json({"type": "stream_complete", "source": "MOCK_STREAM"})
    except WebSocketDisconnect:
        logger.info("[/ws/mock] Client disconnected.")
    except Exception as e:
        logger.error("[/ws/mock] Error: %s", e)
