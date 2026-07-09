"""Minimal websocket client for a running harness_kit server.

Start the server:
    OPENAI_API_KEY=... uv run uvicorn \
        "harness_kit.serving.app:create_app_from_yaml" --factory

Then in another shell:
    uv run python examples/ws_client.py
"""

from __future__ import annotations

import asyncio
import json

import websockets

URL = "ws://localhost:8000/ws/demo-conversation"
USER_ID = "demo-user"


async def turn(ws, message: str) -> None:
    print(f"\n>>> {message}\n")
    await ws.send(json.dumps({"user_id": USER_ID, "message": message}))
    while True:
        frame = json.loads(await ws.recv())
        kind = frame["type"]
        if kind == "text":
            print(frame["text"], end="", flush=True)
        elif kind == "tool_call":
            print(f"\n[calling {frame['name']}({frame['arguments']})]")
        elif kind == "tool_result":
            print(f"\n[{frame['name']} -> ok={frame['ok']}: {frame['content']}]")
        elif kind == "turn_complete":
            print(f"\n--- {frame['stop_reason']}, usage={frame['usage']}")
            return
        elif kind == "error":
            print(f"\n[error] {frame['error']}")
            return


async def main() -> None:
    async with websockets.connect(URL) as ws:
        await turn(ws, "Hello! Remember I manage a team of 6.")
        await turn(ws, "How big is my team?")


if __name__ == "__main__":
    asyncio.run(main())
