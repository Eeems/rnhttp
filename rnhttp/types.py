"""HTTP/1.1 protocol parser and serializer.

Based on RFC 9112 - HTTP/1.1 Message Syntax and Routing (June 2022)
Uses httptools for parsing."""

from typing import (
    final,
    override,
)

from httptools import (
    HttpParserError as HttptoolsParserError,
)
from httptools import (
    HttpRequestParser,
    HttpResponseParser,
)


class HttpParserError(Exception):
    """Exception raised for HTTP parsing errors."""

    pass


class HttpSerializerError(Exception):
    """Exception raised for HTTP serialization errors."""

    pass


class RequestCallbacks:
    """Callback handler for request parsing."""

    def __init__(self) -> None:
        self.method: bytes = b""
        self.url: bytes = b""
        self.version: bytes = b"HTTP/1.1"
        self.headers: dict[bytes, bytes] = {}
        self.body_chunks: list[bytes] = []

    def on_message_begin(self) -> None:
        pass

    def on_method(self, method: bytes) -> None:
        self.method = method

    def on_url(self, url: bytes) -> None:
        self.url = url

    def on_version(self, version: bytes) -> None:
        self.version = version

    def on_header(self, name: bytes, value: bytes) -> None:
        self.headers[name] = value

    def on_body(self, body: bytes) -> None:
        self.body_chunks.append(body)


class ResponseCallbacks:
    """Callback handler for response parsing."""

    def __init__(self) -> None:
        self.version: bytes = b"HTTP/1.1"
        self.status: int = 0
        self.reason: bytes = b""
        self.headers: dict[bytes, bytes] = {}
        self.body_chunks: list[bytes] = []

    def on_message_begin(self) -> None:
        pass

    def on_version(self, version: bytes) -> None:
        self.version = version

    def on_status_code(self, status_code: bytes) -> None:
        self.status = int(status_code)

    def on_reason_phrase(self, reason: bytes) -> None:
        self.reason = reason

    def on_header(self, name: bytes, value: bytes) -> None:
        self.headers[name] = value

    def on_body(self, body: bytes) -> None:
        self.body_chunks.append(body)


def encode_chunked(body: bytes) -> bytes:
    """Encode body using chunked transfer encoding.

    Args:
        body: Body bytes to encode

    Returns:
        Chunked encoded body bytes
    """
    offset = 0
    data: bytes = b""
    while offset < len(body):
        chunk = body[offset : offset + 4096]
        data += f"{len(chunk):x}".encode() + b"\r\n" + chunk + b"\r\n"
        offset += len(chunk)

    return data + b"0" + b"\r\n\r\n"


@final
class HttpRequest:
    """Represents an HTTP/1.1 request."""

    __slots__ = ("method", "path", "version", "headers", "body")

    def __init__(
        self,
        method: str,
        path: str,
        version: str = "HTTP/1.1",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers if headers is not None else {}
        self.body = body

    @staticmethod
    def parse(data: bytes) -> "HttpRequest":
        if not data:
            raise HttpParserError("Incomplete request")

        callbacks = RequestCallbacks()
        parser = HttpRequestParser(callbacks)

        try:
            parser.feed_data(data)  # pyright: ignore[reportUnknownMemberType]

        except HttptoolsParserError as e:
            raise HttpParserError(str(e)) from e

        method = parser.get_method()
        if not method:
            raise HttpParserError("Incomplete request")

        version = parser.get_http_version()

        return HttpRequest(
            method=method.decode("utf-8"),
            path=callbacks.url.decode("utf-8") if callbacks.url else "/",
            version=f"HTTP/{version}" if version else "HTTP/1.1",
            headers={
                k.decode("utf-8").lower(): v.decode("utf-8")
                for k, v in callbacks.headers.items()
            },
            body=b"".join(callbacks.body_chunks) if callbacks.body_chunks else None,
        )

    @override
    def __repr__(self) -> str:
        body_len = len(self.body) if self.body else 0
        return (
            f"HttpRequest(method={self.method!r}, path={self.path!r}, "
            f"version={self.version!r}, body_len={body_len})"
        )

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HttpRequest):
            return NotImplemented
        return (
            self.method == other.method
            and self.path == other.path
            and self.version == other.version
            and self.headers == other.headers
            and self.body == other.body
        )

    def __bytes__(self) -> bytes:
        status_line = (f"{self.method} {self.path} {self.version}").encode() + b"\r\n"

        headers = dict(self.headers)
        body = self.body
        if body is not None:
            if headers.get("Transfer-Encoding", "").lower() == "chunked":
                body = encode_chunked(body)
                headers["Transfer-Encoding"] = "chunked"
                _ = headers.pop("Content-Length", None)

            elif "Content-Length" not in headers:
                headers["Content-Length"] = str(len(body))

        header_bytes = (
            b"\r\n".join([f"{k}: {v}".encode() for k, v in headers.items()])
            + b"\r\n"
            + b"\r\n"
        )
        if body is None:
            return status_line + header_bytes

        return status_line + header_bytes + body


def reason_text(status: int) -> str:
    """Return default reason phrase for status code."""
    reasons = {
        100: "Continue",
        101: "Switching Protocols",
        200: "OK",
        201: "Created",
        202: "Accepted",
        204: "No Content",
        301: "Moved Permanently",
        302: "Found",
        304: "Not Modified",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        409: "Conflict",
        413: "Payload Too Large",
        414: "URI Too Long",
        500: "Internal Server Error",
        501: "Not Implemented",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    return reasons.get(status, "Unknown")


@final
class HttpResponse:
    """Represents an HTTP/1.1 response."""

    __slots__ = ("version", "status", "reason", "headers", "body")

    def __init__(
        self,
        status: int,
        reason: str | None = None,
        version: str = "HTTP/1.1",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.version = version
        self.status = status
        self.reason = reason if reason is not None else reason_text(status)
        self.headers = headers if headers is not None else {}
        self.body = body

    @staticmethod
    def parse(data: bytes) -> "HttpResponse":
        """Parse an HTTP/1.1 response from raw bytes.

        Args:
            data: Raw HTTP response bytes

        Returns:
            HttpResponse object

        Raises:
            HttpParserError: If response is malformed
        """
        if not data:
            raise HttpParserError("Incomplete response")

        callbacks = ResponseCallbacks()
        parser = HttpResponseParser(callbacks)

        try:
            parser.feed_data(data)  # pyright: ignore[reportUnknownMemberType]

        except HttptoolsParserError as e:
            raise HttpParserError(str(e)) from e

        status = parser.get_status_code()
        if not status:
            raise HttpParserError("Incomplete response")

        version = parser.get_http_version()
        return HttpResponse(
            version=f"HTTP/{version}" if version else "HTTP/1.1",
            status=status,
            reason=callbacks.reason.decode("utf-8") if callbacks.reason else None,
            headers={
                k.decode("utf-8").lower(): v.decode("utf-8")
                for k, v in callbacks.headers.items()
            },
            body=b"".join(callbacks.body_chunks) if callbacks.body_chunks else None,
        )

    @override
    def __repr__(self) -> str:
        body_len = len(self.body) if self.body else 0
        return (
            f"HttpResponse(status={self.status}, reason={self.reason!r}, "
            f"version={self.version!r}, body_len={body_len})"
        )

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HttpResponse):
            return NotImplemented
        return (
            self.version == other.version
            and self.status == other.status
            and self.reason == other.reason
            and self.headers == other.headers
            and self.body == other.body
        )

    def __bytes__(self) -> bytes:
        status_line = (f"{self.version} {self.status} {self.reason}").encode() + b"\r\n"
        headers = dict(self.headers)
        body = self.body
        if body is not None:
            if headers.get("Transfer-Encoding", "").lower() == "chunked":
                body = encode_chunked(body)
                headers["Transfer-Encoding"] = "chunked"
                _ = headers.pop("Content-Length", None)

            elif "Content-Length" not in headers:
                headers["Content-Length"] = str(len(body))

        header_bytes = (
            b"\r\n".join([f"{k}: {v}".encode() for k, v in headers.items()])
            + b"\r\n"
            + b"\r\n"
        )
        if body is None:
            return status_line + header_bytes

        return status_line + header_bytes + body
