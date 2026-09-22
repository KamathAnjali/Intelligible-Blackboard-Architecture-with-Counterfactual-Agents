# Day 2 inference observations

Initial run: 2026-09-20, Windows, Ollama 0.34.2,
`qwen3:4b-instruct-2507-q4_K_M`, digest prefix `0edcdef34593`.
Settings: context 4096, temperature 0, seed 42, maximum 256 output tokens.

## Five sample tasks

| Task | Expected / actual prediction | Format checks | Explanation review |
| --- | --- | --- | --- |
| Arithmetic | `12` / `12` | Passed | Correctly adds 7 and 5. |
| Deduction | `yes` / `yes` | Passed | Correctly applies the rule about tulips. |
| Insufficient evidence | `insufficient information` / same | Passed | Correctly distinguishes some birds from all birds. |
| Contradiction | `inconsistent` / same | Passed | Identifies mutually contradictory claims at the same time. |
| Constraint | `5` / `5` | Passed | Correctly selects the only odd number greater than 3. |

Assistant inspection found no incorrect or irrelevant explanations in this run.
No formatting failures, extra fields, or truncated responses were observed.
These are five easy smoke tests; do not interpret them as a quality benchmark.
The raw JSON report is in the local, Git-ignored `results/ollama/` directory.
Every new run needs its own explanation review; the runner leaves review pending.

## Setup issue observed and fixed

A normal chat reply containing an emoji triggered a Windows console encoding
error while printing from Python. The model request itself succeeded. The CLI
now uses UTF-8 for standard output, and a repeated emoji request printed correctly.

## Latency baselines

CPU baseline: one first request after model unload took 11.038 seconds,
including 4.257 seconds reported model loading. Three warm requests took 6.283,
6.328, and 6.230 seconds; median 6.283 seconds. Each generated 99 tokens at
about 16 tokens/second.

RX 6800S GPU baseline: `ollama ps` reported `100% GPU`, and the Vulkan log
reported all 37 of 37 layers offloaded to the RX 6800S. One first request took
8.901 seconds, including 6.901 seconds model loading. Five warm requests took
1.904, 1.919, 1.900, 1.928, and 1.927 seconds; median 1.919 seconds. Each
generated 100 tokens at about 54 tokens/second. This is about 3.3 times the CPU
generation speed for this fixed prompt.

Repeated prompts can benefit from prompt caching. These results are local setup
baselines, not benchmark results.

For later failures, record the task, exact raw response/report filename,
failure category, expected behavior, and whether a fix changes the result.

## Day 3 validation and persona check (2026-09-22)

Added Pydantic schema enforcement, schema-constrained Ollama generation, and
bounded retries (two additional attempts by default). The 29 offline tests,
including the existing blackboard tests, pass. New checks cover malformed JSON,
missing/extra fields, strict string types, blank fields, truncation, successful
retry recovery, budget exhaustion, transport errors, and persona composition.

Both draft personas returned schema-valid output on a live call. On a general
blackboard explanation question without supplied facts, the cautious verifier
declined to infer an answer while the aggressive proposer used general
knowledge. This is a behavioral difference to review during Day 4 testing;
valid JSON alone does not establish that a response follows the evidence policy.

The default `make pex` question now includes explicit tulip/plant facts. Both
personas returned `yes` with the correct deduction on their first attempt.
No live formatting retry was needed; retry behavior was exercised with
controlled malformed responses in the offline tests. Full testing on three
tasks per persona and frozen BoardEntry review remain Day 4 work.
