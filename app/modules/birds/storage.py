"""Bounded PCM WAV storage. No user-supplied filesystem paths."""
import hashlib
import io
import os
from pathlib import PurePosixPath
import re
import struct
import wave
from uuid import uuid4

from fastapi import HTTPException

KEY_PATTERN = re.compile(
    r"(?:birds|bats|review/(?:bird|bat))/audio/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9a-f-]{36}\.wav"
)


def audio_path(root, key):
    if not KEY_PATTERN.fullmatch(key) or PurePosixPath(key).is_absolute():
        raise HTTPException(404, "Audio unavailable")
    root = root.resolve()
    path = root.joinpath(*PurePosixPath(key).parts).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404, "Audio unavailable")
    return path


def inspect_wav(data):
    try:
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ValueError("Not a WAV")
        if int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ValueError("Incomplete RIFF")
        offset = 12
        while offset < len(data):
            if offset + 8 > len(data):
                raise ValueError("Incomplete chunk")
            size = int.from_bytes(data[offset + 4:offset + 8], "little")
            offset += 8 + size
            if offset > len(data):
                raise ValueError("Truncated chunk")
            if offset % 2 and offset < len(data):
                offset += 1
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate, channels, width, frames = (
                wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()
            )
            if (wav.getcomptype() != "NONE" or channels not in (1, 2)
                    or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000
                    or not 0 < frames / rate <= 60):
                raise ValueError("Unsupported PCM parameters")
            if len(wav.readframes(frames + 1)) != frames * channels * width:
                raise ValueError("Incomplete PCM data")
    except (wave.Error, EOFError, ValueError, OverflowError, struct.error):
        raise HTTPException(422, "Expected complete PCM WAV: 1-2 channels, 8-192 kHz, duration >0 and <=60 seconds") from None
    return dict(
        content_type="audio/wav", codec="pcm", sample_rate=rate, channels=channels,
        sample_width=width, duration_seconds=frames / rate, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def sync_directory(path):
    # POSIX directory fsync makes the published filename durable before DB commit.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(root, key, data, sha256):
    """Return (path, newly_created). Reuse an identical orphan from a crash."""
    path = audio_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Recheck containment after directory creation (local storage is trusted).
    path = audio_path(root, key)
    if path.exists():
        if path.is_file() and path.stat().st_size == len(data):
            if hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
                return path, False
        raise HTTPException(409, "Unregistered audio conflict; operator review required")
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    published = False
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic exclusive publication, never overwrite an existing final file.
        os.link(temporary, path)
        published = True
        sync_directory(path.parent)
    except BaseException:
        if published:
            path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return path, True
