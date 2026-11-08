"""Opt-in end-to-end smoke test using a locally installed Ollama model.

Run with:
    RUN_LIVE_OLLAMA_TEST=1 OLLAMA_TEST_MODEL=qwen3:4b-instruct-2507-q4_K_M \
        python -m pytest -s -q tests/test_live_llm_websocket.py
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os

import pytest
from fastapi.testclient import TestClient

from agents.conversation import run_conversation
from agents.llm_client import MODEL, OPTIONS, OllamaClient
from ui.server import board_tap, main


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_OLLAMA_TEST") != "1",
    reason="set RUN_LIVE_OLLAMA_TEST=1 to call a real local Ollama model",
)


@pytest.mark.parametrize("task", [
    "All tulips are plants. This item is a tulip. Is it a plant? Answer yes or no.",
    "Every square is a rectangle. This shape is a square. Is it a rectangle? Answer yes or no.",
    "Every whale is a mammal. This animal is a whale. Is it a mammal? Answer yes or no.",
], ids=["tulip", "square", "whale"])
def test_real_agent_entry_reaches_live_websocket(monkeypatch, tmp_path, task):
    """A model-generated runner entry reaches /ws through its actual Blackboard."""
    model = os.getenv("OLLAMA_TEST_MODEL", MODEL)
    assert OPTIONS["num_predict"] == 768
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            ack = websocket.receive_json()
            assert ack["type"] == "connection_ack"
            assert ack["mode"] == "LIVE_TAP"

            with ThreadPoolExecutor(max_workers=2) as pool:
                run_future = pool.submit(
                    run_conversation,
                    OllamaClient(model),
                    task,
                    1,
                    0,
                    tmp_path,
                    on_board_created=board_tap.attach_live_board,
                )
                event_future = pool.submit(websocket.receive_json)

                while True:
                    done, _ = wait(
                        (run_future, event_future),
                        timeout=1,
                        return_when=FIRST_COMPLETED,
                    )
                    if event_future in done:
                        event = event_future.result()
                        report, transcript = run_future.result(timeout=300)
                        break
                    if run_future in done:
                        report, transcript = run_future.result(timeout=1)
                        websocket.close()
                        pytest.fail(
                            f"conversation ended without a WebSocket entry: "
                            f"outcome={report.get('outcome')}, "
                            f"failures={report.get('failures')}, transcript={transcript}"
                        )

            assert report["model"] == model
            assert report["turns"][0]["posted"] is True
            assert report["failures"] == []
            assert event["type"] == "board_entry"
            assert event["entry"]["entry_id"] == report["turns"][0]["entry"]["entry_id"]
            assert event["entry"]["tag"] == "REVISE"
            assert event["entry"]["token_count_kind"] == "estimated_board_entry_text_only"
