"""Exercise real scheduling, entry mapping and storage with controlled LLM output."""

import json
from unittest.mock import Mock

import pytest

from agents.conversation import run_conversation
from agents.llm_client import OllamaClient
from blackboard.models import BlackboardState


def response(tag="REVISE", prediction="yes", **fields):
    return {"response": json.dumps({"tag": tag, "prediction": prediction,
                                   "explanation": "The supplied rule applies.", **fields}),
            "done": True, "done_reason": "stop", "wall_seconds": 0.1}


def run(tmp_path, responses, **kwargs):
    client = OllamaClient()
    client.generate = Mock(side_effect=responses)
    client.check_explanation = Mock(return_value={
        "parsed": {"supported": True, "reason": "Controlled review."},
        "raw": {"wall_seconds": 0.1}, "error": None,
    })
    report, path = run_conversation(client, output_dir=tmp_path, **kwargs)
    assert json.loads(path.read_text(encoding="utf-8")) == report
    snapshot = path.parent / f"{report['task_id']}.json"
    assert BlackboardState.model_validate_json(snapshot.read_text()).model_dump(mode="json") == report["board"]
    return client, report


def test_live_path_uses_fresh_history_and_real_scheduler_until_consensus(tmp_path):
    client, report = run(tmp_path, [response(), response("RATIFY"), response("RATIFY")])
    entries = report["board"]["entries"]
    assert report["outcome"] == "scheduler_consensus"
    assert report["scheduler_status"] in ("STOPPED", "ULTRA_STRONG", "STRONG")
    assert report["explanation_review"] == "pending"
    assert report["failures"] == []
    assert [e["agent_id"] for e in entries] == ["proposer", "verifier", "proposer"]
    assert [e["target_entry_id"] for e in entries] == [None, entries[0]["entry_id"], entries[1]["entry_id"]]
    for index, call in enumerate(client.generate.call_args_list):
        prompt = json.loads(call.args[0])
        assert prompt["history"] == entries[:index]
        assert prompt["task"] == report["task"]
        assert prompt["acting_agent_id"] == entries[index]["agent_id"]
        assert report["prompts"]["pxp_template"] in call.kwargs["system"]


def test_revisions_stop_at_budget_without_claiming_agreement(tmp_path):
    client, report = run(tmp_path, [response()] * 4, max_turns=4)
    assert report["outcome"] == "turn_limit"
    assert report["scheduler_status"] in ("RUNNING", "UNRESOLVED")
    assert len(report["board"]["entries"]) == client.generate.call_count == 4


def test_real_board_deadlock_stops_generation(tmp_path):
    client, report = run(tmp_path, [response()] + [response("REFUTE")] * 4)
    assert report["outcome"] == "deadlocked"
    assert report["scheduler_status"] == "DEADLOCKED"
    assert len(report["board"]["deadlocks"]) == 1
    assert client.generate.call_count == 5


@pytest.mark.parametrize(("bad", "category"), [
    ({"response": "{", "done": True}, "malformed_json"),
    (response("AGREE"), "malformed_tag"),
    (response("RATIFY"), "malformed_tag"),  # opening must be REVISE
    (response(agent_id="invented"), "hallucinated_fields"),
    (response(prediction=""), "invalid_fields"),
    ({**response(), "done_reason": "length"}, "incomplete_output"),
])
def test_retry_logs_original_failure_and_posts_only_valid_entry(tmp_path, bad, category):
    _, report = run(tmp_path, [bad, response(), response("RATIFY"), response("RATIFY")])
    assert report["outcome"] == "scheduler_consensus"
    assert report["failures"] == [{"turn": 1, "attempt": 1, "category": category}]
    assert report["turns"][0]["attempts"][0]["raw"] == bad
    assert len(report["board"]["entries"]) == 3


def test_exhausted_validation_preserves_partial_board(tmp_path):
    _, report = run(tmp_path, [response(), response("BROKEN")], max_retries=0)
    assert report["outcome"] == "error"
    assert len(report["board"]["entries"]) == 1
    assert not report["turns"][1]["posted"]
    assert {f["category"] for f in report["failures"]} == {"turn_error", "malformed_tag"}


def test_transport_failure_keeps_earlier_invalid_attempt_and_saves_without_server(tmp_path):
    _, report = run(tmp_path, [response("BROKEN"), RuntimeError("connection lost")])
    assert report["outcome"] == "error"
    assert report["board"]["entries"] == []
    assert len(report["turns"][0]["attempts"]) == 2
    assert {f["category"] for f in report["failures"]} == {"turn_error", "malformed_tag", "transport_error"}


def test_reviewer_rejection_is_posted_and_scheduler_continues(tmp_path):
    client = OllamaClient()
    client.generate = Mock(side_effect=[response(), response(), response("RATIFY"), response("RATIFY")])
    client.check_explanation = Mock(side_effect=[
        {"parsed": {"supported": False, "reason": "No aggregate result supports this value."}, "error": None},
        {"parsed": {"supported": True, "reason": "Supported."}, "error": None},
        {"parsed": {"supported": True, "reason": "Supported."}, "error": None},
        {"parsed": {"supported": True, "reason": "Supported."}, "error": None},
    ])
    events = []
    report, _ = run_conversation(
        client, output_dir=tmp_path, max_retries=0,
        on_rejection=lambda entry, review: events.append((entry.tag.value, review["reason"])),
    )
    tags = [item["tag"] for item in report["board"]["entries"]]
    assert "REJECT" in tags
    assert report["workflow_counters"]["reviewer_rejections"] == 1
    assert events == [("REJECT", "No aggregate result supports this value.")]
    assert report["outcome"] == "scheduler_consensus"


def test_same_target_rejection_streak_runs_one_isolated_counterfactual(tmp_path):
    client = OllamaClient()
    client.generate = Mock(side_effect=[
        response(), response(), response(prediction="alternative"), response(prediction="alternative"),
    ])
    client.check_explanation = Mock(side_effect=[
        {"parsed": {"supported": False, "reason": "Evidence does not support the explanation."}, "error": None},
        {"parsed": {"supported": False, "reason": "Evidence does not support the explanation."}, "error": None},
        {"parsed": {"supported": True, "reason": "Supported."}, "error": None},
        {"parsed": {"supported": True, "reason": "Supported."}, "error": None},
    ])
    report, _ = run_conversation(
        client,
        output_dir=tmp_path,
        max_turns=3,
        max_retries=0,
        counterfactual_fraction=1.0,
        counterfactual_seed=9,
    )
    counters = report["workflow_counters"]
    assert counters["reviewer_rejections"] == 2
    assert counters["counterfactual_triggers"] == 1
    assert counters["counterfactual_simulations"] == 1
    assert counters["counterfactual_changed_outcome"] == 1
    assert len(report["board"]["counterfactual_events"]) == 1
    assert [entry["tag"] for entry in report["board"]["entries"]][:2] == ["REJECT", "REJECT"]
    assert report["board"]["entries"][2]["tag"] == "REVISE"
    simulated = report["board"]["counterfactual_events"][0]["simulated_entry"]
    assert simulated["is_counterfactual_sim"] is True
    assert report["counterfactual_attempts"][0]["simulated_entry"]["is_counterfactual_sim"] is True
    assert report["counterfactual_attempts"][0]["revision_requested"] is True
    assert report["counterfactual_attempts"][0]["live_revision_changed_prediction"] is True
    simulated_prompt = json.loads(client.generate.call_args_list[2].args[0])
    assert "Evidence does not support the explanation." in simulated_prompt["task"]
    assert "Frame the explanation directly around the available evidence" in simulated_prompt["task"]
    assert report["outcome"] == "turn_limit"


def test_counterfactual_fraction_selection_is_seeded_and_zero_is_disabled(tmp_path):
    _, first = run(tmp_path / "one", [response()], max_turns=1,
                    counterfactual_fraction=0.33, counterfactual_seed=123)
    _, second = run(tmp_path / "two", [response()], max_turns=1,
                     counterfactual_fraction=0.33, counterfactual_seed=123)
    assert first["counterfactual_capable_agents"] == second["counterfactual_capable_agents"]
    assert len(first["counterfactual_capable_agents"]) == 1
    _, disabled = run(tmp_path / "zero", [response()], max_turns=1,
                      counterfactual_fraction=0.0, counterfactual_seed=123)
    assert disabled["counterfactual_capable_agents"] == []
    assert disabled["workflow_counters"]["counterfactual_triggers"] == 0


def test_zero_counterfactual_fraction_keeps_rejection_path_without_simulation(tmp_path):
    client = OllamaClient()
    client.generate = Mock(side_effect=[response(), response()])
    client.check_explanation = Mock(side_effect=[
        {"parsed": {"supported": False, "reason": "Not supported."}, "error": None},
        {"parsed": {"supported": False, "reason": "Not supported."}, "error": None},
    ])
    report, _ = run_conversation(
        client, output_dir=tmp_path, max_turns=2, max_retries=0,
        counterfactual_fraction=0.0,
    )
    assert client.generate.call_count == 2
    assert report["workflow_counters"]["reviewer_rejections"] == 2
    assert report["workflow_counters"]["counterfactual_simulations"] == 0
    assert report["board"]["counterfactual_events"] == []


def test_interrupt_saves_partial_transcript(tmp_path):
    _, report = run(tmp_path, [response(), KeyboardInterrupt()])
    assert report["outcome"] == "interrupted"
    assert len(report["board"]["entries"]) == 1


def test_conflicting_ratifications_are_flagged_despite_scheduler_stop(tmp_path):
    _, report = run(tmp_path, [response(), response("RATIFY", "no"), response("RATIFY", "yes")], max_turns=3)
    assert report["outcome"] == "turn_limit"
    assert len(report["failures"]) == 2
    assert all(f["category"] == "ratify_prediction_text_differs" for f in report["failures"])


def test_evidence_guard_retries_at_most_twice_without_changing_model_turn_usage(tmp_path):
    client, report = run(
        tmp_path,
        [response(), response("RATIFY"), response("RATIFY")] * 3,
        initial_facts=["Verified data-tool FACT: complete category counts are available."],
        termination_trace=[{"task_query": "What percentage of observations had an outcome?"}],
        termination_guard=lambda trace, answer: False,
        termination_guard_reason=lambda trace, answer: "refusal_despite_evidence",
    )

    assert report["outcome"] == "consensus_needs_review"
    assert report["workflow_counters"]["guard_retries"] == 2
    assert report["workflow_counters"]["refusal_despite_evidence_retries"] == 2
    assert [item["retry_count"] for item in report["workflow_counters"]["guard_activations"]] == [1, 2]
    assert len([turn for turn in report["turns"] if turn.get("attempts")]) == 9
    # Existing accounting includes both generation and explanation-review calls.
    assert sum(turn["usage"]["calls"] for turn in report["turns"]) == 18
    assert len([entry for entry in report["board"]["entries"] if entry["tag"] == "FACT"]) == 1
    assert len([entry for entry in report["board"]["entries"] if entry["tag"] == "REJECT"]) == 2
    assert client.generate.call_count == 9


@pytest.mark.parametrize("kwargs", [{"max_turns": 0}, {"max_turns": 13}, {"max_turns": True},
                                    {"max_retries": -1}, {"task": " "}])
def test_bad_settings_rejected_before_generation(tmp_path, kwargs):
    client = OllamaClient()
    client.generate = Mock()
    with pytest.raises(ValueError):
        run_conversation(client, output_dir=tmp_path, **kwargs)
    client.generate.assert_not_called()
    assert list(tmp_path.iterdir()) == []
