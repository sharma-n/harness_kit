"""Serve several users concurrently from one AgentService (no server needed).

    OPENAI_API_KEY=... uv run python examples/multi_user.py

This is the multi-user story end to end, driven directly against the Agent:

  * **One service, many users.** A single ``AgentService`` (and its shared LLM /
    embedder / stores) serves every user. ``user_id`` is the isolation key — you do
    not build a service per user.
  * **State is user-scoped.** Each user gets their own ``conversation_id``; sessions
    are user-owned, the profile/episodic memory is filtered by ``user_id``, so one
    user never sees another's facts. The script proves it: alice and bob each store a
    different fact and recall only their own.
  * **Tool permissions are per-user.** Permissions live in ``stores.permissions``, not
    in config. We grant the default set to two users and *revoke* ``remember_fact``
    from a third — her model is never even offered the tool, so she can't store facts.
  * **Concurrency is real.** All users' turns run on the same event loop via
    ``asyncio.gather`` — overlapping LLM calls, exactly the shape a live server sees.

### Live-testing telemetry with this

Turn on Langfuse and run it — every user becomes a distinct Langfuse **user**, every
conversation a distinct **session**, and the concurrent turns interleave as separate
traces you can compare side by side:

    export LANGFUSE_ENABLED=true
    export LANGFUSE_PUBLIC_KEY=pk-...  LANGFUSE_SECRET_KEY=sk-...  LANGFUSE_HOST=https://cloud.langfuse.com
    OPENAI_API_KEY=... uv run python examples/multi_user.py

Then open Langfuse → Sessions and confirm each conversation groups its turn →
context.build → llm generation → tool.execute spans, plus the background
extract/rollover writes and the conversation_end trace at the end.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from harness_kit.agent.events import TextDelta, ToolCallStarted, ToolResult, TurnComplete
from harness_kit.service import AgentService

# A lock so concurrent users don't garble the console. Each turn's output is buffered
# and printed as one labeled block — the turns still run concurrently; only the final
# print is serialized (a real client streams straight to its own socket).
_print_lock = asyncio.Lock()


@dataclass(slots=True)
class User:
    """One user: their id, their conversation, the tools they may use, and a script."""

    user_id: str
    conversation_id: str
    allowed_tools: set[str]
    messages: list[str] = field(default_factory=list)


# Three users sharing one service. alice and bob get the full native toolset; carol's
# allowlist omits remember_fact, so she literally cannot persist facts (defense in
# depth: the registry filters tool *definitions* by her grant AND re-checks on execute).
FULL_TOOLS = {"remember_fact", "forget_fact", "list_facts", "recall"}

USERS = [
    User(
        user_id="alice",
        conversation_id="alice-demo",
        allowed_tools=FULL_TOOLS,
        messages=[
            "Hi! Please remember that I'm allergic to peanuts.",
            "What food should I avoid?",
        ],
    ),
    User(
        user_id="bob",
        conversation_id="bob-demo",
        allowed_tools=FULL_TOOLS,
        messages=[
            "Remember that my favorite programming language is Rust.",
            "What's my favorite language — and do you know anything about my allergies?",
        ],
    ),
    User(
        user_id="carol",
        conversation_id="carol-demo",
        allowed_tools=FULL_TOOLS - {"remember_fact"},
        messages=[
            "Please remember that my password is hunter2.",  # she has no tool to do this
        ],
    ),
]


async def run_user(service: AgentService, user: User) -> None:
    """Run one user's whole conversation, then finalize it.

    Per-user tool permissions are set up first via the PermissionStore — this is the
    authorization seam; config only sets the *global* default fallback.
    """
    await service.stores.permissions.grant(user.user_id, user.allowed_tools)
    revoked = FULL_TOOLS - user.allowed_tools
    if revoked:
        await service.stores.permissions.revoke(user.user_id, revoked)

    for message in user.messages:
        # Buffer this turn's events so the printed block stays intact under concurrency.
        lines: list[str] = [f">>> [{user.user_id}] {message}", ""]
        text_parts: list[str] = []
        async for event in service.agent.run_turn(
            user.user_id, user.conversation_id, message
        ):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallStarted):
                if text_parts:
                    lines.append("".join(text_parts))
                    text_parts = []
                lines.append(f"[calling {event.name}({event.arguments})]")
            elif isinstance(event, ToolResult):
                lines.append(f"[{event.name} -> ok={event.ok}: {event.content}]")
            elif isinstance(event, TurnComplete):
                if text_parts:
                    lines.append("".join(text_parts))
                lines.append(
                    f"--- {user.user_id}: {event.iterations} iter(s), "
                    f"stop={event.stop_reason}, usage={event.usage}"
                )
        async with _print_lock:
            print("\n".join(lines) + "\n")

    # Conversation end: embeds the whole conversation as one episodic point and marks
    # it finalized (its own trace under the same Langfuse session). The server does this
    # on WebSocket disconnect / via the idle sweeper; here we call it explicitly.
    await service.agent.end_conversation(user.user_id, user.conversation_id)


async def main() -> None:
    service = AgentService.from_yaml("config.yaml")
    await service.astart()  # connect any configured MCP servers + register their tools
    try:
        # All users run concurrently on one event loop — the real multi-user shape.
        await asyncio.gather(*(run_user(service, u) for u in USERS))
    finally:
        # Drains background writes, closes MCP + the shared HTTP client, flushes traces.
        await service.aclose()


if __name__ == "__main__":
    asyncio.run(main())
