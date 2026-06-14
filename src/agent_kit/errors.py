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


class ToolExecutionError(AgentKitError):
    """A tool failed in a way that is not a recoverable observation.

    Note: ordinary tool failures (raised, timed out, denied) are turned into
    ``ToolResult(ok=False)`` observations and fed back to the model — they do
    *not* raise. This is reserved for registry-level misuse (unknown tool name).
    """


class ContextOverflowError(AgentKitError):
    """Tier-0 context (system + current message + tool defs) alone overflows."""


class BudgetExceededError(AgentKitError):
    """A turn exceeded its per-turn wall-clock budget."""
