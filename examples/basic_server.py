"""Basic HTTP server example using rnhttp."""

import argparse
import asyncio
import os

import RNS

from rnhttp import HttpServer
from rnhttp._http import (
    RequestIO,
    Response,
)

if __name__ == "__main__":
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

    assert isinstance(args.port, int)  # pyright: ignore[reportAny]
    port: int = args.port
    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    identity_path: str | None = args.identity
    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path: str | None = args.config
    if config_path is None:
        config_path = os.environ.get("RNS_CONFIG_PATH", None)
    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    verbose: bool = args.verbose

    server = HttpServer(
        port=port,
        identity_path=identity_path,
    )

    @server.route("/")
    def _handle_root(_request: RequestIO, response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to root path."""
        response.status = 200
        response.add_header("Content-Type", "text/plain")
        response.body = b"Hello from RNS HTTP Server!"

    @server.route("/hello")
    def _handle_hello(_request: RequestIO, response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /hello path."""
        response.status = 200
        response.add_header("Content-Type", "application/json")
        response.body = b'{"message": "Hello, World!"}'

    @server.route("/echo/*")
    def _handle_echo(request: RequestIO, response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /echo/* path - echoes back the path."""
        response.status = 200
        response.add_header("Content-Type", "text/plain")
        path = request.url.path or "/echo"
        response.body = f"Echo: {path}".encode()

    @server.route("/resource")
    @server.route("/resource", "POST")
    @server.route("/resource", "PUT")
    @server.route("/resource", "DELETE")
    def _handle_resource(_request: RequestIO, response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /resource path."""
        response.status = 200

    print("Starting RNS HTTP Server...")
    print("=" * 50)

    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    async def loop():
        await server.start()

        print(f"Server listening on HTTP.{server.port}")
        print(f"Destination: <{server.destination_hash}>")
        print("\nServer is running. Press Ctrl+C to stop.")
        print("=" * 50)

        try:
            await asyncio.sleep(float("infinity"))

        except KeyboardInterrupt:
            print("Server stopped.")

    asyncio.run(loop())
