"""
Token Counter Stub — Student 4 (UI & Benchmarking)
bench/metrics/token_counter.py

Stub for counting tokens per agent turn in the PXP Blackboard session.

Function signatures are designed to match the eventual hook into Student 3's LLM client
(see agents/llm_client.py) so this module can be dropped into the trial_runner without
interface changes when the real tokeniser is available.

TODO (Student 3 Integration — Week 2/3):
  Replace whitespace-split estimate with the tokeniser from the agreed Ollama model:
    from agents.llm_client import get_tokenizer
    tokenizer = get_tokenizer(model_name)
    return len(tokenizer.encode(text))

Assumptions (flagged for standup):
  - agent_id is a plain string identifier (e.g. "Agent_Alpha (Proposer)").
  - turn_text is the concatenated prediction + explanation for one BoardEntry.
  - model_name defaults to the team-agreed model tag from .env; may also be passed
    explicitly by the trial runner to support multi-model ablations.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("token-counter")

# Team-agreed default model (from .env or fallback)
_DEFAULT_MODEL: str = os.getenv("OLLAMA_MODEL", "mistral-7b-instruct")


def count_tokens(
    text: str,
    agent_id: Optional[str] = None,
    model_name: Optional[str] = None,
) -> int:
    """
    Estimate the number of tokens in *text* for a given agent turn.

    Args:
        text:       The agent's turn content (typically prediction + explanation,
                    concatenated with a space separator).
        agent_id:   The agent that produced this text (informational / logging only).
        model_name: The LLM model whose tokeniser to use. Defaults to the
                    team-agreed model in OLLAMA_MODEL env var.
                    TODO (Student 3): wire in real tokeniser keyed on model_name.

    Returns:
        int: Token count estimate.
    """
    # ── Stub implementation (whitespace split) ────────────────────────
    # Using str.split() as a reasonable approximation (~1.3 tokens/word for English text).
    # This matches GPT-style BPE tokenisers to within ~15% for typical reasoning text.
    #
    # TODO (Student 3 Integration): replace with:
    #   tokenizer = get_tokenizer(model_name or _DEFAULT_MODEL)
    #   return len(tokenizer.encode(text))
    token_estimate = len(text.split())

    effective_model = model_name or _DEFAULT_MODEL
    logger.debug(
        "count_tokens | agent=%s model=%s text_len=%d estimate=%d",
        agent_id or "unknown",
        effective_model,
        len(text),
        token_estimate,
    )
    return token_estimate


def count_tokens_for_entry(
    prediction: str,
    explanation: str,
    agent_id: Optional[str] = None,
    model_name: Optional[str] = None,
) -> dict[str, int]:
    """
    Count tokens for both parts of a BoardEntry individually and in total.

    Args:
        prediction:  The "what" field of the board entry.
        explanation: The "why" field of the board entry.
        agent_id:    Agent identifier for logging.
        model_name:  LLM model tag.

    Returns:
        dict with keys: 'prediction_tokens', 'explanation_tokens', 'total_tokens'.
    """
    pred_tokens = count_tokens(prediction, agent_id=agent_id, model_name=model_name)
    expl_tokens = count_tokens(explanation, agent_id=agent_id, model_name=model_name)
    return {
        "prediction_tokens": pred_tokens,
        "explanation_tokens": expl_tokens,
        "total_tokens": pred_tokens + expl_tokens,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Quick self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        ("Agent_Alpha", "x = 12", "Initial hypothesis based on x^2 = 144."),
        ("Agent_Gamma", "Solution incomplete", "(-12)^2 = 144 is also valid."),
    ]
    for agent, pred, expl in test_cases:
        result = count_tokens_for_entry(pred, expl, agent_id=agent)
        print(f"[{agent}]  pred={result['prediction_tokens']}t  "
              f"expl={result['explanation_tokens']}t  "
              f"total={result['total_tokens']}t")
