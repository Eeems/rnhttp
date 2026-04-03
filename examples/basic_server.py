"""Basic HTTP server example using rnhttp."""

import argparse
import asyncio
import os

import RNS

from rnhttp import (
    HttpRequest,
    HttpResponse,
    HttpServer,
)


async def main() -> None:
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
    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]

    config_path = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)

    server = HttpServer(
        port=args.port,
        identity_path=args.identity,
    )

    @server.route("/")
    async def handle_root(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to root path."""
        return HttpResponse(
            status=200,
            headers={"Content-Type": "text/plain"},
            body=b"Hello from RNS HTTP Server!",
        )

    @server.route("/hello")
    async def handle_hello(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /hello path."""
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"message": "Hello, World!"}',
        )

    @server.route("/echo/*")
    async def handle_echo(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /echo/* path - echoes back the path."""
        return HttpResponse(
            status=200,
            headers={"Content-Type": "text/plain"},
            body=f"Echo: {request.path}".encode(),
        )

    @server.route("/resource")
    async def handle_resource(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /echo/* path - echoes back the path."""
        return HttpResponse(status=200)

    print("Starting RNS HTTP Server...")
    print("=" * 50)

    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if args.verbose else RNS.LOG_WARNING)
    await server.start()

    print(f"Server listening on HTTP.{server.port}")
    print(f"Destination: <{server.destination_hash}>")
    print("\nServer is running. Press Ctrl+C to stop.")
    print("=" * 50)

    try:
        await asyncio.sleep(float("infinity"))

    except KeyboardInterrupt:
        print("\nStopping server...")
        await server.stop()
        print("Server stopped.")


if __name__ == "__main__":
    asyncio.run(main())
