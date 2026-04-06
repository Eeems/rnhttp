"""HTTP proxy server over RNS.

Forwards HTTP requests from RNS to the real internet.

Usage:
    python examples/proxy_server.py [--config] [--identity] [-v]
"""

import argparse
import asyncio
import logging
import os
from collections.abc import Generator
from http.client import (
    HTTPConnection,
    HTTPException,
)

import RNS

from rnhttp import HttpServer
from rnhttp._http import (
    RequestIO,
    Response,
)
from rnhttp._pipe import PipeIO

log = logging.getLogger(__name__)

HOP_BY_HOP = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "upgrade",
    ]
)


def proxy_handler(
    request: RequestIO, response: Response
) -> Generator[None, None, None]:
    """Forward HTTP requests to the real internet."""
    host = request.headers.get("host", [None])[0]
    if not host:
        response.status = 400
        response.body = b"Missing Host header"
        return

    path = request.url.path or "/"
    if request.url.query:
        path += "?" + request.url.query

    conn: HTTPConnection | None = None
    try:
        conn = HTTPConnection(host)
        conn.putrequest(request.method, path)
        for name, values in request.headers.items():
            if name not in HOP_BY_HOP and name != "host":
                conn.putheader(name, *values)
        conn.endheaders()

        while True:
            chunk = request.read(4096)
            if not chunk:
                break

            conn.send(chunk)

        resp = conn.getresponse()
        response.status = resp.status
        response.reason = resp.reason
        response.headers = {
            k.lower(): [v] for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP
        }
        with PipeIO() as pipe:
            response.body = pipe
            yield
            while True:
                data = resp.read(4096)
                if not data:
                    break

                _ = pipe.write(data)

    except TimeoutError:
        response.status = 504
        response.body = b"Gateway Timeout"

    except (ConnectionRefusedError, OSError, HTTPException) as e:
        log.error("Upstream error: %s", e)
        response.status = 502
        response.body = b"Bad Gateway"

    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP proxy server over RNS")
    _ = parser.add_argument("--config", help="RNS config directory")
    _ = parser.add_argument("--identity", help="Identity file path")
    _ = parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    assert isinstance(args.verbose, bool)  # pyright: ignore[reportAny]
    verbose: bool = args.verbose
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    assert isinstance(args.config, str | None)  # pyright: ignore[reportAny]
    config_path: str | None = args.config or os.environ.get("RNS_CONFIG_PATH")
    _ = RNS.Reticulum(config_path, RNS.LOG_VERBOSE if verbose else RNS.LOG_WARNING)

    assert isinstance(args.identity, str | None)  # pyright: ignore[reportAny]
    identity_path: str | None = args.identity
    server = HttpServer(port=80, identity_path=identity_path)
    server.set_default_handler(proxy_handler)

    async def loop() -> None:
        await server.start()

        print("HTTP proxy listening on RNS port 80")
        print(f"Destination: <{server.destination_hash}>")
        print("Press Ctrl+C to stop.")

        try:
            await asyncio.sleep(float("infinity"))

        except KeyboardInterrupt:
            pass

        finally:
            print("\nProxy server stopped.")

    asyncio.run(loop())
