"""
Token Counter & LLM Integration Hook — Student 4 (UI & Benchmarking)
bench/metrics/token_counter.py

W2 Day 2 / Day 3 Implementation:
- Hooks into Student 3's LLM client calls via adapter interface.
- Provides per-turn and running token tallies per agent and global session.
- Exposes TokenTallyTracker for live UI feeding and benchmark analytics.

Integration Point (Student 3 — Week 2/3):
  When Student 3's LLM client (e.g. `agents.llm_client.call_llm` or `OllamaClient.generate`)
  is invoked, call `record_llm_turn(...)` or wrap the response using `llm_token_count_adapter(...)`.
  If raw usage metadata (prompt_tokens, completion_tokens) is returned by the LLM, it is used
  directly when supplied; board-entry counts otherwise use a registered tokenizer
  or a whitespace heuristic.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agents.llm_client import MODEL

logger = logging.getLogger("token-counter")

# Keep token accounting metadata aligned with the project agent default.
_DEFAULT_MODEL: str = MODEL


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TurnTokenMetrics:
    """Text counts for one board entry plus optional model usage fields."""
    entry_id: str
    agent_id: str
    tag: str
    prediction_tokens: int
    explanation_tokens: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_name: str = _DEFAULT_MODEL


@dataclass
class AgentTokenSummary:
    """Cumulative token statistics for one agent."""
    agent_id: str
    turn_count: int = 0
    total_prediction_tokens: int = 0
    total_explanation_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    mainline_tokens: int = 0
    simulation_tokens: int = 0
    tags_used: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ─────────────────────────────────────────────────────────────────────────────
# Tokenizer Hooks & Adapter
# ─────────────────────────────────────────────────────────────────────────────

# Optional tokenizer factory registry for Student 3's LLM client
_TOKENIZER_REGISTRY: Dict[str, Callable[[str], int]] = {}


def register_custom_tokenizer(model_name: str, count_fn: Callable[[str], int]) -> None:
    """Register a custom tokenizer function for a specific model."""
    _TOKENIZER_REGISTRY[model_name] = count_fn
    logger.info("Registered custom tokenizer for model: %s", model_name)


def count_tokens(
    text: str,
    agent_id: Optional[str] = None,
    model_name: Optional[str] = None,
) -> int:
    """
    Count or estimate the number of tokens in `text` for a given agent turn.

    1. Uses custom registered tokenizer for `model_name` if available.
    2. Falls back to a rough whitespace heuristic (~1.3 tokens / word).

    Unless a tokenizer is explicitly registered for the selected model, the
    result is only an estimate. This counts supplied text, not a full LLM call.
    """
    if not text:
        return 0

    effective_model = model_name or _DEFAULT_MODEL

    # 1. Custom registered tokenizer (Student 3 hook)
    if effective_model in _TOKENIZER_REGISTRY:
        try:
            return _TOKENIZER_REGISTRY[effective_model](text)
        except Exception as e:
            logger.warning("Custom tokenizer error for %s: %s — falling back", effective_model, e)

    # 2. Heuristic fallback (~1.3 tokens per whitespace word for English reasoning)
    words = text.split()
    token_estimate = max(1, int(len(words) * 1.3)) if words else len(text) // 4

    logger.debug(
        "count_tokens | agent=%s model=%s text_len=%d count=%d",
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


def llm_token_count_adapter(
    response_or_usage: Any,
    prompt_text: str = "",
    completion_text: str = "",
    agent_id: Optional[str] = None,
    model_name: Optional[str] = None,
) -> dict[str, int]:
    """
    Adapter function that hooks into Student 3's LLM response objects.

    Expected Student 3 LLM call signature patterns:
      Pattern A: Response dict with 'usage' object:
                 {'usage': {'prompt_tokens': 120, 'completion_tokens': 45, 'total_tokens': 165}}
      Pattern B: Response object with attributes (.prompt_tokens, .completion_tokens, or .usage)
      Pattern C: Raw completion text string (computed via fallback tokenizer)

    Returns:
        dict: {'prompt_tokens': int, 'completion_tokens': int, 'total_tokens': int}
    """
    prompt_tokens = 0
    completion_tokens = 0

    # Pattern A: Dict with usage
    if isinstance(response_or_usage, dict):
        usage = response_or_usage.get("usage") or response_or_usage
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens") or usage.get("prompt_eval_count") or 0
            completion_tokens = usage.get("completion_tokens") or usage.get("eval_count") or 0

    # Pattern B: Object with usage attributes
    elif response_or_usage is not None and hasattr(response_or_usage, "usage"):
        u = getattr(response_or_usage, "usage")
        prompt_tokens = getattr(u, "prompt_tokens", 0) or getattr(u, "prompt_eval_count", 0)
        completion_tokens = getattr(u, "completion_tokens", 0) or getattr(u, "eval_count", 0)

    # Pattern C: Estimate from raw texts if usage was 0 or absent
    if prompt_tokens == 0 and prompt_text:
        prompt_tokens = count_tokens(prompt_text, agent_id=agent_id, model_name=model_name)
    if completion_tokens == 0 and completion_text:
        completion_tokens = count_tokens(completion_text, agent_id=agent_id, model_name=model_name)

    total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Session Running Token Tracker
# ─────────────────────────────────────────────────────────────────────────────

class TokenTallyTracker:
    """
    Maintains cumulative board-entry text counts per agent and session.

    The displayed total is not full prompt/completion usage. Calls should be
    serialized if the same tracker is shared across threads.
    """

    def __init__(self) -> None:
        self._history: List[TurnTokenMetrics] = []
        self._by_agent: Dict[str, AgentTokenSummary] = defaultdict(
            lambda: AgentTokenSummary(agent_id="")
        )
        self._total_tokens: int = 0
        self._mainline_tokens: int = 0
        self._simulation_tokens: int = 0

    def record_turn(
        self,
        entry_id: str,
        agent_id: str,
        tag: str,
        prediction: str,
        explanation: str,
        prompt_text: str = "",
        model_name: Optional[str] = None,
        llm_response_obj: Any = None,
        is_counterfactual_sim: bool = False,
    ) -> TurnTokenMetrics:
        """Record board-entry text counts, optionally separating sandbox entries.

        The total counts only prediction and explanation text. It is not full
        prompt/completion usage unless those are recorded separately from
        provider metadata.
        """
        # Entry token breakdown
        entry_counts = count_tokens_for_entry(prediction, explanation, agent_id=agent_id, model_name=model_name)
        
        # LLM prompt/completion breakdown via adapter
        adapter_counts = llm_token_count_adapter(
            llm_response_obj,
            prompt_text=prompt_text,
            completion_text=f"{prediction} {explanation}",
            agent_id=agent_id,
            model_name=model_name,
        )

        metrics = TurnTokenMetrics(
            entry_id=entry_id,
            agent_id=agent_id,
            tag=tag,
            prediction_tokens=entry_counts["prediction_tokens"],
            explanation_tokens=entry_counts["explanation_tokens"],
            prompt_tokens=adapter_counts["prompt_tokens"],
            completion_tokens=adapter_counts["completion_tokens"],
            total_tokens=entry_counts["total_tokens"],
            model_name=model_name or _DEFAULT_MODEL,
        )

        self._history.append(metrics)
        self._total_tokens += metrics.total_tokens

        # Update per-agent summary
        summary = self._by_agent[agent_id]
        summary.agent_id = agent_id
        summary.turn_count += 1
        summary.total_prediction_tokens += metrics.prediction_tokens
        summary.total_explanation_tokens += metrics.explanation_tokens
        summary.total_prompt_tokens += metrics.prompt_tokens
        summary.total_completion_tokens += metrics.completion_tokens
        summary.total_tokens += metrics.total_tokens
        if is_counterfactual_sim:
            summary.simulation_tokens += metrics.total_tokens
            self._simulation_tokens += metrics.total_tokens
        else:
            summary.mainline_tokens += metrics.total_tokens
            self._mainline_tokens += metrics.total_tokens
        summary.tags_used[tag] += 1

        logger.debug(
            "[TokenTracker] Recorded turn %s for %s (%s): %d tokens (Session Total: %d)",
            entry_id, agent_id, tag, metrics.total_tokens, self._total_tokens,
        )
        return metrics

    def get_total_tokens(self) -> int:
        """Get the global cumulative token count."""
        return self._total_tokens

    def get_agent_summary(self, agent_id: str) -> Optional[AgentTokenSummary]:
        """Get token summary for a specific agent."""
        return self._by_agent.get(agent_id)

    def get_all_summaries(self) -> Dict[str, dict]:
        """Get dictionary summary suitable for JSON serialization / UI transmission."""
        return {
            agent_id: {
                "agent_id": s.agent_id,
                "turn_count": s.turn_count,
                "total_prediction_tokens": s.total_prediction_tokens,
                "total_explanation_tokens": s.total_explanation_tokens,
                "total_prompt_tokens": s.total_prompt_tokens,
                "total_completion_tokens": s.total_completion_tokens,
                "total_tokens": s.total_tokens,
                "mainline_tokens": s.mainline_tokens,
                "simulation_tokens": s.simulation_tokens,
                "tags_used": dict(s.tags_used),
            }
            for agent_id, s in self._by_agent.items()
        }

    def export_tally_report(self) -> dict:
        """Export comprehensive token tally report."""
        return {
            "total_tokens": self._total_tokens,
            "mainline_tokens": self._mainline_tokens,
            "simulation_tokens": self._simulation_tokens,
            "total_tokens_kind": "estimated_board_entry_text_only",
            "token_count_note": (
                "Counts prediction and explanation text only; uses a registered tokenizer when available, "
                "otherwise a whitespace heuristic. Does not include system/user prompts or represent a benchmark score."
            ),
            "turn_count": len(self._history),
            "agents": self.get_all_summaries(),
        }

    def reset(self) -> None:
        """Reset all counters for a new session."""
        self._history.clear()
        self._by_agent.clear()
        self._total_tokens = 0
        self._mainline_tokens = 0
        self._simulation_tokens = 0


# Process-global tracker singleton
global_token_tracker = TokenTallyTracker()


# ─────────────────────────────────────────────────────────────────────────────
# Quick self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tracker = TokenTallyTracker()
    t1 = tracker.record_turn(
        entry_id="e1",
        agent_id="Agent_Alpha",
        tag="REVISE",
        prediction="x = 12",
        explanation="Solving x^2 = 144.",
    )
    t2 = tracker.record_turn(
        entry_id="e2",
        agent_id="Agent_Beta",
        tag="RATIFY",
        prediction="Verified x = 12",
        explanation="12 * 12 = 144 is verified.",
    )
    print("Self-test token tally report:")
    import pprint
    pprint.pprint(tracker.export_tally_report())
