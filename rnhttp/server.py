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
from typing import (
    Any,
    TypeVar,
)

import RNS

from ._http import RequestIO, Response

HandlerType = Callable[
    [RequestIO, Response],
    Generator[None] | AsyncGenerator[None] | None,
]

ParamSpec = list[tuple[str, type]]
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


def parse_param_spec(param: str) -> tuple[str, type]:
    """Parse a parameter specification like '{id:int}' into (name, type).

    Args:
        param: A string like '{id:int}' or '{user_id}'

    Returns:
        A tuple of (name, type) where type defaults to str if not specified
    """
    param = param.strip()
    if not (param.startswith("{") and param.endswith("}")):
        msg = f"Invalid parameter specification: {param}"
        raise ValueError(msg)

    inner = param[1:-1]
    type_constructor: type
    if ":" in inner:
        name, type_name = inner.split(":", 1)
        type_name = type_name.strip()
        if type_name not in ("int", "str", "float", "bool"):
            msg = f"Unknown type: {type_name}"
            raise ValueError(msg)

        method = locals()[type_name]  # pyright: ignore[reportAny]
        assert isinstance(method, type)
        type_constructor = method
    else:
        name = inner.strip()
        type_constructor = str

    return (name, type_constructor)


def _parse_path_params(pattern: str) -> ParamSpec:
    """Parse a route pattern to extract parameter specifications.

    Args:
        pattern: A route pattern like '/users/{id:int}/posts/{post_id}'

    Returns:
        A list of (name, type) tuples for each parameter
    """
    param_specs: ParamSpec = []
    parts = pattern.split("/")
    for part in parts:
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            param_specs.append(parse_param_spec(part))
    return param_specs


def match_pattern(pattern: str, path: str) -> bool:
    """Check if a path matches a route pattern.

    Args:
        pattern: A route pattern like '/users/{id:int}'
        path: An actual path like '/users/123'

    Returns:
        True if the path matches the pattern, False otherwise
    """
    pattern_parts = [p for p in pattern.split("/") if p]
    path_parts = [p for p in path.split("/") if p]

    if len(pattern_parts) != len(path_parts):
        return False

    for pp, p in zip(pattern_parts, path_parts):
        if pp == p:
            continue
        if pp.startswith("{") and pp.endswith("}"):
            continue
        return False

    return True


def extract_params(pattern: str, path: str, param_specs: ParamSpec) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny, reportUnusedParameter]
    """Extract parameter values from a path using the param specs.

    Args:
        pattern: The route pattern like '/users/{id:int}'
        path: The actual path like '/users/123'
        param_specs: The parameter specifications from _parse_path_params

    Returns:
        A dictionary of parameter names to typed values

    Raises:
        ValueError: If a type conversion fails
    """
    pattern_parts = [p for p in pattern.split("/") if p]
    path_parts = [p for p in path.split("/") if p]
    params: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]

    for pp, p in zip(pattern_parts, path_parts):
        if pp.startswith("{") and pp.endswith("}"):
            name, type_constructor = parse_param_spec(pp)
            try:
                params[name] = type_constructor(p)

            except ValueError as e:
                msg = f"Invalid value for parameter {name}: {p}"
                raise ValueError(msg) from e

    return params


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
        self._handlers: dict[tuple[str, str], tuple[HandlerType, ParamSpec]] = {}
        self._default_handler: HandlerType | None = None
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
            param_specs = _parse_path_params(path)
            self._handlers[(method.upper(), path)] = (handler, param_specs)
            return handler

        return decorator

    def add_handler(self, path: str, handler: HandlerType, method: str = "GET") -> None:
        """Add a handler for a path and method."""
        param_specs = _parse_path_params(path)
        self._handlers[(method.upper(), path)] = (handler, param_specs)

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
            nonlocal request_io, reader
            self.on_reader_ready(ready, reader, request_io)

        print(f"Connected: {link}")
        request_io = RequestIO()
        link.set_link_closed_callback(self.on_link_closed)  # pyright: ignore[reportUnknownMemberType]
        channel = link.get_channel()
        reader = RNS.Buffer.create_reader(0, channel, callback)
        writer = RNS.Buffer.create_writer(0, channel)
        threading.Thread(
            target=self.handle_request,
            args=(link, request_io, writer),
        ).start()

    def on_reader_ready(
        self,
        ready: int,
        reader: io.BufferedReader,
        request_io: RequestIO,
    ) -> None:
        """Handle incoming data on reader."""
        if not ready:
            reader.close()
            request_io.close()
            return

        data = reader.read(ready)
        _ = request_io.write(data)
        request_io.flush()

    def on_link_closed(self, link: RNS.Link) -> None:
        """Handle link close."""
        print(f"Closed: {link}")

    def get_handler(
        self, request_io: RequestIO
    ) -> tuple[HandlerType, ParamSpec, str] | None:
        """Find handler for request. Returns None if no handler found.

        Returns:
            A tuple of (handler, param_specs, pattern) if found, None otherwise
        """
        method = request_io.method
        path = request_io.url.path
        if path is None:
            return None

        # Exact match first
        key = (method, path)
        if key in self._handlers:
            handler, param_specs = self._handlers[key]
            return (handler, param_specs, path)

        # Pattern match
        for (m, pattern), value in self._handlers.items():
            if method != m:
                continue
            if match_pattern(pattern, path):
                handler, param_specs = value
                return (handler, param_specs, pattern)

        # Default handler if no match
        if self._default_handler is not None:
            return (self._default_handler, [], path)

        return None

    def handle_request(
        self,
        link: RNS.Link,
        request_io: RequestIO,
        writer: io.BufferedWriter,
    ) -> None:
        """Handle incoming HTTP request."""
        method = request_io.method
        path = request_io.url.path
        print(f"{link} {method} {path}")
        result = self.get_handler(request_io)
        if result is None:
            request_io.close()
            Response(404, body=b"Not Found").sendto(writer)
            print(f"{link} {method} {path} 404")
            return

        handler, param_specs, route_pattern = result
        response = Response(status=200)
        try:
            params = extract_params(route_pattern, path or "", param_specs)
        except ValueError:
            request_io.close()
            Response(400, body=b"Bad Request").sendto(writer)
            print(f"{link} {method} {path} 400")
            return

        try:
            # Extract params from path (may raise ValueError -> 400)
            gen = handler(request_io, response, **params)
            request_io.close()
            if isinstance(gen, GeneratorType | AsyncGeneratorType):
                sendto_thread = threading.Thread(target=response.sendto, args=(writer,))
                if isinstance(gen, AsyncGeneratorType):
                    consume_async_generator(gen)

                else:
                    consume_generator(gen)

                sendto_thread.start()
                sendto_thread.join()

            else:
                response.sendto(writer)

            print(f"{link} {method} {path} {response.status}")

        except Exception as e:
            request_io.close()
            Response(500, body=str(e).encode()).sendto(writer)
            print(f"{link} {method} {path} 500")
            raise

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
