"""Linux advisory lock shared by manual and systemd monitor entrypoints."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def exclusive_monitor(path):
    # Production capture requires Linux; keep hardware-free Windows tests usable.
    if os.name != "posix":
        yield
        return
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="ascii") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Another Backyard monitor holds {path}; stop it first") from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()) + "\n")
            handle.flush()
            yield
        finally:
            # Never unlink: replacing the inode would allow two simultaneous locks.
            fcntl.flock(handle, fcntl.LOCK_UN)
