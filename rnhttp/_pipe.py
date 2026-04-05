import io
import threading
from typing import (
    TYPE_CHECKING,
    cast,
)

from ._compat import override

if TYPE_CHECKING:
    from ._compat import ReadableBuffer


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
    def write(self, data: "ReadableBuffer", /) -> int:
        data = cast(
            memoryview,
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
                self._buffer[write_pos : write_pos + chunk_size] = data[
                    offset : offset + chunk_size
                ]
                self._write_pos = (write_pos + chunk_size) % self._capacity

            else:
                first = chunk_size - avail
                self._buffer[write_pos:] = data[offset : offset + avail]
                self._buffer[:first] = data[offset + avail : offset + chunk_size]
                self._write_pos = first

            offset += chunk_size
            self._available += chunk_size
            self._data_available.set()
            if self._available >= self._capacity:
                self._write_ready.clear()

        return length

    @override
    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""

        result = bytearray()

        while True:
            if self._available == 0:
                if self._eof or result:
                    break

                _ = self._data_available.wait()
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
        if self._data_available.is_set():
            return

        if self._available > 0 or self._eof:
            self._data_available.set()
