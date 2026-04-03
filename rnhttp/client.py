"""HTTP/1.1 client over RNS."""

import argparse
import asyncio
import os
import sys
import threading
from typing import Any, cast

import RNS

from .types import (
    HttpRequest,
    HttpResponse,
)


class TransportError(Exception):
    """Exception raised for transport errors."""

    pass


class HttpClient:
    """HTTP/1.1 client using RNS for transport."""

    def __init__(
        self,
        destination_hash: bytes | str,
        port: int,
        identity_path: str | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 60.0,
        read_timeout: float = 30.0,
    ) -> None:
        if isinstance(destination_hash, str):
            destination_hash = bytes.fromhex(destination_hash)

        self._destination_hash: bytes = destination_hash
        self._port: int = port
        self._identity_path: str = identity_path or self._default_identity_path()
        self._connect_timeout: float = connect_timeout
        self._request_timeout: float = request_timeout
        self._read_timeout: float = read_timeout
        self._identity: RNS.Identity | None = None
        self._link: RNS.Link | None = None

    @staticmethod
    def _default_identity_path() -> str:
        """Get default identity path."""
        home = os.path.expanduser("~")
        return os.path.join(home, ".rnhttp", "identity")

    def _load_or_create_identity(self) -> RNS.Identity:
        """Load existing identity or create new one."""
        if self._identity is not None:
            return self._identity

        if os.path.exists(self._identity_path):
            self._identity = RNS.Identity.from_file(self._identity_path)  # pyright: ignore[reportUnknownMemberType]
            if self._identity is not None:
                return self._identity

        self._identity = RNS.Identity()
        os.makedirs(os.path.dirname(self._identity_path), exist_ok=True)
        _ = self._identity.to_file(self._identity_path)  # pyright: ignore[reportUnknownMemberType]

        return self._identity

    async def connect(self) -> None:
        """Connect to the server."""
        _ = self._load_or_create_identity()
        RNS.Transport.request_path(self._destination_hash)  # pyright: ignore[reportUnknownMemberType]
        if not RNS.Transport.has_path(self._destination_hash):  # pyright: ignore[reportUnknownMemberType]
            if not RNS.Transport.await_path(  # pyright: ignore[reportUnknownMemberType]
                self._destination_hash, self._connect_timeout
            ):
                raise TransportError("Timeout waiting for path to server")

        server_identity = RNS.Identity.recall(self._destination_hash)  # pyright: ignore[reportUnknownMemberType]
        if server_identity is None:
            raise TransportError("Could not recall server identity")

        dest = RNS.Destination(
            server_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "HTTP",
            str(self._port),
        )

        connected = threading.Event()

        def on_established(_link: RNS.Link) -> None:
            nonlocal connected
            connected.set()

        self._link = RNS.Link(dest, on_established)
        if not connected.wait(self._connect_timeout):
            if self._link is not None:  # pyright: ignore[reportUnnecessaryComparison]
                self._link.teardown()
                self._link = None

            raise TransportError("Connection timeout")

    async def request(
        self,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        """Send an HTTP request.

        Args:
            path: Request path
            method: HTTP method (GET, POST, etc.)
            headers: Additional headers
            body: Request body

        Returns:
            HttpResponse object

        Raises:
            TransportError: If request fails
        """
        if self._link is None:
            await self.connect()

        if self._link is None:
            raise TransportError("Not connected")

        request = HttpRequest(
            method=method,
            path=path,
            headers=headers or {},
            body=body,
        )

        response_data = await self._send_request(bytes(request))
        return HttpResponse.parse(response_data)

    async def _send_request(self, data: bytes) -> bytes:
        """Send request data and wait for response."""
        if self._link is None:
            raise TransportError("Not connected")

        channel = self._link.get_channel()

        response_event = threading.Event()
        response_data: bytes | None = None
        response_error: Exception | None = None

        def on_reader_ready(ready: int) -> None:
            print(f"Reponse ready: {ready}")
            nonlocal response_data, response_error, response_event, buffer
            try:
                response_data = buffer.read(ready)
                response_event.set()

            except Exception as e:
                response_error = e
                response_event.set()

        buffer = RNS.Buffer.create_bidirectional_buffer(1, 0, channel, on_reader_ready)
        _ = buffer.write(data)
        buffer.flush()

        if not response_event.wait(self._request_timeout):
            raise TransportError("Request timeout")

        if response_error is not None:
            raise TransportError(  # pyright: ignore[reportUnreachable]
                f"Request failed: {response_error}"
            ) from response_error

        if response_data is None:
            raise TransportError("No response received")

        return response_data  # pyright: ignore[reportUnreachable]

    async def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Send GET request."""
        return await self.request(path, "GET", headers)

    async def post(
        self,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Send POST request."""
        return await self.request(path, "POST", headers, body)

    async def put(
        self,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Send PUT request."""
        return await self.request(path, "PUT", headers, body)

    async def delete(
        self,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Send DELETE request."""
        return await self.request(path, "DELETE", headers)

    async def close(self) -> None:
        """Close the connection."""
        if self._link is not None:
            self._link.teardown()
            self._link = None

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._link is not None

    async def __aenter__(self) -> "HttpClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:  # pyright: ignore[reportAny, reportExplicitAny]  # noqa: ANN401
        """Async context manager exit."""
        await self.close()


async def main():
    parser = argparse.ArgumentParser(description="HTTP/1.1 client over Reticulum")
    _ = parser.add_argument("destination", type=str, help="Server destination hash")
    _ = parser.add_argument("port", type=int, help="Server port")
    _ = parser.add_argument("method", type=str, default="GET", help="HTTP method")
    _ = parser.add_argument("path", type=str, default="/", help="Request path")
    _ = parser.add_argument("--config", type=str, help="RNS config directory")
    _ = parser.add_argument("--identity", type=str, help="Identity file path")
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
        dest="verbose",
    )
    _ = parser.add_argument(
        "-H", "--header", action="append", help="Add header (Format: Name: Value)"
    )
    _ = parser.add_argument("--body", type=str, help="Request body")
    _ = parser.add_argument(
        "-r",
        "--response-code",
        action="store_true",
        help="Print the response code and exit",
        dest="response_code",
    )
    args = parser.parse_args()

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if args.verbose else RNS.LOG_WARNING)

    headers: dict[str, str] = {}
    assert isinstance(args.header, list | None)  # pyright: ignore[reportAny]
    if args.header is not None:  # pyright: ignore[reportUnknownMemberType]
        for header in cast(list[str], args.header):
            if "=" in header:
                name, value = header.split("=", 1)
                headers[name] = value

    assert isinstance(args.body, str | None)  # pyright: ignore[reportAny]
    body = args.body.encode("utf-8") if args.body else None

    assert isinstance(args.destination, str)  # pyright: ignore[reportAny]
    assert isinstance(args.port, int)  # pyright: ignore[reportAny]
    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    client = HttpClient(
        destination_hash=args.destination,
        port=args.port,
        identity_path=args.identity,
    )

    assert isinstance(args.method, str)  # pyright: ignore[reportAny]
    assert isinstance(args.path, str)  # pyright: ignore[reportAny]
    try:
        async with client:
            response = await client.request(
                path=args.path,
                method=args.method.upper(),
                headers=headers,
                body=body,
            )
            assert isinstance(args.response_code, bool)  # pyright: ignore[reportAny]
            if args.response_code:
                print(response.status)

            else:
                _ = sys.stdout.write(
                    f"{response.version} {response.status} {response.reason}\n"
                )
                for name, value in response.headers.items():
                    _ = sys.stdout.write(f"{name}: {value}\n")

                _ = sys.stdout.write("\n")
                if response.body:
                    _ = sys.stdout.buffer.write(response.body)

                _ = sys.stdout.buffer.flush()

            sys.exit(0 if response.status < 400 else 1)

    except TransportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
