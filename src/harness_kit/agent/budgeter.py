"""Context budgeter (SPEC §6.5) — tiered allocation under a token ceiling.

All five context sources compete for a finite input-token budget. The budgeter
evicts by priority tier, never silently overflowing the model window:

  Tier 0 — never drop: system prompt, current message, tool defs, in-turn obs.
           If these alone overflow → ContextOverflowError.
  Tier 1 — factual profile  (compact, rarely the problem)
  Tier 2 — working buffer    (evict OLDEST turns first; they roll into summary)
  Tier 4 — episodic hits     (drop LOWEST-scoring first; already threshold-gated)

Summary (tier 3) re-tightening is left to the working-memory rollover; here it is
kept whole if it fits. Token measurement uses a char/4 heuristic mirroring
llm_kit's default estimator; a real tokenizer can be injected later.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_kit.config import ContextConfig
from harness_kit.errors import ContextOverflowError
from harness_kit.stores.types import MemoryHit, Turn
from harness_kit.tokens import Estimator, estimate_tokens

# Back-compat alias: the canonical estimator now lives in ``harness_kit.tokens`` so
# lower layers (memory rollover) can share it without importing ``agent/``.
default_estimator = estimate_tokens


@dataclass(slots=True)
class BudgetInputs:
    """Raw materials before budgeting; the builder fills these."""

    system_fixed: str  # tier 0: identity + rules (always kept)
    current_message: str  # tier 0
    tool_text: str  # tier 0: rough size of tool defs
    factual_block: str  # tier 1
    buffer: list[Turn]  # tier 2
    summary: str  # tier 3
    episodic: list[MemoryHit]  # tier 4


@dataclass(slots=True)
class BudgetResult:
    """What survived budgeting, ready for assembly."""

    factual_block: str
    buffer: list[Turn]
    summary: str
    episodic: list[MemoryHit]
    used_tokens: int
    budget_tokens: int


class ContextBudgeter:
    def __init__(self, cfg: ContextConfig, *, estimator: Estimator = default_estimator) -> None:
        self._cfg = cfg
        self._estimate = estimator

    @property
    def budget(self) -> int:
        return (
            self._cfg.max_input_tokens
            - self._cfg.output_reserve_tokens
            - self._cfg.safety_margin
        )

    def allocate(self, inputs: BudgetInputs) -> BudgetResult:
        budget = self.budget

        # Tier 0 — hard required.
        tier0 = (
            self._estimate(inputs.system_fixed)
            + self._estimate(inputs.current_message)
            + self._estimate(inputs.tool_text)
        )
        if tier0 > budget:
            raise ContextOverflowError(
                f"tier-0 context ({tier0} tokens) exceeds budget ({budget})"
            )
        remaining = budget - tier0

        # Tier 1 — factual profile (kept whole if it fits; else dropped).
        factual = inputs.factual_block
        cost = self._estimate(factual)
        if cost <= remaining:
            remaining -= cost
        else:
            factual = ""

        # Tier 3 — rolling summary (kept whole if it fits).
        summary = inputs.summary
        cost = self._estimate(summary)
        if cost <= remaining:
            remaining -= cost
        else:
            summary = ""

        # Tier 2 — working buffer: keep the NEWEST turns that fit; evict oldest.
        buffer: list[Turn] = []
        for turn in reversed(inputs.buffer):
            cost = self._estimate(turn.text)
            if cost <= remaining:
                remaining -= cost
                buffer.append(turn)
            else:
                break
        buffer.reverse()

        # Tier 4 — episodic: highest score first, drop lowest when out of room.
        episodic: list[MemoryHit] = []
        for hit in sorted(inputs.episodic, key=lambda h: h.score, reverse=True):
            cost = self._estimate(hit.point.payload.get("text", ""))
            if cost <= remaining:
                remaining -= cost
                episodic.append(hit)
            else:
                break

        return BudgetResult(
            factual_block=factual,
            buffer=buffer,
            summary=summary,
            episodic=episodic,
            used_tokens=budget - remaining,
            budget_tokens=budget,
        )
