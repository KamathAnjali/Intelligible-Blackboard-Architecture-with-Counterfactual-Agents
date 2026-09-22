"""Day 4 compatibility checks, not the Day 5 production message adapter."""

import pytest
from pydantic import ValidationError

from agents.pex import PEXResponse
from blackboard.models import BoardEntry, PXPTag


@pytest.mark.parametrize("tag", list(PXPTag))
def test_pex_fields_fit_board_entry_with_application_metadata(tag):
    pex = PEXResponse(prediction="yes", explanation="The supplied rule applies.")
    # The tag is a test input, not a decision inferred from a PEX response.
    entry = BoardEntry(
        **pex.model_dump(), agent_id="review-agent", tag=tag,
        target_entry_id="existing-entry",
    )
    restored = BoardEntry.model_validate_json(entry.model_dump_json())
    assert restored.prediction == pex.prediction
    assert restored.explanation == pex.explanation
    assert restored.tag == tag
    assert restored.agent_id == "review-agent"
    assert restored.target_entry_id == "existing-entry"
    assert restored.entry_id and restored.timestamp.utcoffset().total_seconds() == 0
    assert restored.is_counterfactual_sim is False


def test_bare_pex_is_not_a_complete_board_entry():
    pex = PEXResponse(prediction="yes", explanation="The supplied rule applies.")
    with pytest.raises(ValidationError) as error:
        BoardEntry.model_validate(pex.model_dump())
    missing = {issue["loc"][0] for issue in error.value.errors() if issue["type"] == "missing"}
    assert missing == {"agent_id", "tag"}
