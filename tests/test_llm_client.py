"""Offline tests: malformed output and retry limits must not depend on a live LLM."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.llm_client import OllamaClient, PERSONAS
from agents.pex import PEXGenerationError, PEXResponse


def response(text, done=True, reason="stop"):
    return {"response": text, "done": done, "done_reason": reason,
            "eval_count": 10, "wall_seconds": 0.1}


VALID = json.dumps({"prediction": "yes", "explanation": "The supplied rule applies."})


@pytest.mark.parametrize("text", [
    "not JSON", "```json\n" + VALID + "\n```", "[]", "null",
    '{"prediction": "yes"}',
    '{"prediction": 1, "explanation": "why"}',
    '{"prediction": "yes", "explanation": null}',
    '{"prediction": "  ", "explanation": "why"}',
    '{"prediction": "yes", "explanation": "  "}',
    '{"prediction": "yes", "explanation": "why", "tag": "RATIFY"}',
])
def test_schema_rejects_malformed_pex(text):
    with pytest.raises(ValidationError):
        PEXResponse.model_validate_json(text)


def test_valid_answer_is_normalized():
    parsed = PEXResponse(prediction=" yes ", explanation=" evidence ")
    assert parsed.model_dump() == {"prediction": "yes", "explanation": "evidence"}


def test_schema_is_sent_to_ollama_and_raw_query_stays_unconstrained():
    client = OllamaClient()
    client.request = Mock(side_effect=lambda *args: response(VALID))
    result = client.generate_pex("Is the conclusion supported?")
    payload = client.request.call_args.args[1]
    assert payload["format"]["additionalProperties"] is False
    assert set(payload["format"]["required"]) == {"prediction", "explanation"}
    assert result["parsed"]["prediction"] == "yes"
    assert len(result["attempts"]) == 1
    client.generate("Raw question")
    assert "format" not in client.request.call_args.args[1]


def test_malformed_response_retried_with_feedback_and_history_retained():
    client = OllamaClient()
    client.generate = Mock(side_effect=[response("bad JSON"), response(VALID)])
    result = client.generate_pex("Original task")
    assert client.generate.call_count == 2
    assert all(call.args[0] == "Original task" for call in client.generate.call_args_list)
    assert "failed output validation" in client.generate.call_args.kwargs["system"]
    assert result["attempts"][0]["raw"]["response"] == "bad JSON"
    assert result["attempts"][0]["error"]
    assert result["attempts"][1]["error"] is None


@pytest.mark.parametrize("retries", [0, 2])
def test_retry_budget_is_bounded_and_exhaustion_keeps_raw_attempts(retries):
    client = OllamaClient()
    client.generate = Mock(return_value=response("bad"))
    with pytest.raises(PEXGenerationError) as error:
        client.generate_pex("task", max_retries=retries)
    assert client.generate.call_count == retries + 1
    assert len(error.value.attempts) == retries + 1


@pytest.mark.parametrize("first", [response(VALID, reason="length"), response(VALID, done=False), response(None)])
def test_incomplete_or_missing_text_is_retried_even_if_json_would_be_valid(first):
    client = OllamaClient()
    client.generate = Mock(side_effect=[first, response(VALID)])
    result = client.generate_pex("task")
    assert len(result["attempts"]) == 2


def test_connection_error_is_not_retried_as_bad_json():
    client = OllamaClient()
    client.generate = Mock(side_effect=RuntimeError("Ollama unavailable"))
    with pytest.raises(RuntimeError, match="unavailable"):
        client.generate_pex("task")
    assert client.generate.call_count == 1


@pytest.mark.parametrize("persona", PERSONAS)
def test_persona_is_combined_with_shared_output_contract(persona):
    client = OllamaClient()
    client.generate = Mock(return_value=response(VALID))
    client.generate_pex("task", persona=persona)
    system = client.generate.call_args.kwargs["system"]
    assert persona.replace("_", " ") in system
    assert "prediction" in system and "explanation" in system


@pytest.mark.parametrize("options", [{"persona": "unknown"}, {"max_retries": -1}, {"max_retries": 6}])
def test_invalid_options_fail_before_inference(options):
    client = OllamaClient()
    client.generate = Mock()
    with pytest.raises(ValueError):
        client.generate_pex("task", **options)
    client.generate.assert_not_called()
