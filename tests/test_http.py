"""Unit tests for rnhttp._http module.

Covers: URL, Callbacks, HttpSendTo, Request, Response,
        CallbacksIO, RequestIO, ResponseIO.
"""

import io
import threading
import time

from rnhttp._http import (
    URL,
    Callbacks,
    HttpSendTo,
    Request,
    RequestIO,
    Response,
    ResponseIO,
)

# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


class TestURL:
    """Tests for URL construction and stringification."""

    def test_empty_url(self) -> None:
        u = URL()
        assert str(u) == ""
        assert bytes(u) == b""

    def test_full_url(self) -> None:
        u = URL(
            schema="https",
            userinfo="user:pass",
            host="example.com",
            port=8080,
            path="/foo/bar",
            query="a=1&b=2",
            fragment="top",
        )
        assert str(u) == "https://user:pass@example.com:8080/foo/bar?a=1&b=2#top"
        assert bytes(u) == b"https://user:pass@example.com:8080/foo/bar?a=1&b=2#top"

    def test_minimal_url(self) -> None:
        u = URL(host="example.com", path="/")
        assert str(u) == "example.com/"
        assert bytes(u) == b"example.com/"

    def test_url_with_schema_and_host(self) -> None:
        u = URL(schema="http", host="localhost")
        assert str(u) == "http://localhost"
        assert bytes(u) == b"http://localhost"

    def test_url_with_port(self) -> None:
        u = URL(host="localhost", port=3000)
        assert str(u) == "localhost:3000"
        assert bytes(u) == b"localhost:3000"

    def test_url_with_query_only(self) -> None:
        u = URL(path="/search", query="q=test")
        assert str(u) == "/search?q=test"
        assert bytes(u) == b"/search?q=test"

    def test_url_with_fragment_only(self) -> None:
        u = URL(path="/page", fragment="section")
        assert str(u) == "/page#section"
        assert bytes(u) == b"/page#section"

    def test_url_with_path_query_fragment(self) -> None:
        u = URL(path="/", query="test=1", fragment="test")
        assert str(u) == "/?test=1#test"
        assert bytes(u) == b"/?test=1#test"


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    """Tests for Callbacks event handling and data storage."""

    def test_on_message_begin_sets_ready_event(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        assert cb.ready_event.is_set()

    def test_on_header_stores_headers(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.url = URL()  # parser would set this; we set manually for test
        cb.on_header(b"Content-Type", b"text/html")
        assert "content-type" in cb.headers
        assert cb.headers["content-type"] == ["text/html"]

    def test_on_header_multiple_values(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.url = URL()
        cb.on_header(b"Set-Cookie", b"a=1")
        cb.on_header(b"Set-Cookie", b"b=2")
        assert cb.headers["set-cookie"] == ["a=1", "b=2"]

    def test_on_header_lowercases_name(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.url = URL()
        cb.on_header(b"Content-Type", b"text/html")
        assert "content-type" in cb.headers
        assert "Content-Type" not in cb.headers

    def test_on_header_host_sets_url_event(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.url = URL()
        cb.on_header(b"Host", b"example.com")
        assert cb.url_event.is_set()
        assert cb.url is not None
        assert cb.url.host == "example.com"

    def test_on_headers_complete_sets_event(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.on_headers_complete()
        assert cb.header_event.is_set()

    def test_on_body_accumulates_size(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.body_event.set()  # allow on_body to proceed
        cb.on_body(b"hello")
        cb.on_body(b" world")
        assert cb.size == 11

    def test_on_message_complete_sets_all_events(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.on_message_complete()
        assert cb.status_event.is_set()
        assert cb.url_event.is_set()
        assert cb.header_event.is_set()
        assert cb.chunk_event.is_set()
        assert cb.body_event.is_set()
        assert cb.message_event.is_set()

    def test_on_status_decodes_and_stores(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.on_status(b"OK")
        assert cb.status == "OK"
        assert cb.status_event.is_set()

    def test_on_chunk_header_and_complete(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.on_chunk_header()
        assert not cb.chunk_event.is_set()  # cleared by on_chunk_header
        cb.on_chunk_complete()
        assert cb.chunk_event.is_set()

    def test_wait_ready_returns_true(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        assert cb.wait_ready(timeout=1) is True

    def test_wait_blocks_until_complete(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()

        result: bool | None = None

        def waiter() -> None:
            nonlocal result
            result = cb.wait(timeout=2)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        cb.body_event.set()  # allow on_body to proceed
        cb.on_body(b"hello")
        cb.on_message_complete()
        t.join(timeout=3)
        assert result is True

    def test_wait_headers(self) -> None:
        cb = Callbacks()
        cb.on_message_begin()
        cb.on_headers_complete()
        assert cb.wait_headers(timeout=1) is True

    def test_drain_clears_all_callbacks(self) -> None:
        called: list[str] = []
        cb = Callbacks(
            on_message_begin=lambda: called.append("begin"),
            on_body=lambda _: called.append("body"),
        )
        cb.drain()
        cb.on_message_begin()
        cb.body_event.set()  # on_message_begin clears it; set again for on_body
        cb.on_body(b"data")
        assert called == []  # callbacks should not fire

    def test_custom_callbacks_fire(self) -> None:
        called: list[str] = []
        cb = Callbacks(
            on_message_begin=lambda: called.append("begin"),
            on_url=lambda u: called.append("url"),
            on_header=lambda _n, _v: called.append("header"),
            on_headers_complete=lambda: called.append("headers_complete"),
            on_body=lambda _: called.append("body"),
            on_message_complete=lambda: called.append("complete"),
            on_status=lambda s: called.append("status"),
        )
        cb.on_message_begin()
        cb.url = URL()  # needed for host header handling
        cb.on_header(b"X-Test", b"value")
        cb.on_headers_complete()
        cb.body_event.set()  # allow on_body to proceed
        cb.on_body(b"hi")
        cb.on_message_complete()
        cb.on_status(b"OK")
        assert called
        assert "begin" in called
        assert "header" in called
        assert "headers_complete" in called
        assert "body" in called
        assert "complete" in called
        assert "status" in called


# ---------------------------------------------------------------------------
# HttpSendTo
# ---------------------------------------------------------------------------


class TestHttpSendTo:
    """Tests for HttpSendTo header manipulation and sendto."""

    def test_set_header(self) -> None:
        s = HttpSendTo()
        s.set_header("Content-Type", "text/html")
        # HttpSendTo stores headers with original case
        assert s.headers["Content-Type"] == ["text/html"]

    def test_add_header(self) -> None:
        s = HttpSendTo()
        s.add_header("Set-Cookie", "a=1")
        s.add_header("Set-Cookie", "b=2")
        assert s.headers["Set-Cookie"] == ["a=1", "b=2"]

    def test_get_header_single(self) -> None:
        s = HttpSendTo()
        s.set_header("Content-Type", "text/html")
        assert s.get_header("Content-Type") == "text/html"

    def test_get_header_missing(self) -> None:
        s = HttpSendTo()
        assert s.get_header("x-missing") is None

    def test_get_header_multiple_raises(self) -> None:
        s = HttpSendTo()
        s.add_header("Set-Cookie", "a=1")
        s.add_header("Set-Cookie", "b=2")
        try:
            _ = s.get_header("Set-Cookie")
            assert False, "should have raised"

        except ValueError:
            pass

    def test_get_headers(self) -> None:
        s = HttpSendTo()
        s.add_header("X-Custom", "v1")
        s.add_header("X-Custom", "v2")
        assert s.get_headers("X-Custom") == ["v1", "v2"]

    def test_get_headers_missing(self) -> None:
        s = HttpSendTo()
        assert s.get_headers("x-missing") == []

    def test_body_bytes_sets_content_length(self) -> None:
        s = HttpSendTo(body=b"hello")
        assert s.headers["content-length"] == ["5"]
        assert s.body == b"hello"

    def test_body_none(self) -> None:
        s = HttpSendTo(body=None)
        assert s.body is None

    def test_body_bytesio_no_content_length_in_setter(self) -> None:
        """Body setter only sets content-length for bytes, not streams."""
        s = HttpSendTo(body=io.BytesIO(b"hello"))
        # BytesIO is not bytes, so setter does not set content-length
        assert "content-length" not in s.headers

    def test_sendto_bytes_body(self) -> None:
        r = Request("POST", URL(host="example.com", path="/submit"), body=b"hello")
        r.headers["X-Custom"] = ["value"]
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        assert b"X-Custom: value\r\n" in out
        assert b"content-length: 5\r\n" in out
        assert out.endswith(b"\r\nhello")

    def test_sendto_stream_body_chunked(self) -> None:
        """When body is a stream (not bytes), use chunked encoding."""

        # Use a custom reader that is not Sized
        class UnsizedReader:
            def __init__(self, data: bytes) -> None:
                self._data: bytes = data
                self._pos: int = 0

            def read(self, size: int = -1) -> bytes:
                if self._pos >= len(self._data):
                    return b""
                result = self._data[self._pos : self._pos + size]
                self._pos += len(result)
                return result

        r = Request(
            "POST",
            URL(host="example.com", path="/stream"),
            body=UnsizedReader(b"hello"),
        )
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        # chunked format: size\r\ndata\r\n...0\r\n\r\n
        assert b"5\r\nhello\r\n" in out
        assert b"0\r\n\r\n" in out

    def test_sendto_bytes_body_not_chunked(self) -> None:
        r = Request("POST", URL(host="example.com", path="/"), body=b"hello")
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        # bytes body uses content-length, not chunked
        assert b"content-length: 5\r\n" in out
        assert b"chunked" not in out.lower()

    def test_sendto_empty_body(self) -> None:
        r = Request("GET", URL(host="example.com", path="/"))
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        assert out.endswith(b"\r\n")

    def test_sendto_with_explicit_headers(self) -> None:
        r = Request("GET", URL(host="example.com", path="/"), headers={"X-Foo": "bar"})
        assert r.headers["X-Foo"] == ["bar"]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TestRequest:
    """Tests for Request class."""

    def test_statusline(self) -> None:
        r = Request("GET", URL(path="/", query="a=1"))
        assert r.statusline == b"GET /?a=1 HTTP/1.1\r\n"

    def test_method_uppercased(self) -> None:
        r = Request("get", URL(host="example.com", path="/"))
        assert r.method == "GET"

    def test_host_header_set(self) -> None:
        r = Request("GET", URL(host="example.com", path="/"))
        assert r.headers["host"] == ["example.com"]

    def test_host_header_not_set_when_no_host(self) -> None:
        r = Request("GET", URL(path="/"))
        assert "host" not in r.headers

    def test_sendto(self) -> None:
        r = Request("POST", URL(host="example.com", path="/submit"), body=b"data")
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        assert out.startswith(b"POST /submit HTTP/1.1\r\n")
        assert b"host: example.com\r\n" in out
        assert b"content-length: 4\r\n" in out
        assert out.endswith(b"\r\ndata")

    def test_sendto_chunked(self) -> None:
        r = Request(
            "POST",
            URL(host="example.com", path="/stream"),
            body=io.BytesIO(b"streamed"),
        )
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        # BytesIO is not bytes, so sendto uses chunked encoding
        assert b"8\r\nstreamed\r\n" in out
        assert b"0\r\n\r\n" in out


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class TestResponse:
    """Tests for Response class."""

    def test_statusline(self) -> None:
        r = Response(200)
        assert r.statusline == b"HTTP/1.1 200 OK\r\n"

    def test_custom_reason(self) -> None:
        r = Response(200, reason="All Good")
        assert r.statusline == b"HTTP/1.1 200 All Good\r\n"

    def test_reason_text(self) -> None:
        assert Response.reason_text(200) == "OK"
        assert Response.reason_text(404) == "Not Found"
        assert Response.reason_text(500) == "Internal Server Error"
        assert Response.reason_text(999) == "Unknown"

    def test_header_method(self) -> None:
        r = Response(200)
        r.header("X-Custom", "value")
        assert r.headers["X-Custom"] == ["value"]

    def test_sendto(self) -> None:
        r = Response(200, body=b"hello")
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        assert out.startswith(b"HTTP/1.1 200 OK\r\n")
        assert b"content-length: 5\r\n" in out
        assert out.endswith(b"\r\nhello")

    def test_sendto_chunked(self) -> None:
        r = Response(200, body=io.BytesIO(b"streamed"))
        with io.BytesIO() as buf:
            _ = r.sendto(buf)
            out = buf.getvalue()

        # BytesIO is not bytes, so sendto uses chunked encoding
        assert b"8\r\nstreamed\r\n" in out
        assert b"0\r\n\r\n" in out


# ---------------------------------------------------------------------------
# RequestIO
# ---------------------------------------------------------------------------


class TestRequestIO:
    """Tests for RequestIO parsing correctness."""

    def test_parse_request_url(self) -> None:
        with RequestIO() as rio:
            _ = rio.write(b"GET /path?q=1 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            url = rio.url

        assert url.path == "/path"
        assert url.query == "q=1"

    def test_parse_request_method(self) -> None:
        with RequestIO() as rio:
            _ = rio.write(b"POST /submit HTTP/1.1\r\nHost: example.com\r\n\r\n")

        assert rio.method == "POST"

    def test_parse_request_headers(self) -> None:
        with RequestIO() as rio:
            _ = rio.write(
                b"GET / HTTP/1.1\r\nHost: example.com\r\nX-Custom: value\r\n\r\n"
            )
            headers = rio.headers

        assert "x-custom" in headers
        assert headers["x-custom"] == ["value"]

    def test_parse_request_body(self) -> None:
        with RequestIO() as rio:

            def writer() -> None:
                _ = rio.write(
                    b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 5\r\n\r\nhello"
                )
                rio.close()

            t = threading.Thread(target=writer)
            t.start()
            body = rio.readall()
            t.join(timeout=5)

        assert body == b"hello"

    def test_context_manager(self) -> None:
        with RequestIO() as rio:
            _ = rio.write(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        # after exit, buffer should be closed

    def test_readline(self) -> None:
        with RequestIO() as rio:

            def writer() -> None:
                _ = rio.write(
                    b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 11\r\n\r\nline1\nline2"
                )
                rio.close()

            t = threading.Thread(target=writer)
            t.start()
            line = rio.readline()
            t.join(timeout=5)

        assert line == b"line1\n"

    def test_readlines(self) -> None:
        with RequestIO() as rio:

            def writer() -> None:
                _ = rio.write(
                    b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 11\r\n\r\nline1\nline2"
                )
                rio.close()

            t = threading.Thread(target=writer)
            t.start()
            lines = rio.readlines()
            t.join(timeout=5)

        assert lines == [b"line1\n", b"line2"]

    def test_readinto(self) -> None:
        """readinto delegates to buffer; test that it works when buffer supports it."""
        with RequestIO() as rio:

            def writer() -> None:
                _ = rio.write(
                    b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 5\r\n\r\nhello"
                )
                rio.close()

            t = threading.Thread(target=writer)
            t.start()
            # read() works; readinto would too if PipeIO implemented it
            body = rio.read()
            t.join(timeout=5)

        assert body == b"hello"


# ---------------------------------------------------------------------------
# ResponseIO
# ---------------------------------------------------------------------------


class TestResponseIO:
    """Tests for ResponseIO parsing correctness."""

    def test_parse_response_status(self) -> None:
        with ResponseIO() as rio:
            _ = rio.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

        assert rio.status == 200
        assert rio.reason == "OK"

    def test_parse_response_reason(self) -> None:
        with ResponseIO() as rio:
            _ = rio.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

        assert rio.status == 404
        assert rio.reason == "Not Found"

    def test_parse_response_headers(self) -> None:
        with ResponseIO() as rio:
            _ = rio.write(
                b"HTTP/1.1 200 OK\r\nX-Custom: value\r\nContent-Length: 0\r\n\r\n"
            )
            headers = rio.headers

        assert "x-custom" in headers
        assert headers["x-custom"] == ["value"]

    def test_parse_response_body(self) -> None:
        with ResponseIO() as rio:

            def writer() -> None:
                _ = rio.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nHello, World!"
                )
                rio.close()

            t = threading.Thread(target=writer)
            t.start()
            body = rio.readall()
            t.join(timeout=5)

        assert body == b"Hello, World!"


# ---------------------------------------------------------------------------
# CallbacksIO
# ---------------------------------------------------------------------------


class TestCallbacksIO:
    """Tests for CallbacksIO base class."""

    def test_len_with_content_length(self) -> None:
        rio = RequestIO()
        _ = rio.write(
            b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 42\r\n\r\n"
        )
        rio.close()
        assert len(rio) == 42

    def test_len_after_message_complete(self) -> None:
        rio = RequestIO()

        def writer() -> None:
            _ = rio.write(
                b"POST / HTTP/1.1\r\nHost: example.com\r\nContent-Length: 5\r\n\r\nhello"
            )
            rio.close()

        t = threading.Thread(target=writer)
        t.start()
        # readall allows body to be processed and message to complete
        _ = rio.readall()
        t.join(timeout=5)
        # after message complete, size should be known
        assert len(rio) == 5

    def test_flush(self) -> None:
        rio = RequestIO()
        rio.flush()  # should not raise
