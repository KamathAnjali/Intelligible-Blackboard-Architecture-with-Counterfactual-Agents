import pytest
from pydantic import ValidationError

from blackboard.models import BoardEntry, PXPTag

def test_valid_payload_passes():
    """A fully valid payload should parse successfully."""
    payload = {
        "agent_id": "agent_alpha",
        "tag": "RATIFY",
        "prediction": "The patient has acute bronchitis.",
        "explanation": "Symptoms match.",
        "target_entry_id": "msg_001",
        "is_counterfactual_sim": False
    }
    entry = BoardEntry(**payload)
    assert entry.agent_id == "agent_alpha"
    assert entry.tag == PXPTag.RATIFY
    assert entry.prediction == "The patient has acute bronchitis."
    assert entry.explanation == "Symptoms match."
    assert entry.target_entry_id == "msg_001"
    assert entry.is_counterfactual_sim is False

def test_missing_field_raises():
    """Missing a required field like 'prediction' should raise ValidationError."""
    payload = {
        "agent_id": "agent_alpha",
        "tag": "RATIFY",
        # "prediction" is missing
        "explanation": "Symptoms match."
    }
    with pytest.raises(ValidationError) as exc_info:
        BoardEntry(**payload)
    assert "prediction" in str(exc_info.value)

def test_wrong_type_raises():
    """Passing a wrong type (e.g. invalid tag enum) should raise ValidationError."""
    payload = {
        "agent_id": "agent_alpha",
        "tag": "NOT_A_VALID_TAG", # invalid
        "prediction": "The patient has acute bronchitis.",
        "explanation": "Symptoms match."
    }
    with pytest.raises(ValidationError) as exc_info:
        BoardEntry(**payload)
    assert "tag" in str(exc_info.value)

def test_malformed_input_empty_explanation_raises():
    """Custom validator ensures prediction/explanation are not empty strings."""
    payload = {
        "agent_id": "agent_alpha",
        "tag": "RATIFY",
        "prediction": "Valid prediction.",
        "explanation": "   " # empty/whitespace
    }
    with pytest.raises(ValidationError) as exc_info:
        BoardEntry(**payload)
    assert "cannot be empty" in str(exc_info.value)
