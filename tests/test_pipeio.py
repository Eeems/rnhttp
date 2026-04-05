# pyright: reportPrivateUsage=false
"""Tests for PipeIO ring buffer."""

import threading
import time

from rnhttp._pipe import PipeIO


class TestPipeIOBasic:
    """Basic PipeIO tests."""

    def test_default_capacity(self):
        """Test default capacity is 64KB."""
        pipe = PipeIO()
        assert pipe._capacity == 65536

    def test_custom_capacity(self):
        """Test custom capacity."""
        pipe = PipeIO(capacity=4096)
        assert pipe._capacity == 4096

    def test_write_returns_length(self):
        """Write returns number of bytes written."""
        pipe = PipeIO(capacity=100)
        result = _ = pipe.write(b"hello")
        assert result == 5

    def test_read_zero_returns_empty(self):
        """Reading with size=0 returns empty immediately."""
        pipe = PipeIO(capacity=100)
        result = pipe.read(0)
        assert result == b""


class TestPipeIOBuffer:
    """Tests for buffer behavior."""

    def test_write_read_roundtrip(self):
        """Basic write then read."""
        pipe = PipeIO(capacity=100)
        _ = pipe.write(b"hello")
        result = pipe.read(5)
        assert result == b"hello"

    def test_partial_read(self):
        """Partial read returns partial data."""
        pipe = PipeIO(capacity=100)
        _ = pipe.write(b"hello world")

        result = pipe.read(5)
        assert result == b"hello"

        result = pipe.read(6)
        assert result == b" world"

    def test_read_larger_than_available(self):
        """Reading with size larger than available blocks until EOF."""
        pipe = PipeIO(capacity=100)
        _ = pipe.write(b"hello")
        pipe.close()

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
        pipe = PipeIO(capacity=10)

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
        pipe = PipeIO(capacity=100)

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
        _ = pipe.write(b"hello")

        # Wait for read to complete
        thread.join(timeout=2)

        assert thread.is_alive() is False
        assert data == b"hello"


class TestPipeIOEOF:
    """Tests for EOF behavior."""

    def test_close_signals_eof(self):
        """Close sets EOF and read returns remaining data."""
        pipe = PipeIO(capacity=100)
        _ = pipe.write(b"hello")

        # Close sets EOF
        pipe.close()

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
        pipe = PipeIO(capacity=10)

        # Fill buffer
        _ = pipe.write(b"1234567890")

        # Read some (reads 3 bytes: "123", leaves 7 in buffer)
        _ = pipe.read(3)

        # Write more - should wrap
        _ = pipe.write(b"abc")

        # Read all - should get remaining 7 from before + 3 new = 10 bytes
        data = pipe.read(10)
        assert data == b"4567890abc"


class TestPipeIOFlush:
    """Tests for flush method."""

    def test_flush_signals_available(self):
        """Flush signals data_available if data waiting."""
        pipe = PipeIO(capacity=100)

        _ = pipe.write(b"hello")

        # Clear the event
        pipe._data_available.clear()

        # Flush should set it
        pipe.flush()

        assert pipe._data_available.is_set()
