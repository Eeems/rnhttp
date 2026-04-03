"""Basic HTTP server example using rnhttp."""

import asyncio
import sys

import RNS

from rnhttp import (
    HttpRequest,
    HttpResponse,
    HttpServer,
)


async def main() -> None:
    """Run the HTTP server."""
    server = HttpServer(port=80)

    @server.route("/")
    async def handle_root(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to root path."""
        return HttpResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/plain"},
            body=b"Hello from RNS HTTP Server!",
        )

    @server.route("/hello")
    async def handle_hello(_request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /hello path."""
        return HttpResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"message": "Hello, World!"}',
        )

    @server.route("/echo/*")
    async def handle_echo(request: HttpRequest) -> HttpResponse:  # pyright: ignore[reportUnusedFunction]
        """Handle requests to /echo/* path - echoes back the path."""
        return HttpResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/plain"},
            body=f"Echo: {request.path}".encode(),
        )

    print("Starting RNS HTTP Server...")
    print("=" * 50)

    _ = RNS.Reticulum()
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
