# Prompt library v1

Frozen 18 October 2026 on `3-Dhruva`. This freezes the seven existing prompt files; it does not claim that their reasoning is error-free. No persona or review wording was changed for this checkpoint.

| File | Role |
| --- | --- |
| `aggressive_proposer.txt` | Propose and test a concrete, supported answer. |
| `cautious_verifier.txt` | Check whether the evidence supports the inference. |
| `evidence_auditor.txt` | Trace claims to original facts and identify missing premises. |
| `counterexample_challenger.txt` | Test genuine counterexamples and alternative answers. |
| `pex_template.txt` | Require exactly prediction and explanation. |
| `pxp_template.txt` | Add RATIFY/REVISE/REFUTE/REJECT while leaving metadata to code. |
| `explanation_check.txt` | Review the answer against the task, separately from board claims. |

The live batch records prompt SHA-256 fingerprints and exact prompt contents in its session transcripts. The freeze fingerprints below use UTF-8 text with LF newlines, so Windows/Linux line endings do not create a different version. Any wording change must be identified as a subsequent prompt version and its evidence rerun.

## Known limits

- The reviewer is the same Qwen model as the actor. It can accept an unsupported claim or reject equivalent reasoning inconsistently. A passed self-check is not an independent correctness judgment.
- Review is restricted to stated task facts. Open-ended philosophy and other knowledge questions may require facts not present in the question. Such sessions are outside the validated closed-premise workload unless the required knowledge is supplied explicitly.
- Missing support for a claim does not prove its negation. The philosophy run exposed the erroneous transition from "not established" to "neither".
- During the 18 October live workload, the first inventory candidate returned prediction `Well` instead of an integer and claimed insufficient information despite explicit counts. The reviewer rejected it, and regeneration returned `17`. Schema-valid text can still violate the requested answer vocabulary and facts.
- REFUTE and REJECT overlap in natural language. Prefer REVISE whenever a corrected position, including insufficient information, is available.
- Exact prediction strings can differ even when meanings match. The small session workload requests a fixed answer vocabulary; its scoring is not a general semantic evaluator.
- Generation is capped at 256 tokens on this branch. Truncation is rejected and retried. The integrated branch uses 768, so results from those settings must not be silently pooled.
- A model may follow another actor's incorrect claim. History is labelled as claims, and the reviewer sees the original task, but these instructions do not guarantee resistance.
- Context is 4096 tokens. Long histories may exceed the useful context budget; this checkpoint covers bounded short sessions only.

## Reproduction

```sh
make sessions TURNS=6 RETRIES=2
make deadlocks
```

The live workload has six distinct tasks, three actors, no synthetic starting answer, temperature 0, seed 42, and at most six turns per case. Expected answers are used only after inference. Retry failures stay in the transcript. Model agreement and expected-answer agreement are reported separately. This is a small synthetic check, not a benchmark or a claim about general accuracy.

The sandbox interface is in [agents/counterfactual.py](../agents/counterfactual.py), with semantics and pending shared changes in [rollback-contract.md](rollback-contract.md).

## Frozen fingerprints

```text
aggressive_proposer.txt      4af4c36f8ca30a1eeb5df1f9db8564959211f953c2e1ee1902bfcb758dbf5760
cautious_verifier.txt        fa2f0bff44772f2b24494bfe2df9514c070858b61827733d1a97a8d623170913
counterexample_challenger.txt 843b02f8c609a07b2cdc9d4ed765478b3f0755ae1484fbc5b5e5f32590b7ad4b
evidence_auditor.txt         3b9ef87b43b5907edb3d153b8cd0c7410e07cd06f19e6ebba24c53dc1015fac3
explanation_check.txt        0c66bc79cf340f9c375a090606bdfd9f087fcdad416ea7f52699f6a49eb9c45e
pex_template.txt             b0691354e3d9d238670233f61f3d9611b5c563b8d3b7fee75f459a713155daec
pxp_template.txt             327318ecdd2af733e6f12ede59f32c6b10c45c4493b522dc406c3b38f2703d78
```
