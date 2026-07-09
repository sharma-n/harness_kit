"""agent_kit exception hierarchy.

Provider/transport failures are llm_kit's job — we reuse ``llm_kit.LLMError`` and
its subclasses verbatim rather than wrapping them. These types cover only the
concerns agent_kit owns: authorization, tool execution, and context budgeting.
"""

from __future__ import annotations


class AgentKitError(Exception):
    """Base for all agent_kit-specific errors."""


class UnauthorizedError(AgentKitError):
    """A user tried to access a conversation/session owned by someone else."""


class ContextOverflowError(AgentKitError):
    """Tier-0 context (system + current message + tool defs) alone overflows."""


class BudgetExceededError(AgentKitError):
    """A turn exceeded its per-turn wall-clock budget."""


class StoreWriteError(AgentKitError):
    """A background store write exhausted its retries.

    Raised by ``retry.retry_async`` when a memory-layer store write (e.g.
    ``upsert_facts``, ``save``, ``add``, ``mark_finalized``) keeps failing. Carries
    the ``operation`` label so the single choke-point log line distinguishes a
    store-write failure from an LLM-step failure (``llm_kit.LLMError``). The
    triggering backend exception is chained via ``raise ... from``.
    """

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"store write failed after retries: {operation}")
