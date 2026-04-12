"""Live relay transport using WebSocket with polling fallback."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Callable, List, Optional
from urllib.parse import quote

from .relay_manager import RelayManager


class LiveConnection:
    def __init__(
        self,
        *,
        did: str,
        relay_manager: RelayManager,
        on_ws_message: Callable[[dict[str, Any]], None],
        poll_once: Callable[[], None],
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.did = did
        self.relay_manager = relay_manager
        self.on_ws_message = on_ws_message
        self.poll_once = poll_once
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        if self._can_use_websocket():
            self._thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        else:
            self._thread = threading.Thread(target=self._run_poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _can_use_websocket(self) -> bool:
        try:
            import websockets  # noqa: F401
        except Exception:
            return False
        return True

    def _relay_ws_urls(self) -> List[str]:
        urls: List[str] = []
        for relay in self.relay_manager.relays:
            base = relay.rstrip("/")
            if base.startswith("https://"):
                ws_base = "wss://" + base[len("https://") :]
            elif base.startswith("http://"):
                ws_base = "ws://" + base[len("http://") :]
            else:
                continue
            urls.append(f"{ws_base}/ws?did={quote(self.did)}")
        return urls

    def _run_ws_loop(self) -> None:
        asyncio.run(self._ws_loop())

    async def _ws_loop(self) -> None:
        import websockets

        urls = self._relay_ws_urls()
        backoff_seconds = 1.0
        max_backoff = 60.0
        while not self._stop.is_set():
            connected = False
            for ws_url in urls:
                if self._stop.is_set():
                    return
                try:
                    async with websockets.connect(ws_url, ping_interval=20, close_timeout=2) as conn:
                        connected = True
                        backoff_seconds = 1.0
                        while not self._stop.is_set():
                            raw = await asyncio.wait_for(conn.recv(), timeout=1.0)
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                self.on_ws_message(message)
                except Exception:
                    continue

            if not connected:
                self.poll_once()
                print(f"[LiveConnection] reconnecting in {backoff_seconds:.0f}s")
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2.0, max_backoff)

    def _run_poll_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            time.sleep(self.poll_interval_seconds)
