"""Offline regression tests for bounded explanation-review validation."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.llm_client import OllamaClient
from agents.pex import ExplanationCheck


def ollama_response(text, *, prompt_tokens=10, output_tokens=4):
    return {
        "response": text,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": prompt_tokens,
        "eval_count": output_tokens,
        "wall_seconds": 0.1,
    }


def test_phase_run_long_review_reason_rejects_candidate_then_allows_correction():
    # Captured in the failed genuine run: this 777-character review reason
    # exceeded the old 300-character cap, hiding a valid supported=false
    # decision from the existing PXP correction loop.
    captured_reason = (
        "The dataset does not contain any records where 'County Description' includes "
        "'Massachusetts' as a value. While the data includes beaches in various counties "
        "within Massachusetts (such as Barnstable, Plymouth, Suffolk), the 'County Description' "
        "field does not list 'Massachusetts' as a county. Therefore, the geographic scope of "
        "the query—'Massachusetts beaches'—cannot be satisfied with the available data. As a "
        "result, the requested subset of samples from 'Massachusetts beaches' cannot be "
        "identified, and the violation rate derived from the full dataset does not represent "
        "the specific subset intended by the query. Hence, the result cannot be reliably "
        "attributed to the specified geographic area. Final answer: Cannot be determined due "
        "to geographic mismatch in the dataset."
    )
    assert len(captured_reason) == 777

    rejected_candidate = json.dumps({
        "prediction": "insufficient information",
        "explanation": "The requested population cannot be identified.",
    })
    corrected_candidate = json.dumps({
        "prediction": "4.796",
        "explanation": "The verified task-scoped calculation gives 4.796 percent.",
    })
    review_reject = json.dumps({"supported": False, "reason": captured_reason})
    review_accept = json.dumps({"supported": True, "reason": "Matches verified task evidence."})

    client = OllamaClient()
    client.generate = Mock(side_effect=[
        ollama_response(rejected_candidate),
        ollama_response(review_reject),
        ollama_response(corrected_candidate),
        ollama_response(review_accept),
    ])

    result = client.generate_pex(
        "Use the verified deterministic calculation in the original task context.",
        max_retries=1,
    )

    assert result["parsed"]["prediction"] == "4.796"
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["self_check"]["parsed"]["supported"] is False
    assert len(result["attempts"][0]["self_check"]["parsed"]["reason"]) == 777
    assert result["attempts"][0]["error"] == "Independent explanation review rejected this candidate."
    assert "authoritative task scope and validated evidence" in client.generate.call_args_list[2].kwargs["system"]
    assert "use its value exactly" in client.generate.call_args_list[2].kwargs["system"]
    assert result["usage"] == {
        "calls": 4,
        "prompt_tokens": 40,
        "output_tokens": 16,
        "wall_seconds": 0.4,
    }


def test_review_reason_remains_strictly_bounded_and_typed():
    valid = ExplanationCheck.model_validate({"supported": False, "reason": "x" * 1024})
    assert len(valid.reason) == 1024

    with pytest.raises(ValidationError):
        ExplanationCheck.model_validate({"supported": "false", "reason": "brief"})
    with pytest.raises(ValidationError):
        ExplanationCheck.model_validate({"supported": False, "reason": "x" * 1025})
    with pytest.raises(ValidationError):
        ExplanationCheck.model_validate({"supported": False, "reason": "brief", "extra": True})
