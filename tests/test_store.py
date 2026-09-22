import json
import pytest
from pathlib import Path

from blackboard.models import BlackboardState, BoardEntry, AgentRecord, PXPTag
from blackboard.store import InMemoryJSONStore

@pytest.fixture
def temp_store(tmp_path):
    # Use pytest's tmp_path fixture for isolated snapshot testing
    return InMemoryJSONStore(snapshot_dir=tmp_path)

def test_store_empty_board(temp_store, tmp_path):
    """Test saving and loading a board with no entries."""
    task_id = "task_empty"
    state = BlackboardState(task_id=task_id)
    
    # Save to memory and disk
    temp_store.save(state)
    temp_store.snapshot_to_disk(task_id)
    
    # Load back
    loaded_state = temp_store.load_snapshot_from_disk(task_id)
    assert loaded_state.task_id == task_id
    assert len(loaded_state.entries) == 0
    assert len(loaded_state.agents) == 0

def test_store_populated_board_round_trip(temp_store, tmp_path):
    """Test saving and loading a populated board state."""
    task_id = "task_populated"
    state = BlackboardState(task_id=task_id)
    
    # Add some agents and entries
    agent = AgentRecord(agent_id="agent1", persona="cautious")
    state.agents[agent.agent_id] = agent
    
    entry = BoardEntry(
        agent_id="agent1",
        tag=PXPTag.RATIFY,
        prediction="Test prediction",
        explanation="Test explanation"
    )
    state.entries.append(entry)
    
    # Save to memory and disk
    temp_store.save(state)
    temp_store.snapshot_to_disk(task_id)
    
    # Load into a fresh store to verify it actually reads from disk
    new_store = InMemoryJSONStore(snapshot_dir=tmp_path)
    loaded_state = new_store.load_snapshot_from_disk(task_id)
    
    # Verify contents
    assert loaded_state.task_id == task_id
    assert len(loaded_state.entries) == 1
    assert loaded_state.entries[0].entry_id == entry.entry_id
    assert loaded_state.entries[0].prediction == "Test prediction"
    assert "agent1" in loaded_state.agents

def test_store_corrupted_file_raises(temp_store, tmp_path):
    """Test that attempting to load a corrupted JSON file raises an error."""
    task_id = "task_corrupted"
    file_path = tmp_path / f"{task_id}.json"
    
    # Write invalid JSON manually
    file_path.write_text("{ malformed json }")
    
    # Should raise JSONDecodeError when attempting to load
    with pytest.raises(json.JSONDecodeError):
        temp_store.load_snapshot_from_disk(task_id)
