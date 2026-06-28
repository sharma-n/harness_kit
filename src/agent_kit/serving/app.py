"""FastAPI serving layer (SPEC §10).

Both transports consume the same ``AgentEvent`` stream and forward ``TextDelta``s
as they arrive:
  - WebSocket ``/ws/{conversation_id}`` — bidirectional; client sends JSON
    ``{"user_id", "message"}`` turns, receives encoded event frames.
  - SSE ``/sse/{conversation_id}`` — one turn per request (``user_id``/``message``
    as query params), server-streamed event frames.

Auth is a stub: ``user_id`` comes from the client. A real deployment resolves it
from a verified token — the rest of the stack already treats ``user_id`` as the
isolation key, so only this resolver changes.

Backpressure: each send is awaited, so a slow client paces its own stream without
stalling the shared event loop (other connections run as independent tasks).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from agent_kit import telemetry, metrics as _metrics
from agent_kit.agent.events import ToolApprovalRequired
from agent_kit.config import AgentKitConfig
from agent_kit.errors import UnauthorizedError
from agent_kit.service import AgentService
from agent_kit.serving.wire import encode_conversation, encode_event

logger = logging.getLogger(__name__)


def create_app(service: AgentService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Connect configured MCP servers and register their discovered tools before
        # serving any turns.
        await service.astart()
        # Background idle sweeper: finalizes conversations that have gone idle past
        # ``idle_finalize_s``. This is the transport-agnostic conversation-end signal
        # — SSE never disconnects and WebSockets can drop without firing their handler.
        sweeper = asyncio.create_task(_idle_sweep_loop(service))
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            # Drain background writes, close MCP connections + the shared HTTP client.
            await service.aclose()

    app = FastAPI(title="agent_kit", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        body, content_type = _metrics.metrics_output()
        if not body:
            return Response(
                content='{"status":"not_implemented"}',
                media_type="application/json",
                status_code=501,
            )
        return Response(content=body, media_type=content_type)

    @app.get("/conversations")
    async def list_conversations(
        user_id: str = Query(...),
        status: str | None = Query(None),  # "active" | "finalized"
        limit: int | None = Query(None),
    ) -> dict:
        # ``user_id`` from the query param mirrors the auth-stub posture of ``/sse``;
        # a real deployment resolves it from a verified token (see module docstring).
        metas = await service.stores.session.list(user_id)
        if status == "active":
            metas = [m for m in metas if m.finalized_at is None]
        elif status == "finalized":
            metas = [m for m in metas if m.finalized_at is not None]
        if limit is not None:
            metas = metas[:limit]
        return {"conversations": [encode_conversation(m) for m in metas]}

    @app.put("/conversations/{conversation_id}/model")
    async def set_model(
        conversation_id: str,
        user_id: str = Query(...),
        model: str | None = Query(None),
    ) -> dict:
        """Set (or clear with ``model=null``) the per-conversation model override.

        The override is stored in the session and picked up by the next turn.
        SSE clients use this endpoint since they cannot send messages mid-stream.
        """
        await service.set_conversation_model(conversation_id, user_id, model)
        return {"conversation_id": conversation_id, "model": model}

    @app.websocket("/ws/{conversation_id}")
    async def ws(websocket: WebSocket, conversation_id: str) -> None:
        await websocket.accept()
        last_user_id: str | None = None
        # Turn messages queue: _receive puts payloads here; _run_turns consumes them.
        # None is the disconnect sentinel.
        turn_queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _receive() -> None:
            nonlocal last_user_id
            try:
                while True:
                    payload = json.loads(await websocket.receive_text())
                    if payload.get("type") == "approval":
                        # Route approval responses directly to the pending future in
                        # the running turn — no queue needed, just resolve in place.
                        service.agent.resolve_approval(
                            payload["call_id"], bool(payload.get("approved"))
                        )
                    elif payload.get("type") == "set_model":
                        # Store a per-conversation model override; takes effect on the
                        # next run_turn call for this conversation.
                        await service.set_conversation_model(
                            conversation_id,
                            payload["user_id"],
                            payload.get("model"),
                        )
                    else:
                        last_user_id = payload["user_id"]
                        await turn_queue.put(payload)
            except WebSocketDisconnect:
                await turn_queue.put(None)  # wake _run_turns so it can exit

        async def _run_turns() -> None:
            while True:
                payload = await turn_queue.get()
                if payload is None:
                    break
                try:
                    async for event in service.agent.run_turn(
                        payload["user_id"], conversation_id, payload["message"]
                    ):
                        await websocket.send_json(encode_event(event))
                except WebSocketDisconnect:
                    return
                except UnauthorizedError as exc:
                    with contextlib.suppress(Exception):
                        await websocket.send_json({"type": "error", "error": str(exc)})

        try:
            await asyncio.gather(_receive(), _run_turns())
        finally:
            # Conversation ended — embed it as one episodic point. This runs in a
            # shielded cancel scope so it completes even when the test client (or a
            # proxy) cancels the ASGI handler immediately after sending the disconnect
            # frame, before the gather has a chance to process it. The idle sweeper is
            # the backstop if even this path fails (e.g. abrupt process kill).
            with anyio.CancelScope(shield=True):
                if last_user_id is not None:
                    try:
                        await service.agent.end_conversation(last_user_id, conversation_id)
                    except Exception:
                        logger.exception(
                            "conversation finalize on disconnect failed "
                            "(user_id=%s conversation_id=%s)",
                            last_user_id,
                            conversation_id,
                        )
                # Export this connection's spans now (no-op when telemetry is disabled).
                telemetry.flush()

    @app.get("/sse/{conversation_id}")
    async def sse(
        conversation_id: str,
        user_id: str = Query(...),
        message: str = Query(...),
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            try:
                async for event in service.agent.run_turn(
                    user_id, conversation_id, message
                ):
                    if isinstance(event, ToolApprovalRequired):
                        # SSE is one-way: the client cannot send an approval response
                        # on this connection, so auto-deny immediately. The loop's
                        # wait_for sees the future already resolved and returns False.
                        service.agent.resolve_approval(event.call_id, False)
                    yield _sse_frame(encode_event(event))
            except UnauthorizedError as exc:
                yield _sse_frame({"type": "error", "error": str(exc)})
            finally:
                # One turn per SSE request → export its spans as the stream closes.
                telemetry.flush()

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _sse_frame(data: dict) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


async def _idle_sweep_loop(service: AgentService) -> None:
    """Periodically finalize idle conversations until the app shuts down."""
    cfg = service.cfg.memory.working
    while True:
        await asyncio.sleep(cfg.sweep_interval_s)
        try:
            await service.agent.sweep_idle(cfg.idle_finalize_s)
        except Exception:  # never let a sweep failure kill the loop
            logger.exception("idle sweep failed")


def create_app_from_yaml(path: str = "config.yaml") -> FastAPI:
    """ASGI entrypoint factory: ``uvicorn agent_kit.serving.app:app``-friendly."""
    return create_app(AgentService.from_yaml(path))
