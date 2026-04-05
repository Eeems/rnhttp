"""HTTP/1.1 server over RNS."""

import argparse
import asyncio
import io
import os
import sys
import threading
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Generator,
)
from types import (
    AsyncGeneratorType,
    GeneratorType,
)
from typing import TypeVar

import RNS

from ._http import RequestIO, Response

HandlerType = Callable[
    [RequestIO, Response],
    Generator[None] | AsyncGenerator[None] | None,
]

T = TypeVar("T")


def await_in_sync(awaitable: Awaitable[T]) -> T:
    """Run any awaitable from synchronous code safely."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(awaitable)

    except RuntimeError:
        return asyncio.run(awaitable)  # pyright: ignore[reportUnknownVariableType, reportArgumentType]


def consume_generator(gen: Generator[None]) -> None:
    try:
        for _ in gen:
            pass

    except StopIteration:
        pass


def consume_async_generator(gen: AsyncGenerator[None]) -> None:
    async def fn(gen: AsyncGenerator[None]) -> None:
        try:
            async for _ in gen:
                pass

        except StopAsyncIteration:
            pass

    await_in_sync(fn(gen))


def match_pattern(pattern: str, path: str) -> bool:
    """Simple pattern matching for routes."""
    # TODO make this not so shit
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])

    return path == pattern


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
        self._running: bool = False

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
            def handle_request(request: RequestIO, response: Response) -> None:
                response.status = 200
                response.body = b"OK"

            @server.route("/api/data", method="POST")
            def handle_post(request: RequestIO, response: Response) -> None:
                response.status = 200
                response.body = b"OK"
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
        self._handlers[("*", "*")] = handler

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
        self._destination.set_link_established_callback(self.on_link_established)  # pyright: ignore[reportUnknownMemberType]
        _ = self._destination.accepts_links(True)  # pyright: ignore[reportUnknownMemberType]
        _ = self._destination.announce()  # pyright: ignore[reportUnknownMemberType]

    async def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        if self._destination is not None:
            _ = self._destination.accepts_links(False)  # pyright: ignore[reportUnknownMemberType]

    def on_link_established(self, link: RNS.Link) -> None:
        """Handle incoming link."""

        def callback(ready: int) -> None:
            nonlocal request_io, link_buffer
            print(f"callback({ready})")
            self.on_reader_ready(link_buffer, request_io, ready)

        print(f"Connected: {link}")
        request_io = RequestIO()
        link.set_link_closed_callback(self.on_link_closed)  # pyright: ignore[reportUnknownMemberType]
        link_buffer = RNS.Buffer.create_bidirectional_buffer(
            0,
            0,
            link.get_channel(),
            callback,
        )
        threading.Thread(
            target=self.handle_request,
            args=(
                link,
                request_io,
                link_buffer,
            ),
        ).start()

    def on_reader_ready(
        self,
        buffer: io.BufferedRWPair,
        request_io: RequestIO,
        ready: int,
    ) -> None:
        """Handle incoming data on reader."""
        data = buffer.read(ready)
        _ = request_io.write(data)
        request_io.flush()

    def on_link_closed(self, link: RNS.Link) -> None:
        """Handle link close."""
        print(f"Closed: {link}")

    def get_handler(self, request_io: RequestIO) -> HandlerType | None:
        """Find handler for request. Returns None if no handler found."""
        method = request_io.method
        path = request_io.url.path
        if path is None:
            return None

        key = (method, path)
        if key in self._handlers:
            return self._handlers[key]

        for (_, p), handler in self._handlers.items():
            if match_pattern(p, path):
                return handler

        return None

    def handle_request(
        self,
        link: RNS.Link,
        request_io: RequestIO,
        writer: io.BufferedRWPair,
    ) -> None:
        """Handle incoming HTTP request."""
        method = request_io.method
        path = request_io.url.path
        print(f"{link} {method} {path}")

        handler = self.get_handler(request_io)

        if handler is None:
            while request_io.read(4096):
                pass

            Response(404, body=b"Not Found").sendto(writer)
            return

        response = Response(status=200)
        try:
            gen = handler(request_io, response)
            if isinstance(gen, GeneratorType | AsyncGeneratorType):
                sendto_thread = threading.Thread(target=response.sendto, args=(writer,))
                resume_thread = threading.Thread(
                    target=(
                        consume_async_generator
                        if isinstance(gen, AsyncGeneratorType)
                        else consume_generator
                    ),
                    args=(gen,),
                )
                sendto_thread.start()
                resume_thread.start()
                sendto_thread.join()
                resume_thread.join()

            else:
                response.sendto(writer)

        except Exception as e:
            Response(500, body=str(e).encode()).sendto(writer)

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

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    assert isinstance(args.port, int)  # pyright: ignore[reportAny]
    server = HttpServer(
        port=args.port,
        identity_path=args.identity,
    )

    def default_handler(_request: RequestIO, response: Response) -> None:
        response.status = 200
        response.body = b"Hello world!"

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
