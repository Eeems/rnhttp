# pyright: reportPrivateUsage=false
"""Tests for PipeIO ring buffer."""

import threading
import time

from rnhttp._pipe import PipeIO


class TestPipeIOBasic:
    """Basic PipeIO tests."""

    def test_write_returns_length(self):
        """Write returns number of bytes written."""
        with PipeIO(capacity=100) as pipe:
            result = pipe.write(b"hello")

        assert result == 5

    def test_read_zero_returns_empty(self):
        """Reading with size=0 returns empty immediately."""
        with PipeIO(capacity=100) as pipe:
            result = pipe.read(0)

        assert result == b""


class TestPipeIOBuffer:
    """Tests for buffer behavior."""

    def test_write_read_roundtrip(self):
        """Basic write then read."""
        with PipeIO(capacity=100) as pipe:
            size = pipe.write(b"hello")
            assert size == 5
            result = pipe.read(5)

        assert result == b"hello"

    def test_partial_read(self):
        """Partial read returns partial data."""
        with PipeIO(capacity=100) as pipe:
            _ = pipe.write(b"hello world")
            result = pipe.read(5)
            assert result == b"hello"

            result = pipe.read(6)
            assert result == b" world"

    def test_read_larger_than_available(self):
        """Reading with size larger than available blocks until EOF."""
        with PipeIO(capacity=100) as pipe:
            size = pipe.write(b"hello")

        assert size == 5
        # Read 1000 bytes - should return all available since EOF
        data = pipe.read(1000)
        assert data == b"hello"

        # Second read returns empty since EOF and no more data
        data = pipe.read(1000)
        assert data == b""


class TestPipeIOWriteBlocks:
    """Tests for write blocking when buffer is full."""

    def test_write_blocks_when_full(self):
        """Write should block when buffer is full."""
        with PipeIO(capacity=10) as pipe:
            # Fill the buffer
            _ = pipe.write(b"1234567890")

            # This should block - start in thread
            def write_more() -> None:
                _ = pipe.write(b"extra")

            thread = threading.Thread(target=write_more)
            thread.start()

            # Give it time to block
            time.sleep(0.1)

            # Read some data to free space
            _ = pipe.read(5)

            # Wait for write to complete
            thread.join(timeout=2)

        assert thread.is_alive() is False


class TestPipeIOReadBlocks:
    """Tests for read blocking when no data."""

    def test_read_blocks_when_empty_no_eof(self):
        """Read should block when no data available and not EOF."""
        with PipeIO(capacity=100) as pipe:
            data: bytes | None = None

            def read_data() -> None:
                nonlocal data
                data = pipe.read(5)  # Request 5 bytes

            thread = threading.Thread(target=read_data)
            thread.start()

            # Should block initially
            time.sleep(0.1)
            assert thread.is_alive()

            # Write data
            size = pipe.write(b"hello")
            assert size == 5

            # Wait for read to complete
            thread.join(timeout=2)

        assert thread.is_alive() is False
        assert data == b"hello"


class TestPipeIOEOF:
    """Tests for EOF behavior."""

    def test_close_signals_eof(self):
        """Close sets EOF and read returns remaining data."""
        with PipeIO(capacity=100) as pipe:
            size = pipe.write(b"hello")
            assert size == 5

        # Read should return remaining data
        data = pipe.read(10)
        assert data == b"hello"

        # Further reads return empty
        data = pipe.read(1)
        assert data == b""


class TestPipeIOWrapping:
    """Tests for ring buffer wrapping."""

    def test_wrap_around_write(self):
        """Test write that wraps around buffer end."""
        with PipeIO(capacity=10) as pipe:
            # Fill buffer
            size = pipe.write(b"1234567890")
            assert size == 10

            # Read some (reads 3 bytes: "123", leaves 7 in buffer)
            data = pipe.read(3)
            assert data == b"123"

            # Write more - should wrap
            size = pipe.write(b"abc")
            assert size == 3

            # Read all - should get remaining 7 from before + 3 new = 10 bytes
            data = pipe.read(10)

        assert data == b"4567890abc"
