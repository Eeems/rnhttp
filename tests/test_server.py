"""Tests for rnhttp server."""

import io
import os
from typing import (
    TYPE_CHECKING,
    cast,
)
from unittest.mock import (
    MagicMock,
    patch,
)

if TYPE_CHECKING:
    pass

from rnhttp._http import (
    RequestIO,
    Response,
)
from rnhttp.server import (
    HttpServer,
    match_pattern,
)


class TestHttpServer:
    """Tests for HttpServer class."""

    def test_default_values(self) -> None:
        """Test default initialization values."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            assert server._port == 8080  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._identity_path is not None  # pyright: ignore[reportPrivateUsage]
            assert server._request_timeout == 60.0  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._read_timeout == 30.0  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._destination is None  # pyright: ignore[reportPrivateUsage]
            assert server._running is False  # pyright: ignore[reportPrivateUsage]

    def test_custom_values(self) -> None:
        """Test custom initialization values."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(
                port=9000,
                identity_path="/custom/path",
                request_timeout=20.0,
                read_timeout=15.0,
            )

            assert server._port == 9000  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._identity_path == "/custom/path"  # pyright: ignore[reportPrivateUsage]
            assert server._request_timeout == 20.0  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._read_timeout == 15.0  # pyright: ignore[reportPrivateUsage]

    def test_default_identity_path(self) -> None:
        """Test default identity path generation."""
        path = HttpServer._default_identity_path()  # pyright: ignore[reportPrivateUsage]

        home = os.path.expanduser("~")
        expected = os.path.join(home, ".rnhttp", "identity")
        assert path == expected

    def test_port_property(self) -> None:
        """Test port property."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.port == 8080  # noqa: PLR2004

    def test_destination_hash_none_when_not_started(self) -> None:
        """Test destination_hash is None before server starts."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.destination_hash is None


class TestHttpServerRoutes:
    """Tests for server routing."""

    def test_route_decorator(self) -> None:
        """Test route decorator registration."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            @server.route("/test")
            def handler(_: RequestIO, response: Response) -> None:
                response.status = 200

            assert ("GET", "/test") in server._handlers  # pyright: ignore[reportPrivateUsage]
            assert server._handlers[("GET", "/test")][0] == handler  # pyright: ignore[reportPrivateUsage]

    def test_route_multiple_paths(self) -> None:
        """Test registering multiple routes."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            @server.route("/path1")
            def handler1(_request: RequestIO, _response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
                pass

            @server.route("/path1")  # this handler overwrites the prevous one
            def handler2(_request: RequestIO, _response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
                pass

            @server.route("/path2")
            def handler3(_request: RequestIO, _response: Response) -> None:  # pyright: ignore[reportUnusedFunction]
                pass

            assert len(server._handlers) == 2  # pyright: ignore[reportPrivateUsage]


class TestHttpServerMatchPattern:
    """Tests for pattern matching."""

    def test_exact_match(self) -> None:
        """Test exact path matching."""
        with patch("rnhttp.server.RNS"):
            assert match_pattern("/test", "/test") is True
            assert match_pattern("/test", "/other") is False

    def test_param_match(self) -> None:
        """Test named parameter pattern matching."""
        with patch("rnhttp.server.RNS"):
            # match_pattern only checks structure, not type validity
            assert match_pattern("/users/{id:int}", "/users/123") is True
            assert (
                match_pattern("/users/{id:int}", "/users/abc") is True
            )  # structure matches
            assert match_pattern("/users/{id:int}", "/posts/123") is False
            assert match_pattern("/users/{name:str}", "/users/john") is True


class TestHttpServerGetHandler:
    """Tests for handler lookup."""

    def test_get_handler_exact_match(self) -> None:
        """Test getting handler with exact path match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock()
            server._handlers[("GET", "/test")] = (mock_handler, [])  # pyright: ignore[reportPrivateUsage]

            request_io = RequestIO()
            _ = request_io.write(b"GET /test HTTP/1.1\r\nHost: example.com\r\n\r\n")
            _ = request_io.headers

            result = server.get_handler(request_io)
            assert result is not None
            handler, _, _ = result
            assert handler == mock_handler

    def test_get_handler_param_match(self) -> None:
        """Test getting handler with parameter match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock()
            server._handlers[("GET", "/users/{id:int}")] = (mock_handler, [("id", int)])  # pyright: ignore[reportPrivateUsage]

            request_io = RequestIO()
            _ = request_io.write(
                b"GET /users/123 HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            _ = request_io.headers

            result = server.get_handler(request_io)
            assert result is not None
            handler, _, _ = result
            assert handler == mock_handler

    def test_get_handler_not_found(self) -> None:
        """Test getting handler with no matching handler."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            request_io = RequestIO()
            _ = request_io.write(
                b"GET /nonexistent HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            _ = request_io.headers

            handler = server.get_handler(request_io)
            assert handler is None

    def test_handle_request_with_int_param(self):
        """handle_request passes int param to handler correctly."""
        server = HttpServer(port=8080)
        user_id: int | None = None

        @server.route("/users/{user:int}")
        def _handler(_request: RequestIO, response: Response, user: int) -> None:  # pyright: ignore[reportUnusedFunction]
            nonlocal user_id
            user_id = user
            response.status = 200

        with RequestIO() as request_io, io.BytesIO() as writer:
            _ = request_io.write(
                b"GET /users/123 HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            server.handle_request(
                None,  # pyright: ignore[reportArgumentType]
                request_io,
                cast(io.BufferedWriter, writer),  # pyright: ignore[reportInvalidCast]
            )

        assert user_id == 123
