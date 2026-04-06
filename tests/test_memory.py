"""Memory validation tests for streaming data through server and client.

Verify that PipeIO ring buffer and CallbacksIO interfaces use bounded memory
when streaming large payloads, and that backpressure works correctly.
"""

import gc
import os
import threading
import time

import psutil

from rnhttp._http import (
    RequestIO,
    ResponseIO,
)
from rnhttp._pipe import PipeIO


def get_rss() -> int:
    """Get current process RSS memory in bytes."""
    _ = gc.collect()
    return psutil.Process(os.getpid()).memory_info().rss  # pyright: ignore[reportAny]


class TestPipeIOMemory:
    """Tests for memory bounding in PipeIO."""

    def test_memory_stays_within_capacity(self) -> None:
        """Memory usage should stay within buffer capacity."""
        capacity = 1024
        with PipeIO(capacity=capacity) as pipe:
            baseline = get_rss()
            data = b"x" * capacity
            size = pipe.write(data)
            assert size == capacity
            growth = get_rss() - baseline

        assert growth < 102400

    def test_memory_bounded_after_many_writes(self) -> None:
        """Memory stays bounded after many writes with reads in between."""
        capacity = 4096
        with PipeIO(capacity=capacity) as pipe:
            baseline = get_rss()
            for _ in range(100):
                size = pipe.write(b"x" * capacity)
                assert size == capacity
                data = pipe.read(capacity)
                assert len(data) == capacity

            growth = get_rss() - baseline

        assert growth < 102400

    def test_memory_bounded_with_large_write(self) -> None:
        """Memory stays bounded when writing more than capacity."""
        capacity = 1024
        total_to_write = 100 * 1024
        written = 0
        chunk = b"x" * capacity
        with PipeIO(capacity=capacity) as pipe:
            baseline = get_rss()
            while written < total_to_write:
                size = pipe.write(chunk)
                assert size == capacity
                data = pipe.read(capacity)
                assert data == chunk
                written += capacity

            growth = get_rss() - baseline

        assert growth < 102400


class TestCallbacksIOMemory:
    """Tests for memory bounding in CallbacksIO (RequestIO/ResponseIO).

    These tests verify that small payloads work correctly with the
    CallbacksIO body_event synchronization mechanism.
    """

    def test_request_body_memory_bounded(self) -> None:
        """Request body streaming should use bounded memory with large payloads."""
        capacity = 4096
        request_io = RequestIO()
        request_io.buffer = PipeIO(capacity=capacity)
        baseline = get_rss()

        body_size = 5 * 1024 * 1024  # 5MB
        chunk_size = 4096
        headers = (
            b"POST /test HTTP/1.1\r\n"
            + b"Host: example.com\r\n"
            + f"Content-Length: {body_size}\r\n".encode()
            + b"\r\n"
        )

        write_done = threading.Event()
        read_done = threading.Event()

        def writer() -> None:
            # Write headers first
            _ = request_io.write(headers)
            # Write body in small chunks to exercise backpressure
            sent = 0
            while sent < body_size:
                to_write = min(chunk_size, body_size - sent)
                chunk = b"x" * to_write
                size = request_io.write(chunk)
                assert size == to_write
                sent += to_write
                time.sleep(0.001)

            request_io.close()
            write_done.set()

        def reader() -> None:
            total = 0
            while True:
                data = request_io.read(chunk_size)
                if not data:
                    break

                total += len(data)

            assert total == body_size
            read_done.set()

        write_thread = threading.Thread(target=writer, daemon=True)
        read_thread = threading.Thread(target=reader, daemon=True)

        # Start reader first so body_event is set before writer processes body
        read_thread.start()
        time.sleep(0.05)
        write_thread.start()

        write_thread.join(timeout=5)
        assert write_thread.is_alive() is False

        read_thread.join(timeout=5)
        assert read_thread.is_alive() is False
        assert read_done.is_set()

        growth = get_rss() - baseline
        assert growth < 1048576  # < 1MB — proves 5MB body didn't buffer fully

    def test_response_body_memory_bounded(self) -> None:
        """Response body streaming should use bounded memory with large payloads."""
        capacity = 4096
        response_io = ResponseIO()
        response_io.buffer = PipeIO(capacity=capacity)
        baseline = get_rss()

        body_size = 5 * 1024 * 1024  # 5MB
        chunk_size = 4096
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {body_size}\r\n".encode()
            + b"\r\n"
        )

        write_done = threading.Event()
        read_done = threading.Event()

        def writer() -> None:
            # Write headers first
            _ = response_io.write(headers)
            # Write body in small chunks to exercise backpressure
            sent = 0
            while sent < body_size:
                to_write = min(chunk_size, body_size - sent)
                chunk = b"x" * to_write
                size = response_io.write(chunk)
                assert size == to_write
                sent += to_write
                time.sleep(0.001)

            response_io.close()
            write_done.set()

        def reader() -> None:
            total = 0
            while True:
                data = response_io.read(chunk_size)
                if not data:
                    break

                total += len(data)

            assert total == body_size
            read_done.set()

        write_thread = threading.Thread(target=writer, daemon=True)
        read_thread = threading.Thread(target=reader, daemon=True)

        # Start reader first so body_event is set before writer processes body
        read_thread.start()
        time.sleep(0.05)
        write_thread.start()

        write_thread.join(timeout=5)
        assert write_thread.is_alive() is False

        read_thread.join(timeout=5)
        assert read_thread.is_alive() is False
        assert read_done.is_set()

        growth = get_rss() - baseline
        assert growth < 1048576  # < 1MB — proves 5MB body didn't buffer fully


class TestBackpressure:
    """Verify backpressure works correctly on PipeIO directly."""

    def test_write_blocks_at_capacity(self) -> None:
        """Write blocks when buffer reaches capacity."""
        pipe = PipeIO(capacity=10)
        size = pipe.write(b"1234567890")
        assert size == 10
        write_completed = threading.Event()

        def write_more() -> None:
            size = pipe.write(b"extra")
            assert size == 5
            write_completed.set()

        thread = threading.Thread(target=write_more, daemon=True)
        thread.start()

        time.sleep(0.1)
        assert not write_completed.is_set()

        data = pipe.read(5)
        assert data == b"12345"

        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert write_completed.is_set()

    def test_read_blocks_when_empty(self) -> None:
        """Read blocks when buffer is empty."""
        pipe = PipeIO(capacity=100)
        read_completed = threading.Event()
        result: list[bytes] = []

        def read_data() -> None:
            data = pipe.read(5)
            result.append(data)
            read_completed.set()

        thread = threading.Thread(target=read_data, daemon=True)
        thread.start()

        time.sleep(0.1)
        assert not read_completed.is_set()

        size = pipe.write(b"hello")
        assert size == 5

        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert read_completed.is_set()
        assert result[0] == b"hello"

    def test_read_returns_on_eof(self) -> None:
        """Read returns available data on EOF."""
        pipe = PipeIO(capacity=100)
        _ = pipe.write(b"hello")
        pipe.close()

        data = pipe.read(1000)
        assert data == b"hello"

        data = pipe.read(1000)
        assert data == b""

    def test_write_blocks_on_large_payload(self) -> None:
        """Write blocks when buffer is full during large payload."""
        capacity = 1024
        pipe = PipeIO(capacity=capacity)

        # Fill the buffer
        size = pipe.write(b"x" * capacity)
        assert size == capacity

        write_completed = threading.Event()
        total_read = [0]

        def write_more() -> None:
            size = pipe.write(b"y" * 10000)
            assert size == 10000
            pipe.close()
            write_completed.set()

        thread = threading.Thread(target=write_more, daemon=True)
        thread.start()

        # Should be blocked
        time.sleep(0.2)
        assert not write_completed.is_set()

        # Read any remaining data
        while True:
            data = pipe.read()
            if not data:
                break

            total_read[0] += len(data)

        thread.join(timeout=5)
        assert thread.is_alive() is False
        assert total_read[0] == 10000 + capacity


class TestLargePayloadStreaming:
    """Test large payload streaming with bounded memory using PipeIO directly."""

    def test_stream_large_data_through_pipe(self) -> None:
        """Streaming large data through PipeIO stays bounded."""
        capacity = 4096
        pipe = PipeIO(capacity=capacity)
        baseline = get_rss()

        total_size = 102400
        chunk_size = 1024
        written = 0
        read_total = 0

        def writer() -> None:
            nonlocal written
            while written < total_size:
                to_write = min(chunk_size, total_size - written)
                chunk = b"x" * to_write
                size = pipe.write(chunk)
                assert size == to_write
                written += len(chunk)

            pipe.close()

        write_thread = threading.Thread(target=writer, daemon=True)
        write_thread.start()

        while True:
            data = pipe.read()
            if not data:
                break

            read_total += len(data)

        write_thread.join(timeout=10)
        assert write_thread.is_alive() is False
        assert read_total == total_size

        growth = get_rss() - baseline
        assert growth < 102400

    def test_multiple_sequential_requests(self) -> None:
        """Multiple requests don't accumulate memory."""
        baseline = get_rss()

        for _ in range(10):
            pipe = PipeIO(capacity=4096)

            total = 10240
            chunk = 1024
            written = 0
            read_total = 0

            def writer() -> None:
                nonlocal written
                while written < total:
                    to_write = min(chunk, total - written)
                    size = pipe.write(b"x" * to_write)
                    assert size == to_write
                    written += chunk

                pipe.close()

            write_thread = threading.Thread(target=writer, daemon=True)
            write_thread.start()

            while True:
                data = pipe.read()
                if not data:
                    break

                read_total += len(data)

            write_thread.join(timeout=5)
            assert write_thread.is_alive() is False
            assert read_total == total

        growth = get_rss() - baseline
        assert growth < 102400
