"""Bounded, sample-indexed primitives. No hardware, ML, HTTP or disk access."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
import math
from threading import Lock
import time
from urllib.parse import urlsplit
from uuid import uuid4, uuid5, NAMESPACE_URL
import wave

MODEL = "birdnet-1.1.1-acoustic-3.0-preview3.1-onnx-fp32"


@dataclass(frozen=True)
class MonitorConfig:
    device: str = "plughw:CARD=Device,DEV=0"
    rate: int = 48000
    channels: int = 1
    threshold: float = 0.60
    window: float = 3.0
    overlap: float = 0.0
    ring_seconds: float = 60.0
    inference_queue: int = 4
    clip_queue: int = 32
    outbound_queue: int = 16
    pre_roll: float = 1.0
    post_roll: float = 1.0
    api_url: str = "http://127.0.0.1:8010"
    http_timeout: float = 2.0
    attempts: int = 3
    status_seconds: float = 10.0
    capture_only: bool = False
    geography: bool = False
    latitude: str = ""
    longitude: str = ""
    policy_queue: int = 4

    def __post_init__(self):
        for name in ("threshold", "window", "overlap", "ring_seconds",
                     "pre_roll", "post_roll", "http_timeout", "status_seconds"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not self.device.strip() or self.device.startswith("-"):
            raise ValueError("A valid ALSA device is required")
        if self.rate not in (32000, 44100, 48000, 96000) or self.channels != 1:
            raise ValueError("Supported rates: 32000/44100/48000/96000; mono channels=1 only")
        if self.window != 3.0 or not 0 <= self.overlap < self.window:
            raise ValueError("BirdNET baseline requires window=3 and 0 <= overlap < 3")
        if self.hop_samples < 1 or not math.isclose(
                self.hop_samples, (self.window - self.overlap) * self.rate, abs_tol=1e-6):
            raise ValueError("Hop must be a positive whole number of capture samples")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if not 0 <= self.pre_roll <= 10 or not 0 <= self.post_roll <= 10:
            raise ValueError("pre/post roll must be between 0 and 10 seconds")
        if not self.window + self.pre_roll + self.post_roll <= self.ring_seconds <= 300:
            raise ValueError("Ring must hold window + pre/post roll, at most 300 seconds")
        for name in ("inference_queue", "clip_queue", "outbound_queue", "policy_queue"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 64:
                raise ValueError(f"{name} must be an integer between 1 and 64")
        if not 1 <= self.attempts <= 5 or not 0.1 <= self.http_timeout <= 5:
            raise ValueError("attempts=1..5 and http_timeout=0.1..5 required")
        if not 1 <= self.status_seconds <= 60:
            raise ValueError("status_seconds must be between 1 and 60")
        if self.geography:
            if not (-90 <= float(self.latitude) <= 90 and -180 <= float(self.longitude) <= 180):
                raise ValueError("Geography needs valid latitude/longitude")
        url = urlsplit(self.api_url)
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("Invalid API port")
        if (url.scheme not in ("http", "https") or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("api_url must be an HTTP(S) origin without credentials/path")

    @property
    def window_samples(self):
        return round(self.window * self.rate)

    @property
    def hop_samples(self):
        return round((self.window - self.overlap) * self.rate)


@dataclass(frozen=True)
class StreamAnchor:
    session_id: str
    device: str
    rate: int
    utc: datetime
    monotonic: float

    @classmethod
    def now(cls, config):
        return cls(str(uuid4()), config.device, config.rate,
                   datetime.now(timezone.utc), time.monotonic())

    def timestamp(self, sample):
        return (self.utc + timedelta(seconds=sample / self.rate)).isoformat()


class UnavailableAudio(ValueError):
    pass


class PCMRing:
    """Fixed PCM16 mono allocation. Half-open ranges [start, end)."""
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.data = bytearray(capacity * 2)
        self.end = 0
        self.lock = Lock()

    def bounds(self):
        with self.lock:
            return max(0, self.end - self.capacity), self.end

    def append(self, pcm):
        if len(pcm) % 2:
            raise ValueError("Incomplete PCM16 sample")
        count = len(pcm) // 2
        with self.lock:
            end = self.end + count
            kept = min(count, self.capacity)
            payload = memoryview(pcm)[(count - kept) * 2:]
            offset = (end - kept) % self.capacity * 2
            first = min(len(payload), len(self.data) - offset)
            self.data[offset:offset + first] = payload[:first]
            self.data[:len(payload) - first] = payload[first:]
            self.end = end
        return end

    def read(self, start, end):
        with self.lock:
            if not max(0, self.end - self.capacity) <= start < end <= self.end:
                raise UnavailableAudio(f"PCM range [{start}, {end}) unavailable")
            offset = start % self.capacity * 2
            size = (end - start) * 2
            first = min(size, len(self.data) - offset)
            return bytes(self.data[offset:offset + first] + self.data[:size - first])


class LatestQueue:
    """Atomic drop-oldest queue; never waits for a consumer."""
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.items = deque()
        self.lock = Lock()
        self.dropped = 0

    def put(self, item):
        with self.lock:
            dropped = None
            if len(self.items) == self.capacity:
                dropped = self.items.popleft()[1]
                self.dropped += 1
            self.items.append((time.monotonic(), item))
            return dropped

    def take(self):
        with self.lock:
            return self.items.popleft()[1] if self.items else None

    def take_matching(self, predicate):
        # Used by post-roll: waiting items keep their original age/order.
        with self.lock:
            for index, (_, item) in enumerate(self.items):
                if predicate(item):
                    del self.items[index]
                    return item
            return None

    def status(self):
        with self.lock:
            return len(self.items), time.monotonic() - self.items[0][0] if self.items else 0

    def clear(self):
        with self.lock:
            count = len(self.items)
            self.items.clear()
            return count


class Metrics:
    def __init__(self):
        self.values = {}
        self.lock = Lock()

    def add(self, key, value=1):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value

    def set(self, **values):
        with self.lock:
            self.values.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.values)


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    pcm: bytes
    scheduled: float


class Scheduler:
    def __init__(self, config, ring, queue, metrics):
        self.config, self.ring, self.queue, self.metrics = config, ring, queue, metrics
        self.next_start = 0

    def tick(self):
        first, end = self.ring.bounds()
        hop = self.config.hop_samples
        if self.next_start < first:
            lost = (first - self.next_start + hop - 1) // hop
            self.metrics.add("ring_overruns", lost)
            self.metrics.add("windows_dropped", lost)
            self.metrics.set(last_lost_range=[self.next_start, self.next_start + lost * hop])
            self.next_start += lost * hop
        # Only the newest bounded set can survive; skip excess without allocating them.
        available = max(0, (end - self.config.window_samples - self.next_start) // hop + 1)
        skipped = max(0, available - self.queue.capacity)
        if skipped:
            self.metrics.add("windows_dropped", skipped)
            self.metrics.set(last_lost_range=[self.next_start, self.next_start + skipped * hop])
            self.next_start += skipped * hop
        while self.next_start + self.config.window_samples <= end:
            start, finish = self.next_start, self.next_start + self.config.window_samples
            self.next_start += hop
            try:
                pcm = self.ring.read(start, finish)
            except UnavailableAudio:
                self.metrics.add("ring_overruns")
                self.metrics.add("windows_dropped")
                self.metrics.set(last_lost_range=[start, finish])
                continue
            old = self.queue.put(Window(start, finish, pcm, time.monotonic()))
            if old is not None:
                self.metrics.add("windows_dropped")
                self.metrics.set(last_lost_range=[old.start, old.end])


@dataclass(frozen=True)
class Candidate:
    payload: dict
    clip_start: int
    clip_end: int


def candidates(predictions, window, anchor, config):
    for prediction in predictions:
        if prediction.confidence < config.threshold:
            continue
        start = window.start + round(prediction.start_seconds * config.rate)
        end = window.start + round(prediction.end_seconds * config.rate)
        if not window.start <= start < end <= window.end:
            raise ValueError("BirdNET result outside scheduled window")
        clip_start = max(0, start - round(config.pre_roll * config.rate))
        clip_end = end + round(config.post_roll * config.rate)
        identity = f"{anchor.session_id}/{window.start}/{window.end}/{start}/{end}/{MODEL}/{prediction.scientific_name}/{prediction.common_name}"
        yield Candidate({
            "event_id": str(uuid5(NAMESPACE_URL, identity)),
            "source": "backyard-birdnet-monitor", "source_version": "1",
            "model_version": MODEL,
            "detected_at": anchor.timestamp(start),
            "scientific_name": prediction.scientific_name,
            "common_name": prediction.common_name,
            "confidence": prediction.confidence,
            "raw_metadata": {
                "stream_id": anchor.session_id, "device": anchor.device,
                "sample_rate": config.rate, "channels": 1, "sample_format": "S16_LE",
                "utc_anchor": anchor.utc.isoformat(), "monotonic_anchor": anchor.monotonic,
                "window_start_sample": window.start, "window_end_sample": window.end,
                "start_sample": start, "end_sample": end,
                "model_start_seconds": prediction.start_seconds,
                "model_end_seconds": prediction.end_seconds,
                "end_utc": anchor.timestamp(end), "hop_samples": config.hop_samples,
                "model_sample_rate": 32000, "backend": "onnx", "precision": "fp32",
                "clip_start_sample": clip_start, "clip_end_sample": clip_end,
                "clip_start_utc": anchor.timestamp(clip_start),
                "pre_roll_requested": config.pre_roll, "post_roll_requested": config.post_roll,
                "capture_gaps": 0, "timing": "estimated stream anchor; window, not call onset",
            },
        }, clip_start, clip_end)


@dataclass(frozen=True)
class Upload:
    payload: dict
    wav: bytes


class ClipExtractor:
    def __init__(self, ring, incoming, outgoing, metrics, rate):
        self.ring, self.incoming, self.outgoing = ring, incoming, outgoing
        self.metrics, self.rate = metrics, rate
        # Same bounded queue keeps waiting post-roll jobs; no unbounded pending list.

    def tick(self):
        count, _ = self.incoming.status()
        for _ in range(count):
            first, end = self.ring.bounds()
            item = self.incoming.take_matching(
                lambda pending: pending.clip_start < first or pending.clip_end <= end)
            if item is None:
                break
            if item.clip_start < first:
                self.metrics.add("clips_expired")
                self.metrics.add("ring_overruns")
                self.metrics.set(last_lost_clip=[item.clip_start, item.clip_end])
                continue
            try:
                pcm = self.ring.read(item.clip_start, item.clip_end)
            except UnavailableAudio:
                self.metrics.add("clips_expired")
                self.metrics.add("ring_overruns")
                continue
            stream = io.BytesIO()
            with wave.open(stream, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.rate)
                output.writeframes(pcm)
            if self.outgoing.put(Upload(item.payload, stream.getvalue())) is not None:
                self.metrics.add("uploads_dropped")
            self.metrics.add("clips_created")
