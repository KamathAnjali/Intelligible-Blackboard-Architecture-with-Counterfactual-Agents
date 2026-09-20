# Shared local model selection

**Decision:** use `qwen3:4b-instruct-2507-q4_K_M` for all development and
comparable project experiments. Pin this exact tag in team configuration and
experiment reports.

## Why this model

The team needs one local model that runs on the least powerful likely machine:
the MacBook Air. The 4B Q4 model is a 2.5 GB download and fits comfortably on
the available machines while leaving room for the operating system and project
runtime. It supports the short, structured `prediction` and `explanation`
responses used by the PXP agents.

The nonthinking instruct model keeps responses and token accounting more
predictable. The client sets context to 4096 tokens, temperature to 0, seed to
42, and output cap to 256 tokens for reproducible local checks.

## Alternatives considered

| Candidate | Decision | Reason |
| --- | --- | --- |
| `qwen3:4b-instruct-2507-q4_K_M` | Selected | Small enough for every teammate and capable of structured JSON output. |
| Qwen3 8B Q4 | Deferred | Higher memory demand risks making the MacBook Air the limiting machine. |
| Mistral 7B Instruct | Deferred | Larger model without a clear functional advantage for the short PXP messages. |
| Llama 3 8B Instruct | Deferred | Larger model without a clear functional advantage for the short PXP messages. |

## Local evidence on Dhruva's laptop

With Ollama 0.34.2 and the RX 6800S through Vulkan, the model loaded fully on
the GPU and generated about 54 tokens/second for the fixed latency prompt. CPU
generation was about 16 tokens/second. Five simple PEX-format sample tasks
returned valid JSON and correct expected predictions. This verifies local setup;
it does not measure benchmark quality.
