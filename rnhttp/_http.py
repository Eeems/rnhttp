import io
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Self,
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


class URL:
    def __init__(
        self,
        schema: bytes | None = None,
        host: bytes | None = None,
        port: int | None = None,
        path: bytes | None = None,
        query: bytes | None = None,
        fragment: bytes | None = None,
        userinfo: bytes | None = None,
    ) -> None:
        self.schema: bytes | None = schema
        self.host: bytes | None = host
        self.port: int | None = port
        self.path: bytes | None = path
        self.query: bytes | None = query
        self.fragment: bytes | None = fragment
        self.userinfo: bytes | None = userinfo

    @override
    def __str__(self) -> str:
        return bytes(self).decode()

    def __bytes__(self) -> bytes:
        url = (self.schema or b"http") + b"://"
        if self.userinfo is not None:
            url += self.userinfo + b"@"

        if self.host is not None:
            url += self.host

        if self.port is not None:
            url += b":" + str(self.port).encode()

        if self.path is not None:
            url += self.path

        if self.query is not None:
            url += b"?" + self.query

        if self.fragment is not None:
            url += b"#" + self.fragment

        return url


class Callbacks:
    def __init__(
        self,
        on_message_begin: Callable[[], None] | None = None,
        on_url: Callable[[URL], None] | None = None,
        on_header: Callable[[bytes, bytes], None] | None = None,
        on_headers_complete: Callable[[], None] | None = None,
        on_body: Callable[[bytes], None] | None = None,
        on_message_complete: Callable[[], None] | None = None,
        on_chunk_header: Callable[[], None] | None = None,
        on_chunk_complete: Callable[[], None] | None = None,
        on_status: Callable[[bytes], None] | None = None,
    ) -> None:
        self._on_message_begin: Callable[[], None] | None = on_message_begin
        self._on_url: Callable[[URL], None] | None = on_url
        self._on_header: Callable[[bytes, bytes], None] | None = on_header
        self._on_headers_complete: Callable[[], None] | None = on_headers_complete
        self._on_body: Callable[[bytes], None] | None = on_body
        self._on_message_complete: Callable[[], None] | None = on_message_complete
        self._on_chunk_header: Callable[[], None] | None = on_chunk_header
        self._on_chunk_complete: Callable[[], None] | None = on_chunk_complete
        self._on_status: Callable[[bytes], None] | None = on_status
        self.ready_event: threading.Event = threading.Event()
        self.message_event: threading.Event = threading.Event()
        self.body_event: threading.Event = threading.Event()
        self.header_event: threading.Event = threading.Event()
        self.chunk_event: threading.Event = threading.Event()
        self.status_event: threading.Event = threading.Event()
        self.url_event: threading.Event = threading.Event()
        self.url: URL | None = None
        self.status: bytes | None = None
        self.headers: dict[bytes, list[bytes]] = {}
        self.size: int = 0

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
            u.schema,
            u.host,
            u.port,
            u.path,
            u.query,
            u.fragment,
            u.userinfo,
        )
        if self._on_url:
            self._on_url(self.url)

    def on_header(self, name: bytes, value: bytes) -> None:
        name = name.lower()
        if name not in self.headers:
            self.headers[name] = []

        self.headers[name].append(value)
        if self._on_header:
            self._on_header(name, value)

        match name.lower():
            case b"host":
                assert self.url is not None
                self.url.host = value
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
        self.status = status
        if self._on_status:
            self._on_status(status)

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


class CallbacksIO(io.RawIOBase):
    def __init__(
        self,
        parser_cls: type[HttpRequestParser | HttpResponseParser],
    ) -> None:
        # TODO remove data from buffer after it's been read
        self.buffer: io.BytesIO = io.BytesIO()
        self.callbacks: Callbacks = Callbacks(
            on_body=self._on_body, on_message_complete=self._on_message_complete
        )
        self.parser: HttpRequestParser | HttpResponseParser = parser_cls(self.callbacks)

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
        self.buffer.close()
        return super().__exit__(exc_type, exc_val, exc_tb)

    def _on_body(self, data: bytes) -> None:
        _ = self.buffer.write(data)
        _ = self.buffer.seek(-len(data), io.SEEK_CUR)

    def _on_message_complete(self) -> None:
        _ = self.buffer.write(b"")

    @property
    def headers(self) -> dict[bytes, list[bytes]]:
        _ = self.callbacks.wait_headers()
        return self.callbacks.headers

    def __len__(self) -> int:
        if b"content-length" in self.headers:
            return int(self.headers[b"content-length"][0])

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

    def read1(self, size: int = -1, /) -> bytes:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        data = self.buffer.read1(size)
        self.callbacks.body_event.set()
        return data

    @override
    def readline(self, size: int | None = -1, /) -> bytes:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        data = self.buffer.readline(size)
        self.callbacks.body_event.set()
        return data

    @override
    def readlines(self, hint: int = -1, /) -> list[bytes]:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        lines = self.buffer.readlines(hint)
        self.callbacks.body_event.set()
        return lines

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int:
        _ = self.callbacks.wait_ready()
        self.callbacks.body_event.set()
        res = self.buffer.readinto(buffer)
        self.callbacks.body_event.set()
        return res


@final
class Request(CallbacksIO):
    def __init__(self) -> None:
        super().__init__(HttpRequestParser)

    @property
    def url(self) -> URL:
        _ = self.callbacks.wait_url()
        assert self.callbacks.url is not None
        return self.callbacks.url


@final
class Response(CallbacksIO):
    def __init__(self) -> None:
        super().__init__(HttpResponseParser)

    @property
    def status(self) -> bytes:
        _ = self.callbacks.wait_status()
        assert self.callbacks.status is not None
        return self.callbacks.status


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

    def feed(request: Request) -> None:
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
    with Request() as request:
        thread = threading.Thread(target=feed, args=(request,), daemon=True)
        thread.start()
        print(request.readall())

    thread.join()

    def feed(response: Response) -> None:
        time.sleep(0.1)
        _ = response.write(b"HTTP/1.1 200 OK\r\n")
        time.sleep(0.1)
        _ = response.write(b"Content-Length: 13\r\n")
        time.sleep(0.1)
        _ = response.write(b"\r\n")
        time.sleep(0.1)
        _ = response.write(b"Hello, World!")

    print("Response()")
    with Response() as response:
        thread = threading.Thread(target=feed, args=(response,), daemon=True)
        thread.start()
        print(response.readall())

    thread.join()
