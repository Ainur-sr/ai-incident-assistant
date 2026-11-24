from asyncio import BoundedSemaphore, timeout

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class AnalysisRequestLimits:
    """Bound buffering and concurrent analysis for the local, single-worker API."""

    MAX_BYTES = 288 * 1024

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.slots = BoundedSemaphore(4)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/analyze":
            await self.app(scope, receive, send)
            return
        if self.slots.locked():
            await JSONResponse({"detail": "Analysis is busy. Try again later."}, 503)(
                scope, receive, send
            )
            return
        async with self.slots:
            body = bytearray()
            try:
                async with timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > self.MAX_BYTES:
                            await JSONResponse({"detail": "Request body is too large."}, 413)(
                                scope, receive, send
                            )
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse({"detail": "Request body timed out."}, 408)(scope, receive, send)
                return

            payload = bytes(body)
            del body

            async def replay() -> dict:
                nonlocal payload
                if payload is None:
                    return await receive()
                data, payload = payload, None
                return {"type": "http.request", "body": data, "more_body": False}

            await self.app(scope, replay, send)
