# Week 1 Demo Script: Blackboard Graph Replay

This walkthrough demonstrates the checked-in replay fixture through the UI. It does not run LLM inference, connect to the live conversation runner, or produce benchmark measurements. The displayed explanation fields are board-entry content, not a model's private chain of thought.

## Start the demo

From the repository root, start the backend with its default replay mode:

```bash
uvicorn ui.server.main:app --reload --port 8000
```

In a second terminal, start the frontend:

```bash
cd ui/frontend
npm run dev
```

Open `http://localhost:5173`. The server's default `BOARD_MODE` is `LOG_REPLAY`; the WebSocket acknowledgement and UI badge should identify replay mode. The UI consumes `bench/data/recorded_session.json` through `/ws`.

## Walkthrough narration

1. **Connect:** Point out the replay-mode badge. Explain that this is a stored session replay, not a live model run.
2. **Watch the graph:** The fixture contains six entries: `REVISE`, `RATIFY`, `REFUTE`, `REVISE`, `RATIFY`, and `REJECT`. Describe the graph nodes and reference edges as each recorded entry arrives.
3. **Inspect an entry:** Select a node and show its recorded agent ID, tag, prediction, explanation, and reference information where present. Call these the entry's recorded fields; do not describe them as hidden reasoning.
4. **Review the alternate branch:** The fixture's final `REJECT` entry is marked as a counterfactual simulation. Explain that the UI is displaying the fixture's recorded marker, not executing a rollback or counterfactual model call during replay.
5. **Finish:** When the stream-complete indicator appears, report only the node and edge counts shown for this fixture. They describe this replay and are not accuracy, token-cost, or benchmark results.

## What this demo verifies

- The frontend connects to the FastAPI WebSocket in `LOG_REPLAY` mode.
- The recorded fixture is delivered as a sequence of board-entry events and rendered in the graph.
- Entry details and tag styling can be inspected in the UI.

The separate real-agent path uses `BOARD_MODE=LIVE_TAP` and the conversation-start endpoint. That path is not part of this replay walkthrough. The Week 1 walkthrough also makes no claims about full benchmark evaluation or KramaBench scores.
