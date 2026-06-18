"""
WebSocket event stream for AionUi.

AionUi's aioncore exposes a WebSocket endpoint for real-time events
(message.stream, turn.completed, agent.status, etc.). The WS endpoint
is NOT on the REST port (40937) but on the secondary port (34931),
and its exact path was not discoverable during v4 build.

This module is a STUB. To use it:
1. Find the WS path by inspecting AionUi Electron renderer network traffic
   or aioncore source code.
2. Replace WS_URL with the correct value.
3. Implement event parsing in `stream_events`.
"""

import json
import asyncio
from typing import Callable, Awaitable

WS_URL = "ws://127.0.0.1:34931/ws"  # TODO: confirm path


async def stream_events(
    host: str,
    on_event: Callable[[dict], Awaitable[None]],
):
    """Connect to the AionUi WebSocket and yield events.

    Args:
        host: WS URL (e.g. "ws://127.0.0.1:34931/ws")
        on_event: Async callback receiving parsed event dicts.
    """
    import websockets

    async with websockets.connect(host) as ws:
        async for raw in ws:
            try:
                event = json.loads(raw)
                await on_event(event)
            except json.JSONDecodeError:
                pass
