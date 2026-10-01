"""LLM cost control: route each task to the cheapest model that can do it,
put the stable part of the prompt first so the provider can cache it, and
measure the real cost of every message.

Prices below are EXAMPLE numbers in USD per million tokens. Copy the current
ones from your provider's pricing page; never hard-code them in production.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelPrice:
    name: str
    tier: int                 # 1 = small/fast, 2 = mid, 3 = large
    input: float              # USD per 1M input tokens
    output: float             # USD per 1M output tokens
    cache_read: float         # USD per 1M tokens served from the prompt cache
    cache_write: float        # USD per 1M tokens written to the cache


EXAMPLE_PRICES = [
    ModelPrice("small-model", 1, input=1.0, output=5.0, cache_read=0.10, cache_write=1.25),
    ModelPrice("mid-model", 2, input=3.0, output=15.0, cache_read=0.30, cache_write=3.75),
    ModelPrice("large-model", 3, input=15.0, output=75.0, cache_read=1.50, cache_write=18.75),
]

# Which tier each kind of task really needs. Decided by measuring quality, not by intuition.
TASK_TIER = {
    "classify_intent": 1,     # "is this a question, a complaint, a purchase?"
    "short_reply": 1,
    "summarize_history": 2,   # long-term memory of the contact
    "draft_reply": 2,
    "complex_negotiation": 3,
}


def pick_model(task: str, prices: list[ModelPrice] = EXAMPLE_PRICES) -> ModelPrice:
    tier = TASK_TIER.get(task, 2)
    eligible = [p for p in prices if p.tier >= tier]
    return min(eligible, key=lambda p: (p.input + p.output, p.tier))


def build_messages(brand_voice: str, policies: str, contact_memory: str, conversation: list[dict]) -> dict:
    """Order matters for caching: stable -> per-contact -> volatile.

    The brand voice and policies are identical for thousands of messages, so
    they go first and are marked cacheable. Only the tail changes per call.
    (Shape follows the Anthropic Messages API `cache_control` blocks.)
    """
    return {
        "system": [
            {"type": "text", "text": brand_voice + "\n\n" + policies, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "What we know about this contact:\n" + contact_memory},
        ],
        "messages": conversation,
    }


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def cost_usd(model: ModelPrice, u: Usage) -> float:
    m = 1_000_000
    return (u.input_tokens * model.input + u.output_tokens * model.output
            + u.cache_read_tokens * model.cache_read + u.cache_write_tokens * model.cache_write) / m


@dataclass
class CostMeter:
    """Accumulates spend per tenant so each customer's AI cost is visible (and billable)."""
    budget_usd_by_tenant: dict[str, float] = field(default_factory=dict)
    spent: dict[str, float] = field(default_factory=dict)

    def record(self, tenant: str, model: ModelPrice, usage: Usage) -> float:
        c = cost_usd(model, usage)
        self.spent[tenant] = self.spent.get(tenant, 0.0) + c
        return c

    def can_spend(self, tenant: str, estimate_usd: float) -> bool:
        budget = self.budget_usd_by_tenant.get(tenant)
        return budget is None or self.spent.get(tenant, 0.0) + estimate_usd <= budget


if __name__ == "__main__":
    model = pick_model("draft_reply")
    no_cache = Usage(input_tokens=6000, output_tokens=200)
    cached = Usage(input_tokens=800, output_tokens=200, cache_read_tokens=5200)
    print(f"model for draft_reply: {model.name}")
    print(f"cost without cache: ${cost_usd(model, no_cache):.5f}")
    print(f"cost with cache:    ${cost_usd(model, cached):.5f}")
    print(f"saving per message: {1 - cost_usd(model, cached) / cost_usd(model, no_cache):.0%}")
