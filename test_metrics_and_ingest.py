"""
Unit tests for Student 4 deliverables:
- bench/metrics/token_counter.py (TokenTallyTracker & LLM adapter)
- bench/ingest/krama_parser.py (TaskFormat, dataset loading & live feeding)
"""

import pytest
from bench.metrics.token_counter import (
    count_tokens,
    count_tokens_for_entry,
    llm_token_count_adapter,
    TokenTallyTracker,
    register_custom_tokenizer,
)
from bench.ingest.krama_parser import (
    TaskFormat,
    parse_krama_record,
    load_krama_dataset,
    feed_tasks_to_live_board,
)


def test_token_counter_basic():
    text = "The quick brown fox jumps over the lazy dog."
    cnt = count_tokens(text, agent_id="agent-1")
    assert cnt > 0
    assert isinstance(cnt, int)


def test_token_counter_entry():
    res = count_tokens_for_entry(
        prediction="x = 12",
        explanation="12 * 12 = 144 is verified.",
        agent_id="Agent_Beta",
    )
    assert res["prediction_tokens"] > 0
    assert res["explanation_tokens"] > 0
    assert res["total_tokens"] == res["prediction_tokens"] + res["explanation_tokens"]


def test_llm_adapter():
    # Test Pattern A (Usage dict)
    usage_dict = {"usage": {"prompt_tokens": 150, "completion_tokens": 40, "total_tokens": 190}}
    res = llm_token_count_adapter(usage_dict)
    assert res["prompt_tokens"] == 150
    assert res["completion_tokens"] == 40
    assert res["total_tokens"] == 190

    # Test Pattern C (Fallback text)
    res2 = llm_token_count_adapter(None, prompt_text="Solve x^2 = 144", completion_text="x = 12")
    assert res2["prompt_tokens"] > 0
    assert res2["completion_tokens"] > 0
    assert res2["total_tokens"] == res2["prompt_tokens"] + res2["completion_tokens"]


def test_token_tally_tracker():
    tracker = TokenTallyTracker()
    tracker.record_turn("e1", "Agent_Alpha", "PROPOSE", "x = 12", "Initial hypothesis")
    tracker.record_turn("e2", "Agent_Beta", "RATIFY", "x = 12", "Confirmed")

    report = tracker.export_tally_report()
    assert report["turn_count"] == 2
    assert report["total_tokens"] > 0
    assert "Agent_Alpha" in report["agents"]
    assert "Agent_Beta" in report["agents"]
    assert report["agents"]["Agent_Alpha"]["turn_count"] == 1
    assert report["agents"]["Agent_Alpha"]["tags_used"]["PROPOSE"] == 1


def test_krama_parser_and_live_feed():
    task = parse_krama_record({
        "task_id": "test_t1",
        "problem_statement": "What is the square root of 144?",
        "expected_answer": "12",
        "domain": "math",
    })
    assert task.task_id == "test_t1"
    assert task.expected_answer == "12"

    # Test feed_tasks_to_live_board (offline / without server)
    states = feed_tasks_to_live_board([task], interval_s=0.01, emit_to_bus=False)
    assert len(states) == 1
    assert len(states[0].entries) >= 2
