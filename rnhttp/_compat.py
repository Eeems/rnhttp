# pyright: reportUnnecessaryTypeIgnoreComment=none
# pyright: reportUnreachable=none
import sys
from typing import (
    TYPE_CHECKING,
    Protocol,
    TypeVar,
)

if sys.version_info < (3, 12):
    from typing_extensions import override

else:
    from typing import (  # noqa: E402
        override,  # pyright: ignore[reportUnknownVariableType,reportAttributeAccessIssue,reportUnknownType,reportUnnecessaryTypeIgnoreComment]
    )

__all__ = ["override"]

if TYPE_CHECKING:
    __all__ += ["ReadableBuffer", "WriteableBuffer"]
    if sys.version_info >= (3, 14):
        from _typeshed import (
            ReadableBuffer,
            WriteableBuffer,
        )

    elif sys.version_info >= (3, 12):
        from collections.abc import Buffer as ReadableBuffer
        from collections.abc import Buffer as WriteableBuffer

    else:
        # ReadableBuffer = bytes | bytearray | memoryview[bytes]
        # WriteableBuffer = bytes | bytearray | memoryview[bytes]
        from _typeshed import (
            ReadableBuffer,
            WriteableBuffer,
        )


__all__ += ["Reader", "Writer"]
if sys.version_info >= (3, 14):
    from io import (
        Reader,
        Writer,
    )

else:
    T_co = TypeVar("T_co", covariant=True)

    class Reader(Protocol[T_co]):
        def read(self, size: int = -1, /) -> T_co: ...

    T_contra = TypeVar("T_contra", contravariant=True)

    class Writer(Protocol[T_contra]):
        def write(self, data: T_contra, /) -> int: ...
