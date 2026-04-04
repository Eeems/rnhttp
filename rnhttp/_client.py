import io
import threading
import time
from collections.abc import Callable
from typing import cast

from httptools import (
    HttpRequestParser,
    parse_url,  # pyright: ignore[reportUnknownVariableType]
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
        self.message_event: threading.Event = threading.Event()
        self.header_event: threading.Event = threading.Event()
        self.chunk_event: threading.Event = threading.Event()
        self.status_event: threading.Event = threading.Event()
        self.url_event: threading.Event = threading.Event()
        self.url: URL | None = None
        self.status: bytes | None = None
        self.headers: dict[bytes, list[bytes]] = {}

    def on_message_begin(self) -> None:
        self.status_event.clear()
        self.url_event.clear()
        self.header_event.clear()
        self.chunk_event.clear()
        self.message_event.clear()
        if self._on_message_begin:
            self._on_message_begin()

    def on_url(self, url: bytes) -> None:
        u = cast(URL, parse_url(url))
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
        if self._on_body:
            self._on_body(body)

    def on_message_complete(self) -> None:
        if self._on_message_complete:
            self._on_message_complete()

        self.status_event.set()
        self.url_event.set()
        self.header_event.set()
        self.chunk_event.set()
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

    def wait(self, timeout: float | None = None) -> bool:
        return self.message_event.wait(timeout)

    def wait_headers(self, timeout: float | None = None) -> bool:
        return self.header_event.wait(timeout)

    def wait_chunk(self, timeout: float | None = None) -> bool:
        return self.chunk_event.wait(timeout)

    def wait_status(self, timeout: float | None = None) -> bool:
        return self.status_event.wait(timeout)

    def wait_url(self, timeout: float | None = None) -> bool:
        return self.url_event.wait(timeout)


if __name__ == "__main__":
    with io.BytesIO() as f:
        cb = Callbacks(on_body=lambda x: f.write(x))  # pyright: ignore[reportArgumentType]  # noqa: PLW0108
        p = HttpRequestParser(cb)

        def feed(p: HttpRequestParser):
            time.sleep(0.1)
            p.feed_data(b"GET /?test=1#test HTTP/1.1\r\n")
            time.sleep(0.1)
            p.feed_data(b"Host: example.com\r\n")
            time.sleep(0.1)
            p.feed_data(b"Content-Length: 4\r\n")
            time.sleep(0.1)
            p.feed_data(b"\r\n")
            time.sleep(0.1)
            p.feed_data(b"test")

        thread = threading.Thread(target=feed, args=(p,), daemon=True)
        thread.start()
        _ = cb.wait_url()
        print(cb.url)
        _ = cb.wait_headers()
        print(cb.headers)
        _ = cb.wait()
        print(f.getvalue())
        thread.join()
