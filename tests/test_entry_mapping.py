"""Day 5 mapping tests using controlled model responses and the real board."""

import json
from unittest.mock import Mock

import pytest

from agents.llm_client import MODEL, OllamaClient
from agents.pxp import PXPGenerationError
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BlackboardState, BoardEntry, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore


def model_response(tag="REVISE", **extra):
    return {
        "response": json.dumps({"tag": tag, "prediction": " yes ", "explanation": " The supplied rule applies. ", **extra}),
        "done": True, "done_reason": "stop", "wall_seconds": 0.1,
    }


@pytest.fixture
def state():
    agent = AgentRecord(agent_id="a", persona="cautious_verifier", model_name=MODEL)
    return BlackboardState(task_id="mapping-test", agents={agent.agent_id: agent})


@pytest.fixture
def client():
    client = OllamaClient()
    client.generate = Mock(return_value=model_response())
    return client


def test_initial_entry_uses_application_metadata_without_mutating_state(client, state):
    before = state.model_dump_json()
    result = client.generate_entry("Is it a plant?", state, "a")
    entry = result["entry"]
    assert isinstance(entry, BoardEntry)
    assert entry.agent_id == "a" and entry.tag == PXPTag.REVISE
    assert entry.target_entry_id is None and entry.is_counterfactual_sim is False
    assert entry.prediction == "yes" and entry.explanation == "The supplied rule applies."
    assert entry.entry_id and entry.timestamp.utcoffset().total_seconds() == 0
    assert state.model_dump_json() == before
    assert result["attempts"][0]["raw"]["response"] == model_response()["response"]


@pytest.mark.parametrize("tag", [tag.value for tag in PXPTag])
def test_reply_tag_selected_by_model_and_target_by_caller(client, state, tag):
    previous = BoardEntry(agent_id="a", tag="REVISE", prediction="yes", explanation="Rule applies")
    later = BoardEntry(agent_id="a", tag="REVISE", prediction="no", explanation="Alternative")
    state.entries.extend([previous, later])
    client.generate.return_value = model_response(tag)
    result = client.generate_entry("Original task", state, "a", target_entry_id=previous.entry_id)
    assert result["entry"].tag.value == tag
    assert result["entry"].target_entry_id == previous.entry_id
    model_input = json.loads(client.generate.call_args.args[0])
    assert model_input["task"] == "Original task"
    assert model_input["target_entry_id"] == previous.entry_id
    assert [entry["entry_id"] for entry in model_input["history"]] == [previous.entry_id, later.entry_id]


def test_reply_defaults_to_latest_target_and_simulation_flag_is_owned_by_caller(client, state):
    state.entries.append(BoardEntry(agent_id="a", tag="REVISE", prediction="yes", explanation="Reason"))
    result = client.generate_entry("task", state, "a", is_counterfactual_sim=True)
    assert result["entry"].target_entry_id == state.entries[-1].entry_id
    assert result["entry"].is_counterfactual_sim is True


@pytest.mark.parametrize("failure", ["unknown_agent", "inactive_agent", "wrong_model", "unknown_target", "unknown_persona", "empty_task"])
def test_bad_application_context_rejected_before_inference(client, state, failure):
    args = {"task": "task", "state": state, "agent_id": "a"}
    if failure == "unknown_agent":
        args["agent_id"] = "ghost"
    elif failure == "inactive_agent":
        state.agents["a"].active = False
    elif failure == "wrong_model":
        state.agents["a"].model_name = "another-model"
    elif failure == "unknown_target":
        args["target_entry_id"] = "ghost"
    elif failure == "unknown_persona":
        state.agents["a"].persona = "ghost"
    else:
        args["task"] = "   "
    with pytest.raises(ValueError):
        client.generate_entry(**args)
    client.generate.assert_not_called()


@pytest.mark.parametrize("extra", [{"agent_id": "attacker"}, {"target_entry_id": "invented"}, {"is_counterfactual_sim": True}])
def test_model_cannot_supply_metadata(client, state, extra):
    client.generate.return_value = model_response(**extra)
    with pytest.raises(PXPGenerationError):
        client.generate_entry("task", state, "a", max_retries=0)
    assert not state.entries


@pytest.mark.parametrize("invalid", [model_response("WRONG"), model_response("RATIFY"), {
    "response": '{"prediction": "yes", "explanation": "why"}', "done": True, "done_reason": "stop",
}])
def test_bad_or_missing_tag_and_initial_ratify_retry_without_posting(client, state, invalid):
    client.generate.side_effect = [invalid, model_response()]
    result = client.generate_entry("task", state, "a", max_retries=1)
    assert len(result["attempts"]) == 2 and result["attempts"][0]["error"]
    assert result["entry"].tag == PXPTag.REVISE
    assert not state.entries


def test_generated_entry_can_be_submitted_through_real_scheduler(client, tmp_path):
    board = Blackboard("integration", store=InMemoryJSONStore(tmp_path))
    scheduler = Scheduler(board)
    agent = AgentRecord(agent_id="a", persona="cautious_verifier", model_name=MODEL)
    board.register_agent(agent)
    scheduler.register_agent(agent)
    result = client.generate_entry("task", board.get_state(), scheduler.next_agent().agent_id)
    assert not board.get_history()
    scheduler.submit_entry(result["entry"])
    stored = board.get_history()[0]
    assert stored == result["entry"]
    assert BoardEntry.model_validate_json(stored.model_dump_json()) == stored


def test_exhausted_output_never_returns_an_entry(client, state):
    client.generate.return_value = model_response("WRONG")
    with pytest.raises(PXPGenerationError) as exc:
        client.generate_entry("task", state, "a", max_retries=1)
    assert len(exc.value.attempts) == 2
    assert not state.entries
