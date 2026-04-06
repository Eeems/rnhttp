"""SOCKS5 proxy over RNS example.

Listens on a local TCP port as a SOCKS5 proxy. After the SOCKS5 handshake,
HTTP requests are parsed and forwarded to an RNS HTTP server using
HttpClient.request. The RequestIO buffer serves as the body stream,
avoiding double buffering.

Usage:
    python examples/socks_proxy.py <destination_hash> \\
        --listen 127.0.0.1:1080 \\
        --config /path/to/rns \\
        --identity /path/to/identity

Then use with curl:
    curl --socks5-hostname 127.0.0.1:1080 http://frogfind.com/
"""

import argparse
import asyncio
import ipaddress
import logging
import os
import struct
import threading
from collections.abc import Callable
from typing import (
    Any,
    Generic,
    TypeVar,
)

import RNS

from rnhttp import HttpClient
from rnhttp._compat import override
from rnhttp._http import (
    Request,
    RequestIO,
    Response,
)
from rnhttp._pipe import PipeIO

log = logging.getLogger(__name__)

# SOCKS5 constants
SOCKS_VERSION = 0x05
SOCKS_AUTH_NO_AUTH = 0x00
SOCKS_CMD_CONNECT = 0x01
SOCKS_ATYP_IPV4 = 0x01
SOCKS_ATYP_DOMAIN = 0x03
SOCKS_ATYP_IPV6 = 0x04
SOCKS_REPLY_SUCCEEDED = 0x00
SOCKS_REPLY_COMMAND_NOT_SUPPORTED = 0x07


T = TypeVar("T")


class ThreadWithReturnValue(threading.Thread, Generic[T]):
    """
    Thread subclass that can return a value.
    Fully typed for Python 3.10+.
    """

    def __init__(self, target: Callable[..., T], *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportExplicitAny, reportAny]  # noqa: ANN401
        super().__init__()
        self.target: Callable[..., T] = target
        self.args: tuple[Any, ...] = args  # pyright: ignore[reportExplicitAny]
        self.kwargs: dict[str, Any] = kwargs  # pyright: ignore[reportExplicitAny]
        self._result: T | None = None

    @override
    def run(self) -> None:
        self._result = self.target(*self.args, **self.kwargs)

    @property
    def result(self) -> T | None:
        return self._result


class SocksError(Exception):
    """Error during SOCKS5 handshake."""

    reply_code: int

    def __init__(self, reply_code: int, message: str) -> None:
        self.reply_code = reply_code
        super().__init__(message)


async def read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly n bytes from the stream."""
    return await reader.readexactly(n)


async def socks5_handshake(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[str, int]:
    """Perform SOCKS5 handshake. Returns (host, port) for CONNECT command."""
    # --- Method selection ---
    greeting = await read_exact(reader, 2)
    log.debug("SOCKS greeting: %s", greeting.hex())
    if greeting[0] != SOCKS_VERSION:
        raise SocksError(
            SOCKS_REPLY_COMMAND_NOT_SUPPORTED,
            f"Unsupported SOCKS version: {greeting[0]}",
        )

    nmethods = greeting[1]
    methods = await read_exact(reader, nmethods)
    log.debug("SOCKS methods (%d): %s", nmethods, methods.hex())

    # Check auth methods before replying
    if SOCKS_AUTH_NO_AUTH not in methods:
        writer.write(struct.pack("BB", SOCKS_VERSION, 0xFF))
        await writer.drain()
        raise SocksError(SOCKS_REPLY_COMMAND_NOT_SUPPORTED, "No supported auth methods")

    # Reply: SOCKS5, no auth
    writer.write(struct.pack("BB", SOCKS_VERSION, SOCKS_AUTH_NO_AUTH))
    await writer.drain()
    log.debug("Sent method selection reply: 05 00")

    # --- Request ---
    log.debug("Waiting for SOCKS request header (4 bytes)...")
    request_header = await read_exact(reader, 4)
    log.debug("SOCKS request header: %s", request_header.hex())
    version, cmd, _reserved, atyp = request_header

    if version != SOCKS_VERSION:
        raise SocksError(
            SOCKS_REPLY_COMMAND_NOT_SUPPORTED,
            f"Unsupported SOCKS version in request: {version}",
        )

    if cmd != SOCKS_CMD_CONNECT:
        writer.write(
            struct.pack(
                "BBBB",
                SOCKS_VERSION,
                SOCKS_REPLY_COMMAND_NOT_SUPPORTED,
                0x00,
                SOCKS_ATYP_IPV4,
            )
            + struct.pack("!IH", 0, 0)
        )
        await writer.drain()
        raise SocksError(
            SOCKS_REPLY_COMMAND_NOT_SUPPORTED,
            f"Command {cmd} not supported (only CONNECT)",
        )

    # --- Parse destination address ---
    host: str
    if atyp == SOCKS_ATYP_IPV4:
        addr_bytes = await read_exact(reader, 4)
        host = ".".join(str(b) for b in addr_bytes)

    elif atyp == SOCKS_ATYP_IPV6:
        addr_bytes = await read_exact(reader, 16)
        host = str(ipaddress.IPv6Address(addr_bytes))

    elif atyp == SOCKS_ATYP_DOMAIN:
        domain_len = (await read_exact(reader, 1))[0]
        domain = await read_exact(reader, domain_len)
        host = domain.decode("utf-8", errors="replace")

    else:
        raise SocksError(
            SOCKS_REPLY_COMMAND_NOT_SUPPORTED, f"Unknown address type: {atyp}"
        )

    port_bytes = await read_exact(reader, 2)
    port: int = struct.unpack("!H", port_bytes)[0]  # pyright: ignore[reportAny]

    log.info("SOCKS5 CONNECT %s:%d", host, port)

    # Reply: success, bound address 0.0.0.0:0
    reply = struct.pack(
        "!BBBB",
        SOCKS_VERSION,
        SOCKS_REPLY_SUCCEEDED,
        0x00,
        SOCKS_ATYP_IPV4,
    ) + struct.pack("!IH", 0, 0)
    writer.write(reply)
    await writer.drain()

    return host, port


async def pipe_tcp_to_request_io(
    tcp_reader: asyncio.StreamReader,
    request_io: RequestIO,
) -> None:
    """Forward bytes from TCP to RequestIO for HTTP parsing."""
    try:
        while True:
            data = await tcp_reader.read(4096)
            if not data:
                log.debug("TCP -> RequestIO: EOF")
                request_io.close()
                break

            _ = request_io.write(data)
            request_io.flush()
            log.debug("TCP -> RequestIO: %d bytes", len(data))
    except (ConnectionError, asyncio.IncompleteReadError, OSError) as e:
        log.debug("TCP -> RequestIO error: %s", e)
        request_io.close()


async def pipe_response_to_tcp(
    pipe: PipeIO,
    tcp_writer: asyncio.StreamWriter,
) -> None:
    """Forward bytes from ResponseIO to TCP client."""
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                log.debug("ResponseIO -> TCP: EOF")
                break

            tcp_writer.write(chunk)
            await tcp_writer.drain()
            log.debug("ResponseIO -> TCP: %d bytes", len(chunk))
    except (ConnectionError, OSError) as e:
        log.debug("ResponseIO -> TCP error: %s", e)
        tcp_writer.close()


async def handle_client(
    tcp_reader: asyncio.StreamReader,
    tcp_writer: asyncio.StreamWriter,
    destination_hash: str,
    identity_path: str | None,
) -> None:
    """Handle one SOCKS5 client connection."""
    peer = tcp_writer.get_extra_info("peername")  # pyright: ignore[reportAny]
    peer_str = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)  # pyright: ignore[reportAny]
    log.info("Client connected: %s", peer_str)

    client: HttpClient | None = None
    try:
        # Step 1: SOCKS5 handshake
        _host, socks_port = await socks5_handshake(tcp_reader, tcp_writer)

        # Step 2: Connect to RNS server
        client = HttpClient(
            destination_hash=destination_hash,
            port=socks_port,
            identity_path=identity_path,
        )
        await client.connect()
        log.info("RNS link established on port %d", socks_port)

        # Step 3: Parse incoming HTTP request from TCP into RequestIO
        request_io = RequestIO()

        # Start reading TCP data into RequestIO (fills buffer as body arrives)
        tcp_to_request_task = asyncio.create_task(
            pipe_tcp_to_request_io(tcp_reader, request_io),
            name="tcp_to_request",
        )

        # Wait for headers to be fully parsed (in thread pool to not block event loop)
        if not await asyncio.to_thread(request_io.callbacks.wait_headers, timeout=30):
            log.error("Timeout waiting for HTTP headers from %s", peer_str)
            _ = tcp_to_request_task.cancel()
            return

        # For requests without a body (no content-length, no transfer-encoding),
        # close the buffer to signal EOF. This lets HttpClient.request.sendto()
        # complete without blocking on an empty buffer.
        if (
            "content-length" not in request_io.headers
            and "transfer-encoding" not in request_io.headers
        ):
            request_io.buffer.close()

        # Debug: log what was parsed
        log.info(
            "Parsed request: method=%s url=%s headers=%s",
            request_io.method,
            request_io.url,
            request_io.headers,
        )

        # Step 4: Forward request to RNS server using HttpClient.request
        # Use request_io.buffer (PipeIO) as the body stream - no double buffer!
        # TCP data -> request_io.write() -> parser -> _on_body -> buffer
        # HttpClient.request reads from same buffer
        response_io = await client.send_request(
            Request(
                method=request_io.method,
                url=request_io.url,
                headers={k: ",".join(v) for k, v in request_io.headers.items()},
                body=request_io,
            )
        )
        log.info(
            "Parsed response: status=%d reason=%s headers=%s",
            response_io.status,
            response_io.reason,
            response_io.headers,
        )
        with PipeIO() as pipe:
            response = Response(
                response_io.status,
                response_io.reason,
                {k: ",".join(v) for k, v in response_io.headers.items()},
                response_io,
            )

            def send(response: Response, pipe: PipeIO) -> int:
                size = response.sendto(pipe)
                pipe.close()
                return size

            # Step 5: Forward response back to TCP client
            send_thread = ThreadWithReturnValue[int](send, response, pipe)
            send_thread.start()
            await pipe_response_to_tcp(pipe, tcp_writer)
            send_thread.join()
            size = send_thread.result

        log.info("Request complete for %s with %d bytes", peer_str, size)

        # Cancel the TCP reader task if still running
        _ = tcp_to_request_task.cancel()
        try:
            await tcp_to_request_task

        except asyncio.CancelledError:
            pass

    except SocksError as e:
        log.warning("SOCKS error from %s: %s", peer_str, e)

    except Exception as e:
        log.error("Error handling client %s: %s", peer_str, e, exc_info=True)

    finally:
        if client is not None:
            await client.close()

        tcp_writer.close()
        try:
            await tcp_writer.wait_closed()
        except Exception as e:
            log.debug("Error waiting for TCP writer to close: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOCKS5 proxy over RNS")
    _ = parser.add_argument("destination", help="Server destination hash (hex)")
    _ = parser.add_argument(
        "--listen",
        default="127.0.0.1:1080",
        help="Local listen address (default: 127.0.0.1:1080)",
    )
    _ = parser.add_argument("--config", help="RNS config directory")
    _ = parser.add_argument("--identity", help="Identity file path")
    _ = parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    listen_host: str
    listen_port: int
    assert isinstance(args.listen, str)  # pyright: ignore[reportAny]
    listen_host, listen_port_str = args.listen.rsplit(":", 1)
    listen_port = int(listen_port_str)

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path: str | None = args.config or os.environ.get("RNS_CONFIG_PATH")
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if args.verbose else RNS.LOG_WARNING)

    assert isinstance(args.destination, str)  # pyright: ignore[reportAny]
    destination_hash: str = args.destination
    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    identity_path: str | None = args.identity

    async def loop() -> None:
        server = await asyncio.start_server(
            lambda r, w: handle_client(
                r,
                w,
                destination_hash,
                identity_path,
            ),
            listen_host,
            listen_port,
        )

        print(f"SOCKS5 proxy listening on {listen_host}:{listen_port}")
        print(f"RNS destination: {destination_hash}")
        connect_host = listen_host if listen_host != "0.0.0.0" else "127.0.0.1"  # noqa: S104
        print(
            f"Use: curl --socks5-hostname {connect_host}:{listen_port} http://frogfind.com/"
        )
        print("Press Ctrl+C to stop.")

        try:
            async with server:
                await server.serve_forever()

        except KeyboardInterrupt:
            pass

        finally:
            print("\nProxy stopped.")

    asyncio.run(loop())
