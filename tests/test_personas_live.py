"""Day 4: two personas x three tasks, opt in with --run-ollama.

These are live model checks, not deterministic unit tests. No retries are
allowed here so schema failures cannot be hidden by successful recovery.
"""

import json
from datetime import datetime, timezone

import pytest

from agents.llm_client import MODEL, OPTIONS, PERSONAS, ROOT, OllamaClient
from agents.pex import PEXGenerationError, PEXResponse


CASE_IDS = ("deduction", "insufficient_evidence", "contradiction")
ALL_CASES = json.loads((ROOT / "agents/prompts/sample_tasks.json").read_text(encoding="utf-8"))
CASES = [next(case for case in ALL_CASES if case["id"] == case_id) for case_id in CASE_IDS]


@pytest.fixture(scope="module")
def live_run(request):
    if not request.config.getoption("--run-ollama"):
        pytest.skip("Use make personas or pytest --run-ollama to call the local model")
    client = OllamaClient()
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "base_url": client.base_url, "options": OPTIONS,
        "max_retries": 0,
        "prompts": {
            name: (ROOT / "agents/prompts" / name).read_text(encoding="utf-8")
            for name in ("pex_template.txt", *(f"{persona}.txt" for persona in PERSONAS))
        },
        "records": [],
    }
    try:
        yield client, report["records"]
    finally:
        # Preserve all case failures even if Ollama goes away during the run.
        for key, endpoint in (("ollama", "/api/version"), ("loaded_models", "/api/ps")):
            try:
                report[key] = client.request(endpoint)
            except (RuntimeError, OSError, ValueError) as exc:
                report[key] = {"unavailable": str(exc)}
        directory = ROOT / "results/ollama"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"day4-personas-{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nDay 4 report: {path}")


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_persona_outputs_valid_pex(live_run, persona, case):
    client, records = live_run
    record = {
        **case, "persona": persona, "schema_valid": False,
        "answer_correct": False, "explanation_review": "pending",
    }
    records.append(record)
    try:
        result = client.generate_pex(case["task"], persona=persona, max_retries=0)
        record.update(result)
        # Check the actual response, not only the harness's parsed projection.
        parsed = PEXResponse.model_validate_json(result["raw"]["response"])
        record["schema_valid"] = True
        record["answer_correct"] = parsed.prediction == case["expected"]
        print(f"\n{persona}/{case['id']}: {result['raw']['response']}")
        assert record["answer_correct"], f"Expected {case['expected']!r}; got {parsed.prediction!r}"
    except PEXGenerationError as exc:
        record.update({"error": str(exc), "attempts": exc.attempts})
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        record["error"] = str(exc)
        raise
