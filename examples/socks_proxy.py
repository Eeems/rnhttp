"""SOCKS5 proxy over RNS example.

Listens on a local TCP port as a SOCKS5 proxy. After the SOCKS5 handshake,
bytes are piped bidirectionally between the TCP client and an RNS HTTP server.

Usage:
    python examples/socks_proxy.py <destination_hash> <port> \\
        --listen 127.0.0.1:1080 \\
        --config /path/to/rns \\
        --identity /path/to/identity

Then use with curl:
    curl --socks5 localhost:1080 http://anything/path
"""

import argparse
import asyncio
import io
import logging
import os
import struct
from typing import cast

import RNS

from rnhttp import HttpClient

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


class SocksError(Exception):
    """Error during SOCKS5 handshake."""

    def __init__(self, reply_code: int, message: str) -> None:
        self.reply_code = reply_code
        super().__init__(message)


async def read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly n bytes from the stream."""
    data = await reader.readexactly(n)
    return cast(bytes, data)


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

    # Reply: SOCKS5, no auth
    writer.write(struct.pack("BB", SOCKS_VERSION, SOCKS_AUTH_NO_AUTH))
    await writer.drain()
    log.debug("Sent method selection reply: 05 00")

    if SOCKS_AUTH_NO_AUTH not in methods:
        raise SocksError(SOCKS_REPLY_COMMAND_NOT_SUPPORTED, "No supported auth methods")

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
        host = ":".join(f"{b:02x}" for b in addr_bytes)
    elif atyp == SOCKS_ATYP_DOMAIN:
        domain_len = (await read_exact(reader, 1))[0]
        domain = await read_exact(reader, domain_len)
        host = domain.decode("utf-8", errors="replace")
    else:
        raise SocksError(
            SOCKS_REPLY_COMMAND_NOT_SUPPORTED, f"Unknown address type: {atyp}"
        )

    port_bytes = await read_exact(reader, 2)
    port = struct.unpack("!H", port_bytes)[0]

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


async def pipe_tcp_to_rns(
    reader: asyncio.StreamReader,
    rns_writer: "RNS.Buffer",  # pyright: ignore[reportExplicitAny]
) -> None:
    """Forward bytes from TCP to RNS."""
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                log.debug("TCP -> RNS: EOF")
                break
            rns_writer.write(data)
            rns_writer.flush()
            log.debug("TCP -> RNS: %d bytes", len(data))
    except (ConnectionError, asyncio.IncompleteReadError, OSError) as e:
        log.debug("TCP -> RNS error: %s", e)
    finally:
        try:
            rns_writer.close()
        except Exception as e:
            log.debug("Error closing RNS writer: %s", e)


async def handle_client(
    tcp_reader: asyncio.StreamReader,
    tcp_writer: asyncio.StreamWriter,
    destination_hash: str,
    port: int,
    identity_path: str | None,
) -> None:
    """Handle one SOCKS5 client connection."""
    peer = tcp_writer.get_extra_info("peername")
    log.info("Client connected: %s", peer)

    # Each connection gets its own HttpClient and RNS link
    client = HttpClient(
        destination_hash=destination_hash,
        port=port,
        identity_path=identity_path,
    )

    try:
        # Step 1: SOCKS5 handshake
        host, port = await socks5_handshake(tcp_reader, tcp_writer)

        # Step 2: Connect to RNS server
        await client.connect()
        log.info("RNS link established")

        # Step 3: Get RNS channel for raw byte piping
        link = client._link  # pyright: ignore[reportPrivateUsage]
        if link is None:
            raise RuntimeError("RNS link is None after connect")

        channel = link.get_channel()
        rns_writer = RNS.Buffer.create_writer(0, channel)

        # RNS -> TCP callback
        # Note: this callback runs in a RNS thread, not the asyncio event loop thread.
        # Must use run_coroutine_threadsafe to schedule on the correct loop.
        loop = asyncio.get_running_loop()
        rns_reader = RNS.Buffer.create_reader(
            0,
            channel,
            lambda ready: asyncio.run_coroutine_threadsafe(
                _on_rns_data(ready, rns_reader, tcp_writer),
                loop,
            ),
        )

        # Step 4: Bidirectional pipe
        tcp_to_rns_task = asyncio.create_task(
            pipe_tcp_to_rns(tcp_reader, rns_writer),
            name="tcp_to_rns",
        )

        # Wait for either direction to finish
        try:
            await tcp_to_rns_task
        except asyncio.CancelledError:
            pass

        log.info("Client disconnected: %s", peer)

    except SocksError as e:
        log.warning("SOCKS error from %s: %s", peer, e)
    except Exception as e:
        log.error("Error handling client %s: %s", peer, e, exc_info=True)
    finally:
        await client.close()
        tcp_writer.close()
        try:
            await tcp_writer.wait_closed()
        except Exception as e:
            log.debug("Error waiting for TCP writer to close: %s", e)


async def _on_rns_data(
    ready: int,
    rns_reader: "io.BufferedReader",  # pyright: ignore[reportExplicitAny]
    tcp_writer: asyncio.StreamWriter,
) -> None:
    """Callback when RNS has data to send to TCP client."""
    if ready <= 0:
        log.debug("RNS -> TCP: EOF or error")
        tcp_writer.close()
        return

    try:
        data = rns_reader.read(ready)
        if data:
            tcp_writer.write(data)
            await tcp_writer.drain()
            log.debug("RNS -> TCP: %d bytes", len(data))
    except (ConnectionError, OSError) as e:
        log.debug("RNS -> TCP error: %s", e)
        tcp_writer.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="SOCKS5 proxy over RNS")
    parser.add_argument("destination", help="Server destination hash (hex)")
    parser.add_argument("port", type=int, help="Server HTTP port on RNS")
    parser.add_argument(
        "--listen",
        default="127.0.0.1:1080",
        help="Local listen address (default: 127.0.0.1:1080)",
    )
    parser.add_argument("--config", help="RNS config directory")
    parser.add_argument("--identity", help="Identity file path")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Parse listen address
    listen_host, listen_port = args.listen.rsplit(":", 1)
    listen_port = int(listen_port)

    # RNS config
    config_path = args.config or os.environ.get("RNS_CONFIG_PATH")
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if args.verbose else RNS.LOG_WARNING)

    # Start TCP SOCKS5 server
    # Each connection gets its own HttpClient (separate RNS link)
    server = await asyncio.start_server(
        lambda r, w: handle_client(
            r,
            w,
            args.destination,
            args.port,
            args.identity,
        ),
        listen_host,
        listen_port,
    )

    print(f"SOCKS5 proxy listening on {listen_host}:{listen_port}")
    print(f"RNS destination: {args.destination}:{args.port}")
    connect_host = listen_host if listen_host != "0.0.0.0" else "127.0.0.1"  # noqa: S104
    print(
        f"Use: curl --socks5-hostname {connect_host}:{listen_port} http://anything/path"
    )
    print("Press Ctrl+C to stop.")

    try:
        async with server:
            await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nProxy stopped.")


if __name__ == "__main__":
    asyncio.run(main())
