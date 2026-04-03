"""HTTP/1.1 server over RNS."""

import argparse
import asyncio
import io
import os
import sys
from collections.abc import (
    Awaitable,
    Callable,
)
from typing import TypeVar

import RNS

from .types import (
    HttpRequest,
    HttpResponse,
)

HandlerType = (
    Callable[[HttpRequest], HttpResponse]
    | Callable[[HttpRequest], Awaitable[HttpResponse]]
)

T = TypeVar("T")


def await_in_sync(awaitable: Awaitable[T]) -> T:
    """Run any awaitable from synchronous code safely."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(awaitable)

    except RuntimeError:
        return asyncio.run(awaitable)  # pyright: ignore[reportUnknownVariableType, reportArgumentType]


class HttpServer:
    """HTTP/1.1 server using RNS for transport."""

    def __init__(
        self,
        port: int,
        identity_path: str | None = None,
        request_timeout: float = 60.0,
        read_timeout: float = 30.0,
    ) -> None:
        self._port: int = port
        self._identity_path: str = identity_path or self._default_identity_path()
        self._request_timeout: float = request_timeout
        self._read_timeout: float = read_timeout
        self._destination: RNS.Destination | None = None
        self._handlers: dict[tuple[str, str], HandlerType] = {}
        self._default_handler: HandlerType | None = None
        self._running: bool = False
        self._links: dict[int, tuple[RNS.Link, io.BufferedRWPair]] = {}

    @staticmethod
    def _default_identity_path() -> str:
        """Get default identity path."""
        home = os.path.expanduser("~")
        return os.path.join(home, ".rnhttp", "identity")

    def _load_or_create_identity(self) -> RNS.Identity:
        """Load existing identity or create new one."""
        if os.path.exists(self._identity_path):
            identity = RNS.Identity.from_file(self._identity_path)  # pyright: ignore[reportUnknownMemberType]
            if identity is not None:
                return identity

        identity = RNS.Identity()
        os.makedirs(os.path.dirname(self._identity_path), exist_ok=True)
        _ = identity.to_file(self._identity_path)  # pyright: ignore[reportUnknownMemberType]

        return identity

    def route(
        self, path: str, method: str = "GET"
    ) -> Callable[[HandlerType], HandlerType]:
        """Decorator to register a handler for a path and method.

        Usage:
            @server.route("/api/data")
            def handle_request(request: HttpRequest) -> HttpResponse:
                return HttpResponse(status=200, body=b"OK")

            @server.route("/api/data", method="POST")
            def handle_post(request: HttpRequest) -> HttpResponse:
                return HttpResponse(status=200, body=b"OK")
        """

        def decorator(handler: HandlerType) -> HandlerType:
            self._handlers[(method.upper(), path)] = handler
            return handler

        return decorator

    def add_handler(self, path: str, handler: HandlerType, method: str = "GET") -> None:
        """Add a handler for a path and method."""
        self._handlers[(method.upper(), path)] = handler

    def set_default_handler(self, handler: HandlerType) -> None:
        """Set a default handler for all requests."""
        self._default_handler = handler

    async def start(self) -> None:
        """Start the HTTP server."""
        identity = self._load_or_create_identity()

        self._destination = RNS.Destination(
            identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "HTTP",
            str(self._port),
        )

        self._running = True
        _ = self._destination.accepts_links(True)  # pyright: ignore[reportUnknownMemberType]
        self._destination.set_link_established_callback(self._on_link)  # pyright: ignore[reportUnknownMemberType]
        _ = self._destination.announce()  # pyright: ignore[reportUnknownMemberType]

    async def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        for link, _ in list(self._links.values()):
            link.teardown()

        self._links.clear()
        if self._destination is not None:
            _ = self._destination.accepts_links(False)  # pyright: ignore[reportUnknownMemberType]

    def _on_link(self, link: RNS.Link) -> None:
        """Handle incoming link."""
        if not self._running:
            link.teardown()
            return

        print(f"Connected: {link}")

        def _on_reader_ready(ready: int) -> None:
            """Handle incoming data on reader."""
            nonlocal link
            print(f"Reader ready {link}: {ready}")
            _, buffer = self._links.get(id(link), (None, None))
            if buffer is None:
                return

            request: HttpRequest | Awaitable[HttpRequest] | None = None
            response: HttpResponse | Awaitable[HttpResponse] | None = None
            try:
                request = HttpRequest.parse(buffer.read(ready))

            except Exception as e:
                response = HttpResponse(
                    status=400,
                    body=str(e).encode("utf-8"),
                )

            if response is None:
                assert request is not None
                try:
                    response = self._handle_request(link, request)

                except Exception as e:
                    response = HttpResponse(
                        status=500,
                        body=str(e).encode("utf-8"),
                    )

            await_in_sync(self._send_response(buffer, response))

        link.set_link_closed_callback(self._on_close)  # pyright: ignore[reportUnknownMemberType]
        channel = link.get_channel()
        buffer = RNS.Buffer.create_bidirectional_buffer(0, 1, channel, _on_reader_ready)
        self._links[id(link)] = (link, buffer)

    async def _send_response(
        self,
        writer: io.BufferedRWPair,
        response: HttpResponse | Awaitable[HttpResponse],
    ) -> None:
        """Send response back on the link."""
        if isinstance(response, Awaitable):
            response = await response

        try:
            _ = writer.write(bytes(response))
            writer.flush()

        except Exception:  # TODO narrow this to actual exception
            link_id = None
            for lid, (_, buffer) in self._links.items():
                if buffer is writer:
                    link_id = lid
                    break

            if link_id is not None:
                link, _ = self._links.pop(link_id, (None, None))
                if link is not None:
                    link.teardown()

    def _on_close(self, link: RNS.Link) -> None:
        """Handle link close."""
        _ = self._links.pop(id(link), None)

    def _handle_request(
        self, link: RNS.Link, request: HttpRequest
    ) -> HttpResponse | Awaitable[HttpResponse]:
        """Handle incoming HTTP request."""
        path = request.path
        method = request.method.upper()
        print(f"{link} {method} {path}")

        key = (method, path)
        if key in self._handlers:
            return self._handlers[key](request)

        for (_, p), handler in self._handlers.items():
            if self._match_pattern(p, path):
                return handler(request)

        if self._default_handler:
            return self._default_handler(request)

        return HttpResponse(
            status=404,
            reason="Not Found",
            body=b"Not Found",
        )

    def _match_pattern(self, pattern: str, path: str) -> bool:
        """Simple pattern matching for routes."""
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    @property
    def port(self) -> int:
        """Get the server port."""
        return self._port

    @property
    def destination_hash(self) -> str | None:
        """Get the server destination hash as hex string."""
        if self._destination is None:
            return None

        assert isinstance(self._destination.hexhash, str | None)  # pyright: ignore[reportAny]
        return self._destination.hexhash

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running


async def main():
    parser = argparse.ArgumentParser(description="HTTP/1.1 server over Reticulum")
    _ = parser.add_argument("port", type=int, help="Port number")
    _ = parser.add_argument("--config", type=str, help="RNS config directory")
    _ = parser.add_argument("--identity", type=str, help="Identity file path")
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    args = parser.parse_args()

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if args.verbose else RNS.LOG_WARNING)

    server = HttpServer(
        port=args.port,
        identity_path=args.identity,
    )

    def default_handler(_request: HttpRequest) -> HttpResponse:
        return HttpResponse(
            status=200,
            body=b"Hello world!",
        )

    server.set_default_handler(default_handler)

    await server.start()
    print(f"Server listening on HTTP.{server.port}", file=sys.stderr, flush=True)
    print(f"Destination hash: {server.destination_hash}", file=sys.stderr, flush=True)

    try:
        while server.is_running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        pass

    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
