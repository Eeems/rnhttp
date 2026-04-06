"""Integration tests for rnhttp client/server over Reticulum."""

import os
import random
import re
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest


class SetupError(RuntimeError):
    pass


def _get_free_port() -> int:
    """Ask OS for a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])  # pyright: ignore[reportAny]


def randomword(length: int) -> str:
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for _ in range(length))  # noqa: S311


def _drain_subprocess_output(proc: subprocess.Popen[str], prefix: str) -> None:
    """Drain stdout/stderr from a subprocess, printing with a prefix."""
    while proc.poll() is None:
        for f in (proc.stdout, proc.stderr):
            if f is None:
                continue
            line = f.readline()
            if line:
                print(f"{prefix}: {line}", file=sys.stderr, end="")


RETICULUM_CONFIG = f"""
[reticulum]
  instance_name = rns_http{randomword(5)}

[interfaces]
  [[AutoInterface]]
    type = AutoInterface
    enabled = no

  [[Dummy]]
    type = BackboneInterface
    enable = yes
    listen_on = 127.0.0.2
"""

_rnsd_process: subprocess.Popen[bytes] | None = None
_rnsd_config_dir: Path | None = None


@pytest.fixture(scope="session", autouse=True)
def shared_rnsd() -> Generator[Path, Any, None]:  # pyright: ignore[reportExplicitAny]
    global _rnsd_process
    global _rnsd_config_dir
    with tempfile.TemporaryDirectory() as config_dir:
        rns_config = os.path.join(config_dir, "config")
        with open(rns_config, "w") as f:
            _ = f.write(RETICULUM_CONFIG)

        tries = 3
        timeout = 5
        start = time.time()
        rnsd_proc = None
        remaining = tries
        while True:
            if rnsd_proc is None:
                rnsd_proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "RNS.Utilities.rnsd",
                        "--config",
                        str(config_dir),
                        "-vvv",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )

            if rnsd_proc.poll() is not None:
                stdout = (
                    rnsd_proc.stdout.read().decode()
                    if rnsd_proc.stdout is not None
                    else ""
                )
                raise SetupError(
                    f"RNS shared instance exited early: {rnsd_proc.returncode}"
                    + f"\n  stdout: {stdout}"
                )

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "RNS.Utilities.rnstatus",
                    "--config",
                    str(config_dir),
                    "-a",
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if not proc.returncode:
                break

            if time.time() - start < timeout:
                continue

            rnsd_proc.terminate()
            try:
                _ = rnsd_proc.wait(timeout=5)

            except subprocess.TimeoutExpired:
                rnsd_proc.kill()
                _ = rnsd_proc.wait()

            if remaining:
                rnsd_proc = None
                remaining -= 1
                start = time.time()
                continue

            stdout = (
                rnsd_proc.stdout.read().decode() if rnsd_proc.stdout is not None else ""
            )
            raise SetupError(
                f"RNS shared instance failed to start in {tries} tries..."
                + f"\n  stdout: {stdout}"
                + f"\n  rnstatus: {proc.returncode} {proc.stdout or ''}"
            )

        threading.Thread(
            target=_drain_subprocess_output, args=(rnsd_proc, "rnsd"), daemon=True
        ).start()

        _rnsd_process = rnsd_proc
        _rnsd_config_dir = Path(config_dir)

        yield _rnsd_config_dir

        rnsd_proc.terminate()
        try:
            _ = rnsd_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            rnsd_proc.kill()
            _ = rnsd_proc.wait()


class HttpIntegrationStack:
    def __init__(self, rns_config: Path) -> None:
        self.rns_config: Path = rns_config
        self.server_proc: subprocess.Popen[str] | None = None
        self.proxy_proc: subprocess.Popen[str] | None = None
        self.server_hash: str | None = None
        self.server_port: int = 8080

    def start_server(self, port: int = 8080) -> None:
        if self.server_proc is not None and self.server_proc.poll() is None:
            return

        self.server_port = port
        self.server_proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "examples/basic_server.py",
                str(port),
                "--config",
                str(self.rns_config),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "RNS_CONFIG_PATH": str(self.rns_config)},
        )

        dest_hash = None
        assert self.server_proc.stdout is not None
        while True:
            line = self.server_proc.stdout.readline()
            if not line:
                print("Exiting thread due to empty readline", file=sys.stderr)
                break

            if self.server_proc.poll() is not None:
                print("Exiting thread due to application stopping", file=sys.stderr)
                break

            print(f"SERVER: {line.rstrip()}", file=sys.stderr)
            match = re.search(r"Destination: <([a-f0-9]+)>", line)
            if match:
                print("Exiting thread due to line match", file=sys.stderr)
                dest_hash = match.group(1)
                break

        if self.server_proc.poll() is not None:
            raise SetupError(
                f"server exited early with code {self.server_proc.returncode}"
            )

        assert dest_hash is not None, "Could not get destination hash from server"
        self.server_hash = dest_hash

        while subprocess.run(
            [
                sys.executable,
                "-m",
                "RNS.Utilities.rnpath",
                "--config",
                str(self.rns_config),
                "-w1",
                dest_hash,
            ],
            check=False,
        ).returncode:
            if self.server_proc.poll() is not None:
                raise SetupError("Server exited early")

        threading.Thread(
            target=_drain_subprocess_output,
            args=(self.server_proc, "SERVER"),
            daemon=True,
        ).start()

    def start_proxy_server(self) -> None:
        """Start the proxy server (runs both HTTP and HTTPS handlers)."""
        if self.proxy_proc is not None and self.proxy_proc.poll() is None:
            return

        self.proxy_proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "examples/proxy_server.py",
                "--config",
                str(self.rns_config),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "RNS_CONFIG_PATH": str(self.rns_config)},
        )

        dest_hash = None
        assert self.proxy_proc.stdout is not None
        while True:
            line = self.proxy_proc.stdout.readline()
            if not line:
                print("Exiting thread due to empty readline", file=sys.stderr)
                break

            if self.proxy_proc.poll() is not None:
                print("Exiting thread due to application stopping", file=sys.stderr)
                break

            print(f"PROXY_SERVER: {line.rstrip()}", file=sys.stderr)
            match = re.search(r"Destination: <([a-f0-9]+)>", line)
            if match:
                dest_hash = match.group(1)
                break

        if self.proxy_proc.poll() is not None:
            raise SetupError(
                f"proxy_server exited early with code {self.proxy_proc.returncode}"
            )

        assert dest_hash is not None, "Could not get destination hash from proxy server"
        self.server_hash = dest_hash

        threading.Thread(
            target=_drain_subprocess_output,
            args=(self.proxy_proc, "PROXY_SERVER"),
            daemon=True,
        ).start()

    def start_socks_proxy(self, socks_port: int = 1080) -> subprocess.Popen[str]:
        """Start the SOCKS5 proxy pointing at the server."""
        assert self.server_hash is not None
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "examples/socks_proxy.py",
                self.server_hash,
                f"--listen=127.0.0.1:{socks_port}",
                "--config",
                str(self.rns_config),
                "-v",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "RNS_CONFIG_PATH": str(self.rns_config)},
        )

        # Wait for proxy to start listening
        assert proc.stdout is not None
        started = False
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise SetupError(
                        f"socks_proxy exited early with code {proc.returncode}"
                    )
                continue

            print(f"SOCKS_PROXY: {line.rstrip()}", file=sys.stderr)
            if "SOCKS5 proxy listening on" in line:
                started = True
                break

        if not started:
            proc.terminate()
            try:
                _ = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _ = proc.wait()
            raise SetupError("SOCKS proxy did not start")

        # Start a thread to drain stdout so it doesn't block
        threading.Thread(
            target=_drain_subprocess_output, args=(proc, "SOCKS_PROXY"), daemon=True
        ).start()

        return proc

    def run_client(
        self,
        path: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        response_code: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert self.server_hash is not None
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-um",
                    "rnhttp.client",
                    *(["--response-code"] if response_code else []),
                    f"--config={self.rns_config}",
                    self.server_hash,
                    str(self.server_port),
                    method,
                    path,
                    *[f"--header={k}: {v}" for k, v in (headers or {}).items()],
                    *(
                        ["--body", body.decode("utf-8", errors="replace")]
                        if body
                        else []
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            print(f"CLIENT STDOUT: {result.stdout}")
            print(f"CLIENT STDERR: {result.stderr}")
            return result
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"CLIENT STDOUT: {e.stdout.decode() if e.stdout else ''}")
            print(f"CLIENT STDERR: {e.stderr.decode() if e.stderr else ''}")
            raise

    def cleanup(self) -> None:
        if self.server_proc:
            self.server_proc.terminate()
            try:
                _ = self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                _ = self.server_proc.wait()
            if self.server_proc.stdout is not None:
                print(self.server_proc.stdout.read())

        if self.proxy_proc:
            self.proxy_proc.terminate()
            try:
                _ = self.proxy_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proxy_proc.kill()
                _ = self.proxy_proc.wait()
            if self.proxy_proc.stdout is not None:
                print(self.proxy_proc.stdout.read())


class TestHttpIntegration:
    def test_server_starts(self) -> None:
        """Test that server starts and announces destination."""
        # RFC 9112, 3 (server announces capability)
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            assert stack.server_hash is not None
            assert len(stack.server_hash) == 32

        finally:
            stack.cleanup()

    def test_get_root(self) -> None:
        """Test GET request to root path."""
        # RFC 9110, 9.3.1 GET
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client("/", "GET", response_code=True)
            assert result.returncode == 0, "Request failed"
            assert result.stdout == "200", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_post_with_body(self) -> None:
        """Test POST request with body."""
        # RFC 9110, 9.3.3 POST
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client(
                "/resource",
                "POST",
                body=b"hello",
                response_code=True,
            )
            assert result.returncode == 0, "Request failed"
            assert result.stdout == "200", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_404_not_found(self) -> None:
        """Test 404 response for non-existent path."""
        # RFC 9110, 15.5.15 404 Not Found
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client("/nonexistent", "GET", response_code=True)
            assert result.returncode == 1, "Request succeeded"
            assert result.stdout == "404", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_put_request(self) -> None:
        """Test PUT request."""
        # RFC 9110, 9.3.6 PUT
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client(
                "/resource",
                "PUT",
                body=b"data",
                response_code=True,
            )
            assert result.returncode == 0, "Request failed"
            assert result.stdout == "200", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_delete_request(self) -> None:
        """Test DELETE request."""
        # RFC 9110, 9.3.7 DELETE
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client("/resource", "DELETE", response_code=True)
            assert result.returncode == 0, "Request failed"
            assert result.stdout == "200", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_custom_headers(self) -> None:
        """Test request with custom headers."""
        # RFC 9112, 3.2 Header Fields
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client(
                "/",
                "GET",
                headers={"User-Agent": "TestClient/1.0"},
                response_code=True,
            )
            assert result.returncode == 0, "Request failed"
            assert result.stdout == "200", "Incorrect return code"

        finally:
            stack.cleanup()

    def test_response_headers_in_output(self) -> None:
        """Test that response headers are included in client output."""
        # RFC 9112, 3.2 Header Fields
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        try:
            stack.start_server()
            result = stack.run_client("/", "GET")
            assert result.returncode == 0, "Request failed"
            assert "HTTP/" in result.stdout

        finally:
            stack.cleanup()

    def test_socks_proxy_via_curl(self) -> None:
        """Test SOCKS5 proxy using curl as client."""
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        socks_proc: subprocess.Popen[str] | None = None
        try:
            # Start basic_server on port 80 to match SOCKS CONNECT port
            stack.start_server(port=80)
            socks_port = _get_free_port()
            socks_proc = stack.start_socks_proxy(socks_port)

            # First request
            result = subprocess.run(
                [
                    "curl",
                    "--socks5-hostname",
                    f"127.0.0.1:{socks_port}",
                    "--max-time",
                    "30",
                    "-v",
                    "-w",
                    "\n%{http_code}",
                    "http://localhost/",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            print(f"CURL STDOUT (1): {result.stdout}", file=sys.stderr)
            print(f"CURL STDERR (1): {result.stderr}", file=sys.stderr)
            assert result.returncode == 0, f"curl failed: {result.stderr}"
            assert "200" in result.stdout, f"Expected 200 in output: {result.stdout}"
            assert "Hello from RNS HTTP Server!" in result.stdout, (
                f"Expected body in output: {result.stdout}"
            )

            # Second request - verify proxy handles multiple connections
            result = subprocess.run(
                [
                    "curl",
                    "--socks5-hostname",
                    f"127.0.0.1:{socks_port}",
                    "--max-time",
                    "30",
                    "-v",
                    "-w",
                    "\n%{http_code}",
                    "http://localhost/hello",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            print(f"CURL STDOUT (2): {result.stdout}", file=sys.stderr)
            print(f"CURL STDERR (2): {result.stderr}", file=sys.stderr)
            assert result.returncode == 0, f"curl failed: {result.stderr}"
            assert "200" in result.stdout, f"Expected 200 in output: {result.stdout}"
            assert "Hello, World!" in result.stdout, (
                f"Expected body in output: {result.stdout}"
            )

        finally:
            if socks_proc is not None:
                socks_proc.terminate()
                try:
                    _ = socks_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    socks_proc.kill()
                    _ = socks_proc.wait()
            stack.cleanup()

    def test_proxy_server_http(self) -> None:
        """Test proxy server with HTTP requests."""
        if not _rnsd_config_dir:
            raise SetupError("RNS not available")

        stack = HttpIntegrationStack(_rnsd_config_dir)
        socks_proc: subprocess.Popen[str] | None = None
        try:
            # Start proxy server (handles both HTTP and HTTPS)
            stack.start_proxy_server()
            socks_port = _get_free_port()
            socks_proc = stack.start_socks_proxy(socks_port)

            # Test HTTP via proxy
            result = subprocess.run(
                [
                    "curl",
                    "--socks5-hostname",
                    f"127.0.0.1:{socks_port}",
                    "--max-time",
                    "30",
                    "-v",
                    "-A",
                    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
                    "-w",
                    "\n%{http_code}",
                    "http://frogfind.com/",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            print(f"CURL HTTP STDOUT: {result.stdout}", file=sys.stderr)
            print(f"CURL HTTP STDERR: {result.stderr}", file=sys.stderr)
            assert result.returncode == 0, f"HTTP curl failed: {result.stderr}"
            assert "200" in result.stdout, (
                f"Expected 200 in HTTP response: {result.stdout}"
            )
            # Verify we actually got body content, not just headers
            body = result.stdout.rsplit("\n", 1)[0]  # Remove status code line
            assert len(body) > 100, f"Expected substantial body content, got: {body!r}"

        finally:
            if socks_proc is not None:
                socks_proc.terminate()
                try:
                    _ = socks_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    socks_proc.kill()
                    _ = socks_proc.wait()
            stack.cleanup()
