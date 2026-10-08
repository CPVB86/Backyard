"""Private atomic storage and an OS lock shared by all manual commands."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


def private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError(f"Refusing symlink storage: {path}")
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)


def write_private(path: Path, text: str) -> None:
    private_parent(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_state(path: Path, state: dict) -> None:
    write_private(path, json.dumps(state, indent=2) + "\n")


@contextmanager
def locked(path: Path):
    private_parent(path)
    with path.open("a+b") as handle:
        path.chmod(0o600)
        if os.name == "nt":
            import msvcrt
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
