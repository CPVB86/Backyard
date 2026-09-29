"""Short-file diagnostics only; production continuous capture is future work."""
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import subprocess
import sys
import time
import wave


@dataclass(frozen=True)
class AudioInfo:
    rate: int
    channels: int
    frames: int
    duration: float
    size: int
    peak: float
    rms: float


def inspect_audio(path: Path) -> AudioInfo:
    if not path.is_file() or not 44 <= path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError("WAV ontbreekt, is leeg of groter dan 32 MiB")
    with wave.open(str(path), "rb") as stream:
        rate, channels, frames = stream.getframerate(), stream.getnchannels(), stream.getnframes()
        if stream.getsampwidth() != 2 or stream.getcomptype() != "NONE":
            raise ValueError("Diagnostiek vereist PCM signed 16-bit WAV")
        if channels not in (1, 2) or not 8000 <= rate <= 96000 or not 0 < frames / rate <= 60:
            raise ValueError("Verwacht 1-2 kanalen, 8-96 kHz en maximaal 60 seconden")
        raw = stream.readframes(frames + 1)
        if len(raw) != frames * channels * 2:
            raise ValueError("Onvolledige WAV-samples")
    samples = array("h", raw)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(v) for v in samples) / 32768
    rms = math.sqrt(sum(v * v for v in samples) / len(samples)) / 32768
    return AudioInfo(rate, channels, frames, frames / rate, path.stat().st_size, peak, rms)


def dbfs(value):
    return "-inf" if value == 0 else f"{20 * math.log10(value):.1f}"


def describe(info):
    return (f"{info.duration:.3f}s | {info.rate} Hz | {info.channels} kanaal/kanalen | "
            f"{info.size} bytes | peak {dbfs(info.peak)} dBFS | RMS {dbfs(info.rms)} dBFS")


def capture(path, *, device, rate=48000, channels=1, duration=6):
    """One bounded recording; owns only a newly created file, removes it on error."""
    if not device or device.startswith("-"):
        raise ValueError("Geef een expliciet ALSA-device uit arecord -L op")
    if rate not in (8000, 16000, 32000, 44100, 48000, 96000) or channels not in (1, 2):
        raise ValueError("Ongeldige sample rate of kanalen")
    if not isinstance(duration, int) or not 1 <= duration <= 30:
        raise ValueError("Duur moet 1-30 hele seconden zijn")
    path = Path(path).resolve()
    # Exclusive ownership; never overwrite arbitrary caller files.
    with path.open("xb"):
        pass
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    try:
        process = subprocess.Popen([
            "arecord", "-q", "-D", device, "-t", "wav", "-f", "S16_LE",
            "-r", str(rate), "-c", str(channels), "-d", str(duration), str(path),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            _, error = process.communicate(timeout=duration + 10)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
        if process.returncode:
            raise RuntimeError(f"arecord mislukt ({process.returncode}): {error[-2000:].strip()}")
        info = inspect_audio(path)
        if (info.rate != rate or info.channels != channels
                or abs(info.duration - duration) > 0.1):
            raise ValueError("Opnameformaat/duur wijkt af van de aanvraag")
        return info, started_at, time.monotonic() - started_clock
    except BaseException:
        path.unlink(missing_ok=True)
        raise
