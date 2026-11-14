# LLM usage event contract (request for Student 3)

Student 4 owns the ledger, UI bridge, and reporting. Student 3 owns the LLM client and conversation call sites. The frontend must keep provider values and estimates in separate fields; an estimate must never be presented as provider-reported usage.

## UsageRecord

Emit one record for every completed model call, including proposer generation, self-check/review, retries, counterfactual simulations, data-agent calls, and other model calls. A failed transport with no provider usage may emit an `estimated` record only when a documented estimate exists; otherwise omit token values rather than inventing zero usage.

```json
{
  "session_id": "session-uuid",
  "task_id": "task-id-or-null",
  "agent_id": "verifier",
  "call_kind": "self_check",
  "branch_id": null,
  "prompt_tokens": 420,
  "completion_tokens": 31,
  "total_tokens": 451,
  "source": "provider",
  "model": "configured-model-tag",
  "started_at": "2026-11-11T09:00:00Z",
  "duration_ms": 840
}
```

`call_kind` is one of `proposer`, `self_check`, `retry`, `counterfactual_sim`, or `other`. For a simulation, set `call_kind=counterfactual_sim` and populate `branch_id` when the sandbox has a stable branch identifier. `source` is `provider` only when prompt/completion counts came from provider response metadata; otherwise it is `estimated`. Counts are non-negative integers and `total_tokens == prompt_tokens + completion_tokens`.

## Callback interface requested

Add an optional `on_usage(record: UsageRecord | Mapping[str, Any]) -> None` callback to `OllamaClient` and `run_conversation`, with one normalized callback record per actual call. `OllamaClient` should report the provider response's prompt and completion counts and elapsed duration. `run_conversation` should attach agent identity, call kind, session/task identity, and counterfactual branch identity, and preserve callback records in its transcript. Make sure retries and self-checks each produce separate events; do not emit a second aggregate event for the same call. Keep the callback optional for existing callers.

If one client serves multiple sessions, bind the callback per request/session context rather than storing mutable global callback state on the shared client. The callback must be invoked for the same calls counted in the task record's `provider_token_usage` aggregate, so the sum of event records can be checked against that aggregate.

## Student 3 implementation request

Please implement the optional `on_usage` callback in `agents/llm_client.py` and `agents/conversation.py` as described above. Emit one event per provider call, including proposer, independent explanation self-check, each retry, each counterfactual simulation call, and data/pipeline calls routed through `OllamaClient`. Attach `session_id`, `task_id`, `agent_id`, `call_kind`, and `branch_id` where known; use `source=provider` only for returned provider counts. Preserve callback records in transcripts and verify their totals equal the task record aggregate without exposing task gold/reference data. Student 4 has implemented a feature-detected consumer; callers without this callback continue to use explicitly labelled entry-text estimates.
