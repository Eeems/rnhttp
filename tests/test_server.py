"""Tests for rnhttp server."""

import asyncio
import os
from unittest.mock import (
    MagicMock,
    patch,
)

from rnhttp._http import RequestIO, Response
from rnhttp.server import HttpServer, match_pattern


class TestHttpServer:
    """Tests for HttpServer class."""

    def test_default_values(self):
        """Test default initialization values."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            assert server._port == 8080  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._identity_path is not None  # pyright: ignore[reportPrivateUsage]
            assert server._request_timeout == 60.0  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._read_timeout == 30.0  # pyright: ignore[reportPrivateUsage]  # noqa: PLR2004
            assert server._destination is None  # pyright: ignore[reportPrivateUsage]
            assert server._running is False  # pyright: ignore[reportPrivateUsage]

    def test_custom_values(self):
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

    def test_default_identity_path(self):  # noqa: ANN201
        """Test default identity path generation."""
        path = HttpServer._default_identity_path()  # pyright: ignore[reportPrivateUsage]

        home = os.path.expanduser("~")
        expected = os.path.join(home, ".rnhttp", "identity")
        assert path == expected

    def test_port_property(self):
        """Test port property."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.port == 8080  # noqa: PLR2004

    def test_destination_hash_none_when_not_started(self):
        """Test destination_hash is None before server starts."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.destination_hash is None

    def test_is_running_false_when_not_started(self):
        """Test is_running is False before server starts."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.is_running is False


class TestHttpServerRoutes:
    """Tests for server routing."""

    def test_route_decorator(self):
        """Test route decorator registration."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            @server.route("/test")
            def handler(_: RequestIO, response: Response) -> None:
                response.status = 200

            assert ("GET", "/test") in server._handlers  # pyright: ignore[reportPrivateUsage]
            assert server._handlers[("GET", "/test")] == handler  # pyright: ignore[reportPrivateUsage]

    def test_route_multiple_paths(self):
        """Test registering multiple routes."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            def handler1(_: RequestIO, response: Response) -> None:
                response.status = 200

            def handler2(_: RequestIO, response: Response) -> None:
                response.status = 201

            server._handlers[("GET", "/path1")] = handler1  # pyright: ignore[reportPrivateUsage]
            server._handlers[("GET", "/path2")] = handler2  # pyright: ignore[reportPrivateUsage]

            assert len(server._handlers) == 2  # pyright: ignore[reportPrivateUsage]


class TestHttpServerMatchPattern:
    """Tests for pattern matching."""

    def test_exact_match(self):
        """Test exact path matching."""
        with patch("rnhttp.server.RNS"):
            assert match_pattern("/test", "/test") is True
            assert match_pattern("/test", "/other") is False

    def test_wildcard_match(self):
        """Test wildcard pattern matching."""
        with patch("rnhttp.server.RNS"):
            assert match_pattern("/api/*", "/api/users") is True
            assert match_pattern("/api/*", "/api/users/123") is True
            assert match_pattern("/api/*", "/other") is False


class TestHttpServerGetHandler:
    """Tests for handler lookup."""

    def test_get_handler_exact_match(self):
        """Test getting handler with exact path match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock()
            server._handlers[("GET", "/test")] = mock_handler  # pyright: ignore[reportPrivateUsage]

            request_io = RequestIO()
            _ = request_io.write(b"GET /test HTTP/1.1\r\nHost: example.com\r\n\r\n")
            _ = request_io.headers

            handler = server.get_handler(request_io)
            assert handler == mock_handler

    def test_get_handler_wildcard_match(self):
        """Test getting handler with wildcard match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock()
            server._handlers[("GET", "/api/*")] = mock_handler  # pyright: ignore[reportPrivateUsage]

            request_io = RequestIO()
            _ = request_io.write(
                b"GET /api/users HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            _ = request_io.headers

            handler = server.get_handler(request_io)
            assert handler == mock_handler

    def test_get_handler_not_found(self):
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


class TestHttpServerLifecycle:
    """Tests for server lifecycle."""

    @patch("rnhttp.server.RNS.Reticulum")
    @patch("rnhttp.server.RNS.Destination")
    @patch("rnhttp.server.RNS.Identity")
    @patch("rnhttp.server.os")
    def test_start(
        self,
        mock_os,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_identity,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_destination,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_reticulum,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType, reportUnusedParameter]
    ):
        """Test starting the server."""
        mock_identity_instance = MagicMock()
        mock_identity.return_value = mock_identity_instance
        mock_destination_instance = MagicMock()
        mock_destination_instance.hash = b"\x00" * 32
        mock_destination_instance.hexhash = "00" * 32
        mock_destination.return_value = mock_destination_instance
        mock_os.path.exists = MagicMock(return_value=False)  # pyright: ignore[reportUnknownMemberType]
        mock_os.makedirs = MagicMock()
        mock_os.path.dirname = MagicMock(return_value="/home/user/.rnhttp")  # pyright: ignore[reportUnknownMemberType]

        server = HttpServer(port=8080)

        asyncio.run(server.start())

        assert server._running is True  # pyright: ignore[reportPrivateUsage]
        mock_destination_instance.accepts_links.assert_called_once_with(True)  # pyright: ignore[reportAny]
        mock_destination_instance.set_link_established_callback.assert_called_once()  # pyright: ignore[reportAny]

    @patch("rnhttp.server.RNS.Reticulum")
    @patch("rnhttp.server.RNS.Destination")
    @patch("rnhttp.server.RNS.Identity")
    @patch("rnhttp.server.os")
    def test_stop(
        self,
        mock_os,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_identity,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_destination,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
        mock_reticulum,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType, reportUnusedParameter]
    ):
        """Test stopping the server."""
        mock_identity_instance = MagicMock()
        mock_identity.return_value = mock_identity_instance
        mock_destination_instance = MagicMock()
        mock_destination_instance.hash = b"\x00" * 32
        mock_destination_instance.hexhash = "00" * 32
        mock_destination.return_value = mock_destination_instance
        mock_os.path.exists = MagicMock(return_value=False)  # pyright: ignore[reportUnknownMemberType]
        mock_os.makedirs = MagicMock()
        mock_os.path.dirname = MagicMock(return_value="/home/user/.rnhttp")  # pyright: ignore[reportUnknownMemberType]

        server = HttpServer(port=8080)

        asyncio.run(server.start())
        asyncio.run(server.stop())

        assert server._running is False  # pyright: ignore[reportPrivateUsage]
        mock_destination_instance.accepts_links.assert_called_with(False)  # pyright: ignore[reportAny]
