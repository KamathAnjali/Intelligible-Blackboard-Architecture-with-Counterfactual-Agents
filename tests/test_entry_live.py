"""Two opt-in live Day 5 mapping checks, no multi-agent conversation loop."""

import pytest

from agents.llm_client import MODEL, OllamaClient, save_report
from agents.pex import PEXGenerationError
from blackboard.models import AgentRecord, BlackboardState, BoardEntry, PXPTag


@pytest.mark.parametrize("reply", [False, True], ids=["initial", "reply"])
def test_live_board_entry_mapping(request, reply):
    if not request.config.getoption("--run-ollama"):
        pytest.skip("Pass --run-ollama to call the local model")
    agent = AgentRecord(agent_id="day5-agent", persona="cautious_verifier", model_name=MODEL)
    state = BlackboardState(task_id="day5-live", agents={agent.agent_id: agent})
    target = None
    if reply:
        target = BoardEntry(agent_id=agent.agent_id, tag="REVISE", prediction="yes",
                            explanation="All tulips are plants, and this is a tulip.")
        state.entries.append(target)
    client = OllamaClient()
    prompt = "All tulips are plants. This item is a tulip. Is it a plant? Use yes or no as prediction."
    record = {"case": "reply" if reply else "initial", "prompt": prompt,
              "input_state": state.model_dump(mode="json"), "max_retries": 0}
    try:
        result = client.generate_entry(prompt, state, agent.agent_id, max_retries=0)
    except PEXGenerationError as exc:
        record.update({"error": str(exc), "attempts": exc.attempts})
        save_report("day5-entry", client, [record])
        raise
    entry = result.pop("entry")
    record.update(result)
    record["entry"] = entry.model_dump(mode="json")
    save_report("day5-entry", client, [record])
    print(entry.model_dump_json(indent=2))
    assert entry.prediction == "yes"
    assert entry.agent_id == agent.agent_id
    assert entry.target_entry_id == (target.entry_id if target else None)
    assert entry.tag == (PXPTag.RATIFY if reply else PXPTag.REVISE)
    assert len(state.entries) == int(reply)
