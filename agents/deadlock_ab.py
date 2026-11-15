"""Run a paired, controlled deadlock comparison with local Ollama agents."""

import argparse
import json
import time
from datetime import datetime, timezone

from agents.conversation import run_conversation
from agents.llm_client import BASE_URL, MODEL, ROOT, OllamaClient
from blackboard.models import BoardEntry, PXPTag

TASK = ("Some museum members receive a guest pass. Noor is a museum member. "
        "Do these facts prove that Noor receives a guest pass? "
        "Answer yes, no, or insufficient information.")
EXPECTED = "insufficient information"  # Evaluated after inference only.
SEED_TAGS = (PXPTag.REVISE, PXPTag.REFUTE, PXPTag.REJECT, PXPTag.REFUTE, PXPTag.REJECT)


class InjectedDeadlockClient:
    """Supply the same five controlled entries, then use the real client."""

    def __init__(self, client):
        self.client = client
        self.model, self.base_url, self.options = client.model, client.base_url, client.options
        self.injected = 0

    def generate_entry(self, task, state, agent_id, **kwargs):
        if self.injected < len(SEED_TAGS) and not kwargs.get("is_counterfactual_sim", False):
            tag = SEED_TAGS[self.injected]
            self.injected += 1
            entry = BoardEntry(agent_id=agent_id, tag=tag, prediction="yes",
                               explanation="The facts do not establish whether Noor receives a pass."
                               if tag != PXPTag.REVISE else "Every member receives a pass.",
                               target_entry_id=state.entries[0].entry_id if state.entries else None)
            return {"entry": entry, "attempts": [], "raw": {"wall_seconds": 0}}
        return self.client.generate_entry(task, state, agent_id, **kwargs)


def run_pair(client, directory, turns, retries):
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"scenario": "missing_premise", "task": TASK, "expected": EXPECTED,
               "source": "five controlled entries followed by local model calls",
               "model": client.model, "options": dict(client.options), "runs": []}
    path = directory / "summary.json"
    for enabled in (False, True):
        label = "enabled" if enabled else "disabled"
        start = time.monotonic()
        try:
            report, transcript = run_conversation(
                InjectedDeadlockClient(client), TASK, turns, retries, directory / label,
                counterfactual_fraction=1.0 if enabled else 0.0,
                retrospective_recovery=enabled,
            )
            for turn in report["turns"][:len(SEED_TAGS)]:
                turn["source"] = "controlled_injected_entry"
            transcript.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            active = {k for k, v in report["board"]["agents"].items() if v["active"]}
            latest = {e["agent_id"]: e for e in report["board"]["entries"] if e["agent_id"] in active}
            correct = (report["outcome"] == "scheduler_consensus" and set(latest) == active
                       and all(e["tag"] == "RATIFY" and e["prediction"].strip().casefold() == EXPECTED
                               for e in latest.values()))
            live_calls = sum(t.get("usage", {}).get("calls", 0) for t in report["turns"])
            sim_calls = sum((a.get("usage") or {}).get("calls", 0) for a in report["counterfactual_attempts"])
            record = {"condition": label, "outcome": report["outcome"], "correct_consensus": correct,
                      "turns": len(report["turns"]), "live_model_calls": live_calls,
                      "simulation_model_calls": sim_calls, "counterfactual_attempts": report["counterfactual_attempts"],
                      "failures": report["failures"], "transcript": str(transcript)}
        except Exception as exc:
            record = {"condition": label, "outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
        record["wall_seconds"] = round(time.monotonic() - start, 3)
        summary["runs"].append(record)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{label}: {record['outcome']} (correct consensus: {record.get('correct_consensus', False)})", flush=True)
    print(f"Report: {path}", flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, choices=range(6, 13), default=10)
    parser.add_argument("--retries", type=int, choices=range(6), default=1)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    summary = run_pair(OllamaClient(args.model, args.base_url), ROOT / "results/sessions" / f"deadlock-ab-{stamp}", args.turns, args.retries)
    return 1 if any(run["outcome"] in {"error", "interrupted"} for run in summary["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
