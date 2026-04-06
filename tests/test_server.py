"""Tests for rnhttp server."""

import io
from typing import (
    TYPE_CHECKING,
    cast,
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


class TestHttpServerRoutes:
    """Tests for server routing."""

    def test_route_decorator(self) -> None:
        """Test route decorator registration."""
        server = HttpServer(port=8080)

        @server.route("/test")
        def handler(_: RequestIO, response: Response) -> None:
            response.status = 200

        assert ("GET", "/test") in server._handlers  # pyright: ignore[reportPrivateUsage]
        assert server._handlers[("GET", "/test")][0] == handler  # pyright: ignore[reportPrivateUsage]

    def test_route_multiple_paths(self) -> None:
        """Test registering multiple routes."""
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
        assert match_pattern("/test", "/test") is True
        assert match_pattern("/test", "/other") is False

    def test_param_match(self) -> None:
        """Test named parameter pattern matching."""
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
        server = HttpServer(port=8080)

        @server.route("/test", "GET")
        def fn(_request: RequestIO, _response: Response) -> None:
            pass

        with RequestIO() as request_io:
            _ = request_io.write(b"GET /test HTTP/1.1\r\nHost: example.com\r\n\r\n")
            result = server.get_handler(request_io)

        assert result is not None
        handler, _, _ = result
        assert handler == fn

    def test_get_handler_param_match(self) -> None:
        """Test getting handler with parameter match."""
        server = HttpServer(port=8080)

        @server.route("/users/{user:int}", "GET")
        def fn(_request: RequestIO, _response: Response, _user: int) -> None:
            pass

        with RequestIO() as request_io:
            _ = request_io.write(
                b"GET /users/123 HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            result = server.get_handler(request_io)

        assert result is not None
        handler, _, _ = result
        assert handler == fn

    def test_get_handler_not_found(self) -> None:
        """Test getting handler with no matching handler."""
        server = HttpServer(port=8080)

        with RequestIO() as request_io:
            _ = request_io.write(
                b"GET /nonexistent HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            handler = server.get_handler(request_io)

        assert handler is None

    def test_default_handler(self) -> None:
        """Test getting handler with no matching handler."""
        server = HttpServer(port=8080)

        def fn(_request: RequestIO, _response: Response) -> None:
            pass

        server.set_default_handler(fn)

        with RequestIO() as request_io:
            _ = request_io.write(
                b"GET /nonexistent HTTP/1.1\r\nHost: example.com\r\n\r\n"
            )
            result = server.get_handler(request_io)

        assert result is not None
        handler, _, _ = result
        assert handler == fn

    def test_handle_request_with_int_param(self) -> None:
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
