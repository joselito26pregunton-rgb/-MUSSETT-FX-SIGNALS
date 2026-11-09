"""Bounded request guards; no raw body, address, token or secret is logged."""
import asyncio
from collections import deque
from math import ceil
from time import monotonic
from fastapi.responses import JSONResponse


class WebhookGuards:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.arrivals = deque()

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or scope.get("path") != "/webhook/tradingview"):
            return await self.app(scope, receive, send)
        now = monotonic()
        while self.arrivals and self.arrivals[0] <= now - 60:
            self.arrivals.popleft()
        if len(self.arrivals) >= self.settings.webhook_requests_per_minute:
            response = JSONResponse(
                {"detail": "Límite de solicitudes del webhook alcanzado"}, status_code=429,
                headers={"Retry-After": str(max(1, ceil(60 - (now - self.arrivals[0]))))}
            )
            return await response(scope, receive, send)
        self.arrivals.append(now)
        parts, size = [], 0
        while True:
            remaining = 5 - (monotonic() - now)
            if remaining <= 0:
                return await JSONResponse({"detail": "Tiempo de recepción agotado"}, status_code=408)(scope, receive, send)
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                return await JSONResponse({"detail": "Tiempo de recepción agotado"}, status_code=408)(scope, receive, send)
            if message["type"] == "http.disconnect":
                return
            data = message.get("body", b"")
            size += len(data)
            if size > self.settings.webhook_max_body_bytes:
                return await JSONResponse({"detail": "Cuerpo del webhook demasiado grande"}, status_code=413)(scope, receive, send)
            parts.append(data)
            if not message.get("more_body"):
                break
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(parts), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
