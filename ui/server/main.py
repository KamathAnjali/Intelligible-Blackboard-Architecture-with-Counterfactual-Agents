import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
        "ws_endpoint": "/ws",
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "ui-server",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted.")
    try:
        while True:
            # Receive text / JSON message from client
            data_str = await websocket.receive_text()
            logger.info(f"Received message: {data_str}")

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                payload = {"type": "raw_text", "content": data_str}

            # TODO (Student 1 / Student 2 Integration):
            # Process board event / command via Student 1's store or Student 2's scheduler here.
            # Example: response_data = await process_board_event(payload)

            # Echo response back to client
            echo_response = {
                "type": "echo",
                "received": payload,
                "status": "acknowledged",
            }
            await websocket.send_json(echo_response)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()
