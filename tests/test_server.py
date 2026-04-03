"""Tests for rnhttp server."""

import asyncio
import os
from unittest.mock import (
    MagicMock,
    patch,
)

from rnhttp.server import HttpServer
from rnhttp.types import (
    HttpRequest,
    HttpResponse,
)


class TestHttpServer:
    """Tests for HttpServer class."""

    def test_default_values(self):
        """Test default initialization values."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            assert server._port == 8080
            assert server._identity_path is not None
            assert server._request_timeout == 60.0
            assert server._read_timeout == 30.0
            assert server._destination is None
            assert server._running is False
            assert server._links == {}

    def test_custom_values(self):
        """Test custom initialization values."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(
                port=9000,
                identity_path="/custom/path",
                request_timeout=20.0,
                read_timeout=15.0,
            )

            assert server._port == 9000
            assert server._identity_path == "/custom/path"
            assert server._request_timeout == 20.0
            assert server._read_timeout == 15.0

    def test_default_identity_path(self):
        """Test default identity path generation."""
        path = HttpServer._default_identity_path()

        home = os.path.expanduser("~")
        expected = os.path.join(home, ".rnhttp", "identity")
        assert path == expected

    def test_port_property(self):
        """Test port property."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            assert server.port == 8080

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
            def handler(request: HttpRequest) -> HttpResponse:
                return HttpResponse(status=200, reason="OK")

            assert ("GET", "/test") in server._handlers
            assert server._handlers[("GET", "/test")] == handler

    def test_route_multiple_paths(self):
        """Test registering multiple routes."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            def handler1(request: HttpRequest) -> HttpResponse:
                return HttpResponse(status=200, reason="OK")

            def handler2(request: HttpRequest) -> HttpResponse:
                return HttpResponse(status=201, reason="Created")

            server._handlers["/path1"] = handler1
            server._handlers["/path2"] = handler2

            assert len(server._handlers) == 2


class TestHttpServerMatchPattern:
    """Tests for pattern matching."""

    def test_exact_match(self):
        """Test exact path matching."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            assert server._match_pattern("/test", "/test") is True
            assert server._match_pattern("/test", "/other") is False

    def test_wildcard_match(self):
        """Test wildcard pattern matching."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            assert server._match_pattern("/api/*", "/api/users") is True
            assert server._match_pattern("/api/*", "/api/users/123") is True
            assert server._match_pattern("/api/*", "/other") is False


class TestHttpServerHandleRequest:
    """Tests for request handling."""

    def test_handle_request_exact_match(self):
        """Test handling request with exact path match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock(return_value=HttpResponse(status=200, reason="OK"))
            server._handlers[("GET", "/test")] = mock_handler

            request = HttpRequest(method="GET", path="/test")
            response = server._handle_request(None, request)  # pyright: ignore[reportArgumentType]

            mock_handler.assert_called_once_with(request)
            assert response.status == 200

    def test_handle_request_wildcard_match(self):
        """Test handling request with wildcard match."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)
            mock_handler = MagicMock(return_value=HttpResponse(status=200, reason="OK"))
            server._handlers[("GET", "/api/*")] = mock_handler

            request = HttpRequest(method="GET", path="/api/users")
            _ = server._handle_request(None, request)  # pyright: ignore[reportArgumentType]

            mock_handler.assert_called_once_with(request)

    def test_handle_request_not_found(self):
        """Test handling request with no matching handler."""
        with patch("rnhttp.server.RNS"):
            server = HttpServer(port=8080)

            request = HttpRequest(method="GET", path="/nonexistent")
            response = server._handle_request(None, request)  # pyright: ignore[reportArgumentType]

            assert response.status == 404
            assert response.body == b"Not Found"


class TestHttpServerLifecycle:
    """Tests for server lifecycle."""

    @patch("rnhttp.server.RNS.Reticulum")
    @patch("rnhttp.server.RNS.Destination")
    @patch("rnhttp.server.RNS.Identity")
    @patch("rnhttp.server.os")
    def test_start(
        self,
        mock_os,
        mock_identity,
        mock_destination,
        mock_reticulum,
    ):
        """Test starting the server."""
        mock_identity_instance = MagicMock()
        mock_identity.return_value = mock_identity_instance
        mock_destination_instance = MagicMock()
        mock_destination_instance.hash = b"\x00" * 32
        mock_destination_instance.hexhash = "00" * 32
        mock_destination.return_value = mock_destination_instance
        mock_os.path.exists = MagicMock(return_value=False)
        mock_os.makedirs = MagicMock()
        mock_os.path.dirname = MagicMock(return_value="/home/user/.rnhttp")

        server = HttpServer(port=8080)

        asyncio.run(server.start())

        assert server._running is True
        mock_destination_instance.accepts_links.assert_called_once_with(True)
        mock_destination_instance.set_link_established_callback.assert_called_once()

    @patch("rnhttp.server.RNS.Reticulum")
    @patch("rnhttp.server.RNS.Destination")
    @patch("rnhttp.server.RNS.Identity")
    @patch("rnhttp.server.os")
    def test_stop(
        self,
        mock_os,
        mock_identity,
        mock_destination,
        mock_reticulum,
    ):
        """Test stopping the server."""
        mock_identity_instance = MagicMock()
        mock_identity.return_value = mock_identity_instance
        mock_destination_instance = MagicMock()
        mock_destination_instance.hash = b"\x00" * 32
        mock_destination_instance.hexhash = "00" * 32
        mock_destination.return_value = mock_destination_instance
        mock_os.path.exists = MagicMock(return_value=False)
        mock_os.makedirs = MagicMock()
        mock_os.path.dirname = MagicMock(return_value="/home/user/.rnhttp")

        server = HttpServer(port=8080)

        asyncio.run(server.start())
        asyncio.run(server.stop())

        assert server._running is False
        mock_destination_instance.accepts_links.assert_called_with(False)
