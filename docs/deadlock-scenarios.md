# Deadlock scenarios

Recorded 18 October 2026. A protocol deadlock means four consecutive `REFUTE`/`REJECT` entries, not merely different answers or a failed model review.

## Reproducible controlled cases

Run from this repository:

```sh
make deadlocks
```

This posts explicit fixture entries through the real round-robin scheduler and blackboard with three registered actors. It makes **zero model calls**. Both recorded cases reached `DEADLOCKED`, with one initial `REVISE` followed by `REFUTE`, `REJECT`, `REFUTE`, `REJECT`. New fixture summaries and board snapshots are saved under the printed `results/sessions/` directory, which remains Git-ignored. The [18 October fixture log](../results/transcripts/deadlock-fixtures-20261018T141802737398Z.json) is an explicit compact archive of both recorded board states.

| Case | Initial error | Injected stalemate | Candidate intervention for later replay |
| --- | --- | --- | --- |
| `missing_premise` | Infers that Noor has a guest pass from membership and a rule saying only some members receive passes. | Objections repeat the missing premise while offering no correction; the original position persists. | Reconsider the first contribution and offer `insufficient information`, explaining that the named individual's status is undetermined. |
| `contradictory_premises` | Calls two notes consistent even though the same box is both empty and not empty at the same instant. | Repeated negative moves reject the position without replacing it. | Reconsider the first contribution and report `inconsistent`, identifying the incompatible premises. |

These fixtures provide deterministic Week 3 replay material. Their assigned tags/claims intentionally create a stalemate. They do not demonstrate that the normal personas naturally produced a deadlock or that a counterfactual agent recovered from one. The original opening entry ID is recorded as the proposed cutoff.

## Other observed failures

- The 6 October philosophy conversation (`conversation-20261006T113210200630Z`) exhausted three explanation-review attempts and ended in `error`. Missing evidence was incorrectly converted into a definite "neither" answer. That is a generation/review failure, not a board deadlock.
- Repeated `REVISE` entries can exhaust the turn limit without four negative tags. Record this as `turn_limit`, not `deadlocked`.
- Different predictions accompanied by `RATIFY` must be reviewed even if the older board announces agreement. The live session batch separately checks every active agent's latest prediction and tag.

The easy live-session batch is reported separately in [PROGRESS_REPORT.md](PROGRESS_REPORT.md). Any naturally occurring deadlock must retain its original prompts, raw outputs, explanation reviews, and board snapshot before being added to this catalogue.
