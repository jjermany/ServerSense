import asyncio

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_API_BODY_BYTES = 128 * 1024
API_BODY_TIMEOUT_SECONDS = 15


class APIProtectionMiddleware:
    """Bound API bodies and require a header that HTML forms cannot supply."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_api = scope["path"].startswith("/api/")

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                headers["Content-Security-Policy"] = (
                    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
                )
                if is_api:
                    headers["Cache-Control"] = "no-store"
                    headers["Pragma"] = "no-cache"
            await send(message)

        if is_api and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            headers = Headers(scope=scope)
            # No cross-origin CORS grant is configured. Browsers must preflight
            # this header, so an untrusted origin cannot submit a mutation.
            if headers.get("x-serversense-request") != "1":
                await JSONResponse(
                    {"detail": "Missing ServerSense request header"}, status_code=403
                )(scope, receive, secured_send)
                return
            body = bytearray()
            try:
                async with asyncio.timeout(API_BODY_TIMEOUT_SECONDS):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > MAX_API_BODY_BYTES:
                            await JSONResponse(
                                {"detail": "Request body is too large"}, status_code=413
                            )(scope, receive, secured_send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse({"detail": "Request body timed out"}, status_code=408)(
                    scope, receive, secured_send
                )
                return

            delivered = False

            async def bounded_receive() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, secured_send)
            return
        await self.app(scope, receive, secured_send)
