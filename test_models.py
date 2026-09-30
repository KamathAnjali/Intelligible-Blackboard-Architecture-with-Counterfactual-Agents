import pytest
from pydantic import ValidationError

from blackboard.models import (
    AgentRecord,
    BlackboardState,
    BoardEntry,
    BoardEvent,
    BoardEventType,
    CounterfactualResult,
    DeadlockEvent,
    IntelligibilityLevel,
    PXPTag,
)


def test_board_entry_valid_payload():
    entry = BoardEntry(
        agent_id="agent_1",
        tag=PXPTag.RATIFY,
        prediction="Acute Bronchitis",
        explanation="Symptoms of 3 days cough with no chronic history.",
        target_entry_id="entry_0",
        is_counterfactual_sim=False,
    )
    assert entry.agent_id == "agent_1"
    assert entry.tag == PXPTag.RATIFY
    assert entry.prediction == "Acute Bronchitis"
    assert entry.explanation == "Symptoms of 3 days cough with no chronic history."
    assert entry.target_entry_id == "entry_0"
    assert entry.is_counterfactual_sim is False


def test_board_entry_missing_fields_raise():
    with pytest.raises(ValidationError):
        BoardEntry(agent_id="agent_1", tag=PXPTag.RATIFY)


def test_board_entry_empty_or_whitespace_raises():
    with pytest.raises(ValidationError):
        BoardEntry(
            agent_id="agent_1",
            tag=PXPTag.RATIFY,
            prediction="   ",
            explanation="Valid explanation",
        )

    with pytest.raises(ValidationError):
        BoardEntry(
            agent_id="agent_1",
            tag=PXPTag.RATIFY,
            prediction="Valid prediction",
            explanation="",
        )


def test_board_entry_invalid_tag_raises():
    with pytest.raises(ValidationError):
        BoardEntry(
            agent_id="agent_1",
            tag="INVALID_TAG",  # type: ignore
            prediction="Prediction",
            explanation="Explanation",
        )


def test_board_event_model():
    event = BoardEvent(
        event_type=BoardEventType.ENTRY_POSTED,
        task_id="task_100",
        payload={"entry_id": "e1", "agent_id": "a1"},
    )
    assert event.event_type == BoardEventType.ENTRY_POSTED
    assert event.task_id == "task_100"
    assert event.payload["entry_id"] == "e1"
