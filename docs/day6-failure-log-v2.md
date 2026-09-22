# Failure-mode log v2

This log records what the Day 6 conversation runner detects automatically and
what still needs human review. Generated raw transcripts are Git-ignored under
`results/conversations/`; this document captures the durable findings.

## Live run: 2026-09-22

The first two-agent tulip run completed in three turns with no observed model
format failures: no malformed JSON, invalid tag, extra field, truncation,
transport error, or off-topic explanation. Both explanations were reviewed as
grounded in the supplied tulip rule. The raw evidence is named in
[the Day 6 run log](day6-live-run.md).

## Categories and handling

| Category | Detection | Runner handling | Follow-up |
| --- | --- | --- | --- |
| `malformed_json` | Structured response cannot parse as JSON | Retry within the configured budget; record raw attempt | Adjust prompt on Day 7 if seen live |
| `malformed_tag` | Tag absent, invalid, or opening response uses a non-REVISE tag | Retry; do not post the invalid entry | Review persona/template instruction |
| `hallucinated_fields` | Strict PXP schema rejects unknown fields | Retry; application still owns entry metadata | Keep the metadata prohibition in prompt |
| `invalid_fields` | Empty or structurally invalid prediction/explanation | Retry; do not post it | Check whether answer vocabulary needs clearer constraints |
| `incomplete_output` | Ollama reports a length-limited or unfinished response | Retry; preserve attempt in transcript | Shorten prompt or increase output limit deliberately |
| `transport_error` | Local Ollama request fails | Save partial transcript and stop; no fabricated entry | Restart service or diagnose device/runtime |
| `ratify_prediction_text_differs` | RATIFY prediction text differs from its target after normalization | Flag session as `consensus_needs_review` | Human decides whether wording is semantically equivalent |
| Off-topic or unsupported explanation | Not reliably detectable from JSON shape alone | Mark transcript review as pending | Review task facts, prediction, and explanation together |

The current blackboard consensus classifier is intentionally simple: terminal
RATIFY tags trigger its status. The conversation runner adds the prediction-text
check above, but correctness, explanation relevance, and true agreement remain
review tasks. Day 7 should use actual live failures from additional varied tasks
to tune prompts; it should not tune from fabricated failures.
