import io
import threading
import time
from collections.abc import (
    Callable,
    Sized,
)
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Self,
    cast,
    final,
)

from httptools import (
    HttpRequestParser,
    HttpResponseParser,
    parse_url,  # pyright: ignore[reportUnknownVariableType]
)

from ._compat import override

if TYPE_CHECKING:
    from _typeshed import (
        ReadableBuffer,
        WriteableBuffer,
    )


class PipeIO(io.RawIOBase):
    """Fixed-size ring buffer. writes block when full. good for backpressure."""

    def __init__(self, capacity: int = 65536) -> None:
        self._buffer: bytearray = bytearray(capacity)
        self._capacity: int = capacity
        self._read_pos: int = 0
        self._write_pos: int = 0
        self._available: int = 0
        self._data_available: threading.Event = threading.Event()
        self._write_ready: threading.Event = threading.Event()
        self._write_ready.set()
        self._eof: bool = False

    @override
    def write(self, data: ReadableBuffer, /) -> int:
        data = cast(
            memoryview[bytes],
            data if isinstance(data, memoryview) else memoryview(data),
        )
        length = len(data)
        offset = 0

        while offset < length:
            while self._available >= self._capacity:
                _ = self._write_ready.wait()
                self._write_ready.clear()

            space = self._capacity - self._available
            chunk_size = min(space, length - offset)

            write_pos = self._write_pos
            avail = self._capacity - write_pos

            if chunk_size <= avail:
                self._buffer[write_pos : write_pos + chunk_size] = data[  # pyright: ignore[reportCallIssue, reportArgumentType]
                    offset : offset + chunk_size
                ]
                self._write_pos = (write_pos + chunk_size) % self._capacity
            else:
                first = chunk_size - avail
                self._buffer[write_pos:] = data[offset : offset + avail]  # pyright: ignore[reportCallIssue, reportArgumentType]
                self._buffer[:first] = data[offset + avail : offset + chunk_size]  # pyright: ignore[reportCallIssue, reportArgumentType]
                self._write_pos = first

            offset += chunk_size
            self._available += chunk_size

            if self._available >= self._capacity:
                self._write_ready.clear()

        self._data_available.set()
        return length

    @override
    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""

        result = bytearray()

        while True:
            if self._available == 0:
                if self._eof:
                    break
                _ = self._data_available.wait()
                self._data_available.clear()
                continue

            if size < 0:
                to_read = self._available
            else:
                remaining_needed = size - len(result)
                if remaining_needed <= 0:
                    break
                to_read = min(remaining_needed, self._available)

            read_pos = self._read_pos
            avail = self._capacity - read_pos

            if to_read <= avail:
                result.extend(self._buffer[read_pos : read_pos + to_read])
                self._read_pos = (read_pos + to_read) % self._capacity
            else:
                result.extend(self._buffer[read_pos:])
                result.extend(self._buffer[: to_read - avail])
                self._read_pos = to_read - avail

            self._available -= to_read
            self._write_ready.set()

            if self._available == 0:
                self._data_available.clear()

            if size > 0 and len(result) >= size:
                break

        if self._available > 0:
            self._data_available.set()

        return bytes(result)

    @override
    def close(self) -> None:
        self._eof = True
        self._data_available.set()
        self._write_ready.set()

    @override
    def flush(self) -> None:
        if self._available > 0 and not self._data_available.is_set():
            self._data_available.set()


class URL:
    def __init__(
        self,
        schema: str | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
        query: str | None = None,
        fragment: str | None = None,
        userinfo: str | None = None,
    ) -> None:
        self.schema: str | None = schema
        self.host: str | None = host
        self.port: int | None = port
        self.path: str | None = path
        self.query: str | None = query
        self.fragment: str | None = fragment
        self.userinfo: str | None = userinfo

    @override
    def __str__(self) -> str:
        url = ""
        if self.schema is not None:
            url += self.schema + "://"

        if self.userinfo is not None:
            url += self.userinfo + "@"

        if self.host is not None:
            url += self.host

        if self.port is not None:
            url += ":" + str(self.port)

        if self.path is not None:
            url += self.path

        if self.query is not None:
            url += "?" + self.query

        if self.fragment is not None:
            url += "#" + self.fragment

        return url

    def __bytes__(self) -> bytes:
        return str(self).encode()


class Callbacks:
    def __init__(
        self,
        on_message_begin: Callable[[], None] | None = None,
        on_url: Callable[[URL], None] | None = None,
        on_header: Callable[[str, str], None] | None = None,
        on_headers_complete: Callable[[], None] | None = None,
        on_body: Callable[[bytes], None] | None = None,
        on_message_complete: Callable[[], None] | None = None,
        on_chunk_header: Callable[[], None] | None = None,
        on_chunk_complete: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._on_message_begin: Callable[[], None] | None = on_message_begin
        self._on_url: Callable[[URL], None] | None = on_url
        self._on_header: Callable[[str, str], None] | None = on_header
        self._on_headers_complete: Callable[[], None] | None = on_headers_complete
        self._on_body: Callable[[bytes], None] | None = on_body
        self._on_message_complete: Callable[[], None] | None = on_message_complete
        self._on_chunk_header: Callable[[], None] | None = on_chunk_header
        self._on_chunk_complete: Callable[[], None] | None = on_chunk_complete
        self._on_status: Callable[[str], None] | None = on_status
        self.ready_event: threading.Event = threading.Event()
        self.message_event: threading.Event = threading.Event()
        self.body_event: threading.Event = threading.Event()
        self.header_event: threading.Event = threading.Event()
        self.chunk_event: threading.Event = threading.Event()
        self.status_event: threading.Event = threading.Event()
        self.url_event: threading.Event = threading.Event()
        self.url: URL | None = None
        self.status: str | None = None
        self.headers: dict[str, list[str]] = {}
        self.size: int = 0
        self.encoding: str = "us-ascii"

    def on_message_begin(self) -> None:
        self.status_event.clear()
        self.url_event.clear()
        self.header_event.clear()
        self.chunk_event.clear()
        self.body_event.clear()
        self.message_event.clear()
        if self._on_message_begin:
            self._on_message_begin()

        self.ready_event.set()

    def on_url(self, url: bytes) -> None:
        u = parse_url(url)
        self.url = URL(
            u.schema.decode(self.encoding) if u.schema is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
            u.host.decode(self.encoding) if u.host is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
            u.port,
            u.path.decode(self.encoding) if u.path is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
            u.query.decode(self.encoding) if u.query is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
            u.fragment.decode(self.encoding) if u.fragment is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
            u.userinfo.decode(self.encoding) if u.userinfo is not None else None,  # pyright: ignore[reportUnnecessaryComparison]
        )
        if self._on_url:
            self._on_url(self.url)

    def on_header(self, name: bytes, value: bytes) -> None:
        name_str = name.decode(self.encoding).lower()
        if name_str not in self.headers:
            self.headers[name_str] = []

        value_str = value.decode(self.encoding)
        self.headers[name_str].append(value_str)
        if self._on_header:
            self._on_header(name_str, value_str)

        match name_str:
            case "host":
                assert self.url is not None
                self.url.host = value_str
                self.url_event.set()

            case _:
                pass

    def on_headers_complete(self) -> None:
        if self._on_headers_complete:
            self._on_headers_complete()

        self.header_event.set()

    def on_body(self, body: bytes) -> None:
        _ = self.body_event.wait()
        if self._on_body:
            self._on_body(body)

        self.size += len(body)

    def on_message_complete(self) -> None:
        if self._on_message_complete:
            self._on_message_complete()

        self.status_event.set()
        self.url_event.set()
        self.header_event.set()
        self.chunk_event.set()
        self.body_event.set()
        self.message_event.set()

    def on_chunk_header(self) -> None:
        if self._on_chunk_header:
            self._on_chunk_header()

        self.chunk_event.clear()

    def on_chunk_complete(self) -> None:
        if self._on_chunk_complete:
            self._on_chunk_complete()

        self.chunk_event.set()

    def on_status(self, status: bytes) -> None:
        self.status = status.decode(self.encoding)
        if self._on_status:
            self._on_status(self.status)

        self.status_event.set()

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self.ready_event.wait(timeout)

    def wait(self, timeout: float | None = None) -> bool:
        if not self.wait_ready(timeout):
            return False

        self.body_event.set()  # Allow on_body to process until the message is done
        return self.message_event.wait(timeout)

    def wait_headers(self, timeout: float | None = None) -> bool:
        if not self.wait_ready(timeout):
            return False

        return self.header_event.wait(timeout)

    def wait_chunk(self, timeout: float | None = None) -> bool:
        if not self.wait_ready(timeout):
            return False

        return self.chunk_event.wait(timeout)

    def wait_status(self, timeout: float | None = None) -> bool:
        if not self.wait_ready(timeout):
            return False

        return self.status_event.wait(timeout)

    def wait_url(self, timeout: float | None = None) -> bool:
        if not self.wait_ready(timeout):
            return False

        return self.url_event.wait(timeout)

    def drain(self) -> None:
        self._on_message_begin = None
        self._on_url = None
        self._on_header = None
        self._on_headers_complete = None
        self._on_body = None
        self._on_message_complete = None
        self._on_chunk_header = None
        self._on_chunk_complete = None
        self._on_status = None
        self.ready_event.set()
        self.message_event.set()
        self.body_event.set()
        self.header_event.set()
        self.chunk_event.set()
        self.status_event.set()
        self.url_event.set()


class CallbacksIO(io.RawIOBase):
    def __init__(
        self,
        parser_cls: type[HttpRequestParser | HttpResponseParser],
    ) -> None:
        self.buffer: PipeIO = PipeIO()
        self.callbacks: Callbacks = Callbacks(
            on_body=self._on_body, on_message_complete=self._on_message_complete
        )
        self.parser: HttpRequestParser | HttpResponseParser = parser_cls(self.callbacks)
        self.encoding: str = "us-ascii"

    @override
    def __enter__(self) -> Self:
        return super().__enter__()

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.callbacks.drain()
        self.buffer.close()
        return super().__exit__(exc_type, exc_val, exc_tb)

    def _on_body(self, data: bytes) -> None:
        _ = self.buffer.write(data)

    def _on_message_complete(self) -> None:
        _ = self.buffer.write(b"")

    @property
    def headers(self) -> dict[str, list[str]]:
        _ = self.callbacks.wait_headers()
        return self.callbacks.headers

    def __len__(self) -> int:
        if "content-length" in self.headers:
            return int(self.headers["content-length"][0])

        if self.callbacks.message_event.is_set():
            return self.callbacks.size

        raise ValueError("Unable to determine size")

    @override
    def write(self, data: ReadableBuffer, /) -> int:
        self.parser.feed_data(memoryview(data))  # pyright: ignore[reportUnknownMemberType]
        return len(data) if hasattr(data, "__len__") else -1  # pyright: ignore[reportArgumentType]

    @override
    def read(self, size: int = -1, /) -> bytes:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        data = self.buffer.read(size)
        self.callbacks.body_event.clear()
        return data

    @override
    def readall(self, /) -> bytes:
        _ = self.callbacks.wait()
        return self.read(-1)

    @override
    def readline(self, size: int | None = -1, /) -> bytes:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        data = self.buffer.readline(size)
        self.callbacks.body_event.clear()
        return data

    @override
    def readlines(self, hint: int = -1, /) -> list[bytes]:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        lines = self.buffer.readlines(hint)
        self.callbacks.body_event.clear()
        return lines

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        res = self.buffer.readinto(buffer)
        self.callbacks.body_event.clear()
        return res

    @override
    def flush(self, /) -> None:
        self.buffer.flush()


@final
class RequestIO(CallbacksIO):
    def __init__(self) -> None:
        super().__init__(HttpRequestParser)

    @property
    def url(self) -> URL:
        _ = self.callbacks.wait_url()
        assert self.callbacks.url is not None
        return self.callbacks.url

    @property
    def method(self) -> str:
        _ = self.callbacks.wait_ready()
        assert isinstance(self.parser, HttpRequestParser)
        return self.parser.get_method().decode(self.encoding)


@final
class ResponseIO(CallbacksIO):
    def __init__(self) -> None:
        super().__init__(HttpResponseParser)

    @property
    def reason(self) -> str:
        _ = self.callbacks.wait_status()
        assert self.callbacks.status is not None
        return self.callbacks.status

    @property
    def status(self) -> int:
        _ = self.callbacks.wait_status()
        assert self.callbacks.status is not None
        assert isinstance(self.parser, HttpResponseParser)
        return self.parser.get_status_code()


class HttpSendTo:
    def __init__(
        self,
        body: io.Reader[bytes] | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.headers: dict[str, list[str]] = {}
        self._body: io.Reader[bytes] | bytes | None
        self.encoding: str = "us-ascii"
        if headers is not None:
            for key, value in headers.items():
                self.set_header(key, value)

        self.body = body

    @property
    def body(self) -> io.Reader[bytes] | bytes | None:
        return self._body

    @body.setter
    def body(self, body: io.Reader[bytes] | bytes | None) -> None:
        if isinstance(body, bytes):
            self.headers["content-length"] = [str(len(body))]

        self._body = body

    @property
    def statusline(self) -> bytes:
        raise NotImplementedError()

    def add_header(self, name: str, value: str) -> None:
        if name not in self.headers:
            self.headers[name] = []

        self.headers[name].append(value)

    def set_header(self, name: str, value: str) -> None:
        if name not in self.headers:
            self.headers[name] = []

        self.headers[name] = [value]

    def get_headers(self, name: str) -> list[str]:
        return self.headers.get(name, [])

    def get_header(self, name: str) -> str | None:
        values = self.get_headers(name)
        match len(values):
            case 0:
                return None

            case 1:
                return values[0]

            case _:
                raise ValueError(f"Header {name} has more than one value")

    def sendto(self, stream: io.Writer[bytes]) -> None:
        body = self.body
        if isinstance(body, bytes):
            body = io.BytesIO(body)

        def flush() -> None:
            if hasattr(stream, "flush") and isinstance(stream.flush, Callable):  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                stream.flush()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        _ = stream.write(self.statusline)
        transfer_encoding = self.headers.get("transfer-encoding", [""])[0].lower()
        if transfer_encoding == "chunked":
            _ = self.headers.pop("content-length", None)

        elif isinstance(body, Sized):
            self.headers["content-length"] = [str(len(body))]

        elif "content-length" not in self.headers:
            transfer_encoding = "chunked"
            self.headers["transfer-encoding"] = ["chunked"]

        for key, values in self.headers.items():
            for value in values:
                _ = stream.write(f"{key}: {value}\r\n".encode(self.encoding))

        _ = stream.write(b"\r\n")

        flush()
        if body is not None:
            while True:
                chunk = body.read(4096)
                if not chunk:
                    break

                if transfer_encoding == "chunked":
                    _ = stream.write(
                        f"{len(chunk):x}".encode() + b"\r\n" + chunk + b"\r\n"
                    )

                else:
                    _ = stream.write(chunk)

            if transfer_encoding == "chunked":
                _ = stream.write(b"0" + b"\r\n\r\n")

            flush()

        _ = stream.write(b"")  # EOF
        flush()


class Request(HttpSendTo):
    def __init__(
        self,
        method: str,
        url: URL,
        headers: dict[str, str] | None = None,
        body: io.Reader[bytes] | bytes | None = None,
    ) -> None:
        super().__init__(body=body, headers=headers)
        self.method: str = method.upper()
        self.url: URL = url
        if url.host is not None:
            self.headers["host"] = [url.host]

    @HttpSendTo.statusline.getter
    def statusline(self) -> bytes:
        url = URL(path=self.url.path, query=self.url.query)
        return f"{self.method} {url} HTTP/1.1\r\n".encode(self.encoding)


class Response(HttpSendTo):
    @staticmethod
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

    def __init__(
        self,
        status: int,
        reason: str | None = None,
        headers: dict[str, str] | None = None,
        body: io.Reader[bytes] | bytes | None = None,
    ) -> None:
        super().__init__(body=body, headers=headers)
        self.status: int = status
        self.reason: str = reason or Response.reason_text(status)

    def header(self, name: str, value: str) -> None:
        if name not in self.headers:
            self.headers[name] = []

        self.headers[name].append(value)

    @HttpSendTo.statusline.getter
    def statusline(self) -> bytes:
        return f"HTTP/1.1 {self.status} {self.reason}\r\n".encode(self.encoding)


if __name__ == "__main__":
    with io.BytesIO() as f:

        def feed(p: HttpRequestParser) -> None:  # pyright: ignore[reportRedeclaration]
            time.sleep(0.1)
            p.feed_data(b"GET /?test=1#test HTTP/1.1\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"Host: example.com\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"Content-Length: 4\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"test")  # pyright: ignore[reportUnknownMemberType]

        cb = Callbacks(on_body=lambda x: f.write(x))  # pyright: ignore[reportArgumentType]  # noqa: PLW0108
        p = HttpRequestParser(cb)
        thread = threading.Thread(target=feed, args=(p,), daemon=True)
        thread.start()
        print("HttpRequestParser()")
        _ = cb.wait_url()
        print(cb.url)
        _ = cb.wait_headers()
        print(cb.headers)
        _ = cb.wait()
        print(f.getvalue())

    thread.join()
    with io.BytesIO() as f:

        def feed(p: HttpRequestParser) -> None:  # pyright: ignore[reportRedeclaration]
            time.sleep(0.1)
            p.feed_data(b"HTTP/1.1 200 OK\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"Content-Length: 13\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"\r\n")  # pyright: ignore[reportUnknownMemberType]
            time.sleep(0.1)
            p.feed_data(b"Hello, World!")  # pyright: ignore[reportUnknownMemberType]

        print("HttpResponseParser()")
        cb = Callbacks(on_body=lambda x: f.write(x))  # pyright: ignore[reportArgumentType]  # noqa: PLW0108
        p = HttpResponseParser(cb)
        thread = threading.Thread(target=feed, args=(p,), daemon=True)
        thread.start()
        _ = cb.wait_status()
        print(cb.status)
        _ = cb.wait_headers()
        print(cb.headers)
        _ = cb.wait()
        print(f.getvalue())

    thread.join()

    def feed(request: RequestIO) -> None:  # pyright: ignore[reportRedeclaration]
        time.sleep(0.1)
        _ = request.write(b"GET /?test=1#test HTTP/1.1\r\n")
        time.sleep(0.1)
        _ = request.write(b"Host: example.com\r\n")
        time.sleep(0.1)
        _ = request.write(b"Content-Length: 4\r\n")
        time.sleep(0.1)
        _ = request.write(b"\r\n")
        time.sleep(0.1)
        _ = request.write(b"test")

    print("Request()")
    with RequestIO() as request:
        thread = threading.Thread(target=feed, args=(request,), daemon=True)
        thread.start()
        print(request.url)
        print(request.method)
        print(request.headers)
        print(request.readall())

    thread.join()

    def feed(response: ResponseIO) -> None:  # pyright: ignore[reportRedeclaration]
        time.sleep(0.1)
        _ = response.write(b"HTTP/1.1 200 OK\r\n")
        time.sleep(0.1)
        _ = response.write(b"Content-Length: 13\r\n")
        time.sleep(0.1)
        _ = response.write(b"\r\n")
        time.sleep(0.1)
        _ = response.write(b"Hello, World!")

    print("Response()")
    with ResponseIO() as response:
        thread = threading.Thread(target=feed, args=(response,), daemon=True)
        thread.start()
        print(response.status)
        print(response.reason)
        print(response.headers)
        print(response.readall())

    thread.join()

    def feed(request: RequestIO) -> None:  # pyright: ignore[reportRedeclaration]
        time.sleep(0.1)
        Request(
            "GET",
            URL(host="example.com", path="/", query="test=1", fragment="test"),
            body=b"test",
        ).sendto(request)

    print("Request()")
    with RequestIO() as request:
        thread = threading.Thread(target=feed, args=(request,), daemon=True)
        thread.start()
        print(request.url)
        print(request.method)
        print(request.headers)
        print(request.readall())

    thread.join()

    def feed(request: RequestIO) -> None:  # pyright: ignore[reportRedeclaration]
        time.sleep(0.1)
        Request(
            "GET",
            URL(host="example.com", path="/", query="test=1", fragment="test"),
            body=io.BytesIO(b"test"),
        ).sendto(request)

    print("Request() # chunked")
    with RequestIO() as request:
        thread = threading.Thread(target=feed, args=(request,), daemon=True)
        thread.start()
        print(request.url)
        print(request.method)
        print(request.headers)
        print(request.readall())

    thread.join()

    def feed(response: ResponseIO) -> None:  # pyright: ignore[reportRedeclaration]
        time.sleep(0.1)
        Response(200, body=b"test").sendto(response)

    print("Response()")
    with ResponseIO() as response:
        thread = threading.Thread(target=feed, args=(response,), daemon=True)
        thread.start()
        print(response.status)
        print(response.reason)
        print(response.headers)
        print(response.readall())

    thread.join()

    def feed(response: ResponseIO) -> None:
        time.sleep(0.1)
        Response(200, body=io.BytesIO(b"test")).sendto(response)

    print("Response() # chunked")
    with ResponseIO() as response:
        thread = threading.Thread(target=feed, args=(response,), daemon=True)
        thread.start()
        print(response.status)
        print(response.reason)
        print(response.headers)
        print(response.readall())

    thread.join()
