"""Local inference harness: raw text plus Day 3 validated PEX generation.

Run from the repository root: python -m agents.llm_client --help
"""

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from agents.pex import PEXGenerationError, PEXResponse

MODEL = "qwen3:4b-instruct-2507-q4_K_M"
BASE_URL = "http://127.0.0.1:11434"
OPTIONS = {"num_ctx": 4096, "temperature": 0, "seed": 42, "num_predict": 256}
ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ("cautious_verifier", "aggressive_proposer")


class OllamaClient:
    def __init__(self, model=MODEL, base_url=BASE_URL):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=300) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"Ollama HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Cannot complete request to {self.base_url}. Open Ollama and check the model is installed: {exc}") from exc
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def generate(self, prompt, system="", schema=None):
        """Return raw model text and timing metadata; no JSON repair or retries."""
        started = time.perf_counter()
        payload = {
            "model": self.model, "prompt": prompt, "system": system,
            "stream": False, "keep_alive": "10m", "options": OPTIONS,
        }
        if schema is not None:
            payload["format"] = schema
        result = self.request("/api/generate", payload)
        result["wall_seconds"] = time.perf_counter() - started
        return result

    def generate_pex(self, prompt, persona=None, max_retries=2):
        """Validate every answer; retry malformed/truncated output only.

        The result contains parsed PEX, final raw output, and every attempt's
        raw output/timing. Semantic correctness requires separate evaluation.
        Transport errors propagate without being retried as formatting errors.
        """
        if type(max_retries) is not int or not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be an integer between 0 and 5")
        if persona is not None and persona not in PERSONAS:
            raise ValueError(f"Unknown persona: {persona}")
        prompts = ROOT / "agents/prompts"
        system = (prompts / "pex_template.txt").read_text(encoding="utf-8")
        if persona:
            system = (prompts / f"{persona}.txt").read_text(encoding="utf-8") + "\n\n" + system
        attempts = []
        feedback = ""
        for _ in range(max_retries + 1):
            raw = self.generate(prompt, system=system + feedback, schema=PEXResponse.model_json_schema())
            error = None
            try:
                if raw.get("done") is not True or raw.get("done_reason") == "length":
                    raise ValueError("Response incomplete or truncated; use a shorter complete JSON answer.")
                if not isinstance(raw.get("response"), str):
                    raise ValueError("Response text is missing or is not a string.")
                parsed = PEXResponse.model_validate_json(raw["response"])
            except ValidationError as exc:
                error = "; ".join(
                    f"{'.'.join(map(str, issue['loc'])) or 'JSON'}: {issue['msg']}"
                    for issue in exc.errors(include_input=False, include_url=False)
                )
            except ValueError as exc:
                error = str(exc)
            attempts.append({"raw": raw, "error": error})
            if error is None:
                return {"parsed": parsed.model_dump(), "raw": raw, "attempts": attempts}
            feedback = (
                f"\n\nAttempt {len(attempts)} failed output validation: {error}\n"
                "Answer the original task again. Return only the required JSON with two nonempty strings."
            )
        raise PEXGenerationError(attempts)


def timings(result):
    generation_seconds = result.get("eval_duration", 0) / 1e9
    return {
        "wall_seconds": round(result["wall_seconds"], 3),
        "server_seconds": round(result.get("total_duration", 0) / 1e9, 3),
        "load_seconds": round(result.get("load_duration", 0) / 1e9, 3),
        "prompt_seconds": round(result.get("prompt_eval_duration", 0) / 1e9, 3),
        "output_tokens": result.get("eval_count", 0),
        "tokens_per_second": round(result.get("eval_count", 0) / generation_seconds, 2) if generation_seconds else None,
        "done_reason": result.get("done_reason"),
    }


def save_report(kind, client, records):
    directory = ROOT / "results" / "ollama"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    path = directory / f"{kind}-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    report = {
        "created_at": stamp.isoformat(), "model": client.model,
        "base_url": client.base_url, "platform": platform.platform(),
        "options": OPTIONS, "ollama": client.request("/api/version"),
        "loaded_models": client.request("/api/ps"), "records": records,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {path}")
    return path


def benchmark(client, runs):
    # An explicit unload separates model loading from warm inference. OS file
    # caches may still be warm; this is not a disk-cold or first-token benchmark.
    client.request("/api/generate", {"model": client.model, "keep_alive": 0})
    prompt = "In about 80 words, explain how a shared blackboard helps several software agents solve a problem."
    records = []
    for index in range(runs + 1):
        phase = "first_after_unload" if index == 0 else f"warm_{index}"
        print(f"Running {phase}...", flush=True)
        result = client.generate(prompt)
        metrics = timings(result)
        records.append({"phase": phase, "prompt": prompt, "metrics": metrics, "raw": result})
        print(json.dumps(metrics), flush=True)
    warm = [row["metrics"]["wall_seconds"] for row in records[1:]]
    print(f"Warm wall latency: median={statistics.median(warm):.3f}s, min={min(warm):.3f}s, max={max(warm):.3f}s")
    print("Repeated prompts may use prompt caching. Compare identical settings on the same laptop.")
    save_report("latency", client, records)


def pex_failures(text, done_reason, expected):
    """Offline sample checks; not production validation or automatic retry."""
    failures = []
    if done_reason == "length":
        failures.append("output_truncated")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return failures + ["invalid_json"], None
    if not isinstance(parsed, dict):
        return failures + ["not_a_json_object"], parsed
    if set(parsed) != {"prediction", "explanation"}:
        failures.append("missing_or_extra_fields")
    for field in ("prediction", "explanation"):
        if not isinstance(parsed.get(field), str) or not parsed[field].strip():
            failures.append(f"invalid_{field}")
    if parsed.get("prediction") != expected:
        failures.append("incorrect_prediction")
    return failures, parsed


def samples(client):
    template = (ROOT / "agents/prompts/pex_template.txt").read_text(encoding="utf-8")
    cases = json.loads((ROOT / "agents/prompts/sample_tasks.json").read_text(encoding="utf-8"))
    records = []
    for case in cases:
        print(f"Running {case['id']}...", flush=True)
        result = client.generate(case["task"], system=template)
        failures, parsed = pex_failures(result["response"], result.get("done_reason"), case["expected"])
        records.append({**case, "failures": failures, "parsed": parsed,
                        "explanation_review": "pending", "metrics": timings(result), "raw": result})
        print(result["response"], flush=True)
        print(f"Checks: {', '.join(failures) if failures else 'passed'}; explanation requires human review.", flush=True)
    save_report("pex-samples", client, records)
    print("Inspect every raw response for unsupported claims, faulty reasoning, and irrelevant explanation.")
    return int(any(row["failures"] for row in records))


def main():
    # Model output may contain Unicode unsupported by Windows legacy code pages.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["query", "latency", "samples", "pex"])
    parser.add_argument("--persona", choices=PERSONAS, default="cautious_verifier")
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument("--prompt")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--runs", type=int, default=3, help="Number of warm requests for latency")
    args = parser.parse_args()
    if args.prompt is None:
        args.prompt = (
            "All tulips are plants. This item is a tulip. Is it a plant? Use yes or no as prediction."
            if args.command == "pex" else "Explain a blackboard architecture in three sentences."
        )
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    client = OllamaClient(args.model, args.base_url)
    try:
        if args.command == "query":
            result = client.generate(args.prompt)
            print(result["response"])
            print(json.dumps(timings(result), indent=2))
        elif args.command == "latency":
            benchmark(client, args.runs)
        elif args.command == "pex":
            record = {"prompt": args.prompt, "persona": args.persona, "max_retries": args.retries}
            try:
                result = client.generate_pex(args.prompt, args.persona, args.retries)
            except PEXGenerationError as exc:
                record.update({"attempts": exc.attempts, "error": str(exc)})
                save_report("pex", client, [record])
                raise
            record.update(result)
            print(json.dumps(result["parsed"], ensure_ascii=False, indent=2))
            print(f"Validated after {len(result['attempts'])} attempt(s).")
            save_report("pex", client, [record])
        else:
            return samples(client)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
