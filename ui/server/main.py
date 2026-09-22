import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from ui.server.mock_stream import mock_event_stream_generator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(
    title="Intelligible Blackboard - UI Server",
    description="FastAPI + WebSocket backend for board visualization and benchmarking UI",
    version="0.1.0",
)

# Allow CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "Intelligible Blackboard UI Server",
        "status": "ok",
        "ws_endpoints": {
            "/ws": "Primary board event stream (replays mock stream; plug in live board in Week 2)",
            "/ws/mock": "Explicit mock replay endpoint with configurable delay (?delay=seconds)",
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "ui-server",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, delay: float = 0.8):
    """
    Primary WebSocket endpoint.

    For W1 Days 4-5: replays the canned mock event stream so the frontend can
    receive live-style events without needing real agents running.

    TODO (Student 1 / Student 2 Integration — Week 2):
      Replace mock_event_stream_generator with the real board event publisher.
      Expected interface:
        async for event in board_store.subscribe_events(session_id):
            await websocket.send_json(event.model_dump())
    """
    await websocket.accept()
    logger.info(f"Primary WebSocket connection accepted (mock mode, delay={delay}s).")

    try:
        # Send a "connected" handshake immediately so the frontend can update status
        await websocket.send_json({
            "type": "connection_ack",
            "mode": "mock_stream",
            "message": "Connected to Intelligible Blackboard UI Server (mock mode)",
        })

        # Replay the mock stream
        async for event in mock_event_stream_generator(delay_seconds=delay):
            await websocket.send_json(event)

        logger.info("Mock stream replay complete.")
        await websocket.send_json({"type": "stream_complete"})

    except WebSocketDisconnect:
        logger.info("Primary WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"Primary WebSocket error: {e}")
        await websocket.close()


@app.websocket("/ws/mock")
async def websocket_mock_endpoint(websocket: WebSocket, delay: float = 1.0):
    """
    Explicit mock WebSocket endpoint for replaying canned event stream.
    Supports configurable delay via query param: ws://localhost:8000/ws/mock?delay=0.5
    """
    await websocket.accept()
    logger.info(f"Mock WebSocket connection accepted (delay={delay}s).")
    try:
        await websocket.send_json({
            "type": "connection_ack",
            "mode": "mock_stream",
            "message": "Connected to mock event stream endpoint",
        })
        async for event in mock_event_stream_generator(delay_seconds=delay):
            await websocket.send_json(event)
        logger.info("Finished sending mock event stream.")
        await websocket.send_json({"type": "stream_complete"})
    except WebSocketDisconnect:
        logger.info("Mock WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"Mock WebSocket error: {e}")
        await websocket.close()
