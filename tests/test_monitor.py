"""Hardware/model/network-free tests of the production pipeline."""
from dataclasses import replace
from datetime import datetime, timezone
import io
import struct
from threading import Event, Thread
import time
from unittest.mock import Mock
from uuid import UUID
import wave

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from detector.birdnet_adapter import Prediction
from detector.monitor import Monitor, main, parser
from detector.monitor_capture import ALSACapture
from detector.monitor_http import Uploader, HTTPFailure
from detector.stream import (MonitorConfig, StreamAnchor, PCMRing, LatestQueue,
                             Metrics, Scheduler, Window, candidates, ClipExtractor,
                             UnavailableAudio, Upload)

RATE = 32000


def config(**values):
    return MonitorConfig(rate=RATE, **values)


def pcm(values):
    return struct.pack("<" + "h" * len(values), *values)


def anchor(rate=RATE):
    return StreamAnchor("test-stream", "test-device", rate,
                        datetime(2026, 9, 30, tzinfo=timezone.utc), 100.0)


def prediction(score=0.9):
    return Prediction("Parus major", "Great Tit", score, 0.0, 3.0)


def window(start=0, rate=RATE):
    return Window(start, start + 3 * rate, bytes(3 * rate * 2), time.monotonic())


def candidate(cfg=None, start=0):
    cfg = cfg or config()
    return next(candidates([prediction()], window(start), anchor(), cfg))


def test_ring_wraparound_and_exact_capacity():
    ring = PCMRing(5)
    ring.append(pcm([1, 2, 3]))
    ring.append(pcm([4, 5, 6, 7]))
    assert ring.bounds() == (2, 7)
    assert ring.read(2, 7) == pcm([3, 4, 5, 6, 7])
    assert ring.read(4, 6) == pcm([5, 6])
    assert len(ring.data) == 10
    with pytest.raises(UnavailableAudio):
        ring.read(1, 3)


def test_large_append_and_bounded_memory():
    ring, queue = PCMRing(8), LatestQueue(3)
    for number in range(1000):
        ring.append(pcm(list(range(20))))
        queue.put(number)
    assert len(ring.data) == 16 and ring.bounds() == (19992, 20000)
    assert ring.read(19992, 20000) == pcm(list(range(12, 20)))
    assert queue.status()[0] == 3 and queue.dropped == 997
    assert [queue.take() for _ in range(4)] == [997, 998, 999, None]


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (2, 1), (0, 3)])
def test_ring_refuses_invalid_ranges(start, end):
    ring = PCMRing(4)
    ring.append(pcm([1, 2]))
    with pytest.raises(UnavailableAudio):
        ring.read(start, end)


def test_incomplete_pcm_rejected():
    with pytest.raises(ValueError):
        PCMRing(3).append(b"x")


def test_sample_timing_does_not_read_wall_clock(monkeypatch):
    a = anchor()
    monkeypatch.setattr(time, "time", lambda: -999999)
    assert a.timestamp(48000) == "2026-09-30T00:00:01.500000+00:00"
    assert a.monotonic == 100


@pytest.mark.parametrize("overlap,starts", [(0, [0, 3, 6]), (1.5, [0, 1.5, 3, 4.5, 6])])
def test_scheduling_overlap(overlap, starts):
    cfg = config(overlap=overlap, inference_queue=8)
    ring, queue, stats = PCMRing(10 * RATE), LatestQueue(8), Metrics()
    scheduler = Scheduler(cfg, ring, queue, stats)
    ring.append(bytes(RATE * 9 * 2))
    scheduler.tick()
    result = [queue.take() for _ in starts]
    assert [w.start for w in result] == [round(s * RATE) for s in starts]
    assert all(w.end - w.start == RATE * 3 for w in result)
    scheduler.tick()
    assert queue.take() is None


def test_scheduler_drops_oldest_and_skips_large_backlog():
    cfg = config(inference_queue=2)
    ring, queue, stats = PCMRing(RATE * 60), LatestQueue(2), Metrics()
    scheduler = Scheduler(cfg, ring, queue, stats)
    ring.append(bytes(RATE * 9 * 2))
    scheduler.tick()
    assert queue.take().start == 3 * RATE
    ring.append(bytes(RATE * 6 * 2))
    scheduler.tick()
    assert [queue.take().start, queue.take().start] == [9 * RATE, 12 * RATE]
    assert stats.snapshot()["windows_dropped"] == 2


def test_scheduler_reports_overwritten_ranges():
    cfg = config(inference_queue=8)
    ring, queue, stats = PCMRing(6 * RATE), LatestQueue(8), Metrics()
    ring.append(bytes(12 * RATE * 2))
    Scheduler(cfg, ring, queue, stats).tick()
    assert stats.snapshot()["ring_overruns"] == 2
    assert queue.take().start == 6 * RATE


@pytest.mark.parametrize("values", [
    {"overlap": 3}, {"overlap": -1}, {"overlap": float("nan")},
    {"overlap": 2.9999999}, {"window": 6}, {"channels": 2},
    {"threshold": float("inf")}, {"threshold": 1.1}, {"ring_seconds": 4},
    {"ring_seconds": 301}, {"inference_queue": 0}, {"inference_queue": 65},
    {"outbound_queue": 1000}, {"pre_roll": -1}, {"post_roll": 11},
    {"api_url": "ftp://localhost"}, {"api_url": "http://user:password@localhost"},
    {"http_timeout": 20}, {"attempts": 100}, {"status_seconds": 0},
])
def test_invalid_configuration(values):
    with pytest.raises(ValueError):
        config(**values)


def test_cli_environment_and_override(monkeypatch):
    monkeypatch.setenv("BACKYARD_MONITOR_OVERLAP", "1.5")
    assert MonitorConfig(**vars(parser().parse_args([]))).overlap == 1.5
    assert parser().parse_args(["--overlap", "0"]).overlap == 0
    with pytest.raises(SystemExit):
        main(["--overlap", "3"])


def test_candidate_identity_timing_and_no_species_merging():
    cfg = config(overlap=1.5)
    first = candidate(cfg)
    again = candidate(cfg)
    later = candidate(cfg, int(1.5 * RATE))
    assert first.payload == again.payload
    assert UUID(first.payload["event_id"])
    assert first.payload["event_id"] != later.payload["event_id"]
    meta = later.payload["raw_metadata"]
    assert meta["start_sample"] == 48000 and meta["end_sample"] == 144000
    assert meta["clip_start_sample"] == 16000 and meta["clip_end_sample"] == 176000
    assert later.payload["detected_at"] == "2026-09-30T00:00:01.500000+00:00"


def test_threshold_boundary_and_result_range_validation():
    result = list(candidates([prediction(.59), prediction(.60)], window(), anchor(), config()))
    assert len(result) == 1 and result[0].payload["confidence"] == .60
    with pytest.raises(ValueError):
        list(candidates([replace(prediction(), end_seconds=4)], window(), anchor(), config()))


def test_clip_waits_for_postroll_without_blocking_then_wraparound():
    ring, incoming, outgoing, stats = PCMRing(6 * RATE), LatestQueue(2), LatestQueue(2), Metrics()
    extractor = ClipExtractor(ring, incoming, outgoing, stats, RATE)
    ring.append(bytes(6 * RATE * 2))
    item = candidate(start=3 * RATE)  # clip [2s,7s) crosses physical ring end
    incoming.put(item)
    extractor.tick()
    assert incoming.status()[0] == 1 and outgoing.take() is None
    ring.append(pcm([1234] * RATE))
    extractor.tick()
    upload = outgoing.take()
    with wave.open(io.BytesIO(upload.wav), "rb") as audio:
        assert audio.getnframes() == 5 * RATE
        assert audio.getnchannels() == 1 and audio.getframerate() == RATE
        assert audio.readframes(5 * RATE) == bytes(4 * RATE * 2) + pcm([1234] * RATE)
    assert upload.payload["raw_metadata"]["clip_start_sample"] == 2 * RATE


def test_preroll_clamped_only_at_stream_start():
    item = candidate()
    assert item.clip_start == 0 and item.clip_end == 4 * RATE
    ring, incoming, outgoing, stats = PCMRing(5 * RATE), LatestQueue(2), LatestQueue(2), Metrics()
    incoming.put(item)
    ring.append(bytes(6 * RATE * 2))
    ClipExtractor(ring, incoming, outgoing, stats, RATE).tick()
    assert outgoing.take() is None
    assert stats.snapshot()["clips_expired"] == 1
    assert stats.snapshot()["ring_overruns"] == 1


def test_outbound_queue_drops_are_visible():
    ring, incoming, outgoing, stats = PCMRing(6 * RATE), LatestQueue(3), LatestQueue(1), Metrics()
    ring.append(bytes(6 * RATE * 2))
    incoming.put(candidate())
    incoming.put(candidate(start=RATE))
    ClipExtractor(ring, incoming, outgoing, stats, RATE).tick()
    assert stats.snapshot()["uploads_dropped"] == 1
    assert outgoing.status()[0] == 1


class NoWait(Event):
    def wait(self, timeout=None):
        return self.is_set()


def upload_item():
    return Upload(candidate().payload, b"test-wav")


def test_retry_preserves_exact_payload_after_lost_post_response():
    transport, metrics = Mock(), Metrics()
    identity = "5fc1f1b8-16ac-49c3-b01b-6a1d3b77a73c"
    transport.request.side_effect = [TimeoutError("lost reply"), {"id": identity}, {}]
    assert Uploader(config(), metrics, NoWait(), transport).send(upload_item())
    calls = transport.request.call_args_list
    assert calls[0].args == calls[1].args
    assert calls[2].args[0:2] == ("PUT", f"/api/birds/detections/{identity}/audio")
    assert metrics.snapshot()["uploads_ok"] == 1


def test_audio_retry_does_not_repost_successful_detection():
    transport, metrics = Mock(), Metrics()
    transport.request.side_effect = [
        {"id": "5fc1f1b8-16ac-49c3-b01b-6a1d3b77a73c"}, HTTPFailure(503), {},
    ]
    assert Uploader(config(), metrics, NoWait(), transport).send(upload_item())
    assert [call.args[0] for call in transport.request.call_args_list] == ["POST", "PUT", "PUT"]
    assert transport.request.call_args_list[1].args == transport.request.call_args_list[2].args


@pytest.mark.parametrize("error,attempts", [(HTTPFailure(503), 3), (HTTPFailure(409), 1),
                                           (ConnectionRefusedError(), 3), (HTTPFailure(422), 1)])
def test_outage_and_terminal_errors_have_finite_retries(error, attempts):
    transport, metrics = Mock(), Metrics()
    transport.request.side_effect = error
    assert not Uploader(config(), metrics, NoWait(), transport).send(upload_item())
    assert transport.request.call_count == attempts
    assert metrics.snapshot()["uploads_failed"] == 1


def test_retry_wait_can_be_cancelled():
    stop, transport, stats = Event(), Mock(), Metrics()
    def failed(*_):
        stop.set()
        raise HTTPFailure(503)
    transport.request.side_effect = failed
    assert not Uploader(config(), stats, stop, transport).send(upload_item())
    assert transport.request.call_count == 1


def test_real_api_contract_clip_upload_and_idempotency(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3",
                        storage_root=tmp_path / "audio")
    ring, incoming, outgoing, stats = PCMRing(5 * RATE), LatestQueue(2), LatestQueue(2), Metrics()
    ring.append(bytes(5 * RATE * 2))
    incoming.put(candidate())
    ClipExtractor(ring, incoming, outgoing, stats, RATE).tick()
    upload = outgoing.take()
    with TestClient(create_app(settings), headers={"Authorization": "Bearer backyard-test-token"}) as client:
        class LocalTransport:
            def request(self, method, path, body, content_type):
                response = client.request(method, path, content=body,
                                          headers={"Content-Type": content_type})
                assert response.status_code in (200, 201), response.text
                return response.json()
        sender = Uploader(config(), stats, NoWait(), LocalTransport())
        assert sender.send(upload) and sender.send(upload)
        items = client.get("/api/birds/detections").json()
        assert len(items) == 1
        assert items[0]["raw_metadata"] == upload.payload["raw_metadata"]
        assert client.get(items[0]["audio_url"]).content == upload.wav


class FakeCapture:
    def __init__(self, config, ring, metrics, stop, fail):
        self.config, self.ring, self.metrics = config, ring, metrics
        self.stop, self.fail, self.last_read = stop, fail, time.monotonic()
        self.closed = False

    def start(self):
        pass

    def feed(self, seconds):
        self.ring.append(bytes(round(seconds * self.config.rate) * 2))
        self.last_read = time.monotonic()

    def close(self):
        self.closed = True


class FakeAnalyzer:
    def start(self):
        self.started = True

    def analyze(self, pcm, rate):
        return [prediction()]

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            pytest.fail("worker did not reach expected state")
        Event().wait(.005)


def test_capture_keeps_advancing_while_inference_blocked():
    entered, release = Event(), Event()
    class SlowAnalyzer(FakeAnalyzer):
        def analyze(self, pcm, rate):
            entered.set()
            assert release.wait(3)
            return []
        def close(self):
            release.set()
    monitor = Monitor(config(inference_queue=2), SlowAnalyzer(), FakeCapture)
    try:
        monitor.start()
        monitor.capture.feed(3)
        assert entered.wait(3)
        for _ in range(20):
            monitor.capture.feed(3)
            monitor.scheduler.tick()
        assert monitor.ring.bounds()[1] == 63 * RATE
        assert monitor.windows.status()[0] <= 2
        assert monitor.metrics.snapshot()["windows_dropped"] > 0
    finally:
        release.set()
        monitor.close()
    assert all(not thread.is_alive() for thread in monitor.threads)


def test_slow_http_does_not_hold_inference_or_capture():
    entered, release = Event(), Event()
    class SlowTransport:
        def request(self, *args):
            entered.set()
            assert release.wait(3)
            return {"id": "5fc1f1b8-16ac-49c3-b01b-6a1d3b77a73c"}
    monitor = Monitor(config(pre_roll=0, post_roll=0, outbound_queue=2),
                      FakeAnalyzer(), FakeCapture, SlowTransport())
    try:
        monitor.start()
        monitor.capture.feed(3)
        assert entered.wait(3)
        for expected in range(2, 7):
            monitor.capture.feed(3)
            wait_for(lambda: monitor.metrics.snapshot().get("windows_processed", 0) >= expected)
        assert monitor.outbound.status()[0] == 2
        assert monitor.metrics.snapshot()["uploads_dropped"] > 0
        assert monitor.ring.bounds()[1] == 18 * RATE
    finally:
        release.set()
        monitor.close()
    assert monitor.capture.closed


def test_capture_only_never_loads_model_or_creates_jobs():
    analyzer = Mock()
    monitor = Monitor(config(capture_only=True), analyzer, FakeCapture)
    try:
        monitor.start()
        monitor.capture.feed(30)
        assert monitor.windows.status()[0] == 0
        analyzer.start.assert_not_called()
    finally:
        monitor.close()
    analyzer.close.assert_not_called()


def test_normal_ctrl_c_has_no_traceback_and_closes(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BACKYARD_MONITOR_LOCK_FILE", str(tmp_path / "monitor.lock"))
    instance = Mock()
    instance.run.side_effect = KeyboardInterrupt()
    instance.status.return_value = {}
    monkeypatch.setattr("detector.monitor.Monitor", Mock(return_value=instance))
    assert main(["--geography", "--latitude", "52", "--longitude", "5"]) == 130
    instance.close.assert_called_once()
    output = capsys.readouterr()
    assert "gestopt" in output.out and "Traceback" not in output.err


def test_worker_failure_stops_monitor():
    class Bad(FakeAnalyzer):
        def analyze(self, *args):
            raise RuntimeError("model broke")
    monitor = Monitor(config(), Bad(), FakeCapture)
    try:
        monitor.start()
        monitor.capture.feed(3)
        assert monitor.stop.wait(3)
        assert "model broke" in monitor.error
    finally:
        monitor.close()


def test_alsa_opens_once_and_reassembles_partial_samples(monkeypatch):
    process = Mock()
    process.stdout.read.side_effect = [b"\x01", b"\x00\x02\x00", b""]
    process.stderr.readline.side_effect = [b"  rate : 32000\n", b""]
    process.poll.return_value = 0
    spawn = Mock(return_value=process)
    monkeypatch.setattr("detector.monitor_capture.subprocess.Popen", spawn)
    ring, stop, stats = PCMRing(20), Event(), Metrics()
    capture = ALSACapture(config(), ring, stats, stop, lambda _: stop.set())
    capture.start()
    assert stop.wait(3)
    capture.close()
    assert ring.read(0, 2) == pcm([1, 2])
    spawn.assert_called_once()
    args = spawn.call_args.args[0]
    assert "raw" in args and "-d" not in args and "--fatal-errors" in args
    assert stats.snapshot()["capture_gaps"] == 1


def test_alsa_overrun_invalidates_stream():
    stop, stats = Event(), Metrics()
    capture = ALSACapture(config(), PCMRing(20), stats, stop, lambda _: stop.set())
    capture.process = Mock()
    capture.process.stderr.readline.return_value = b"overrun!!! (at least 0.123 ms long)\n"
    capture._errors()
    assert stop.is_set() and stats.snapshot()["alsa_overruns"] == 1


def test_http_transport_contract_and_response_size(monkeypatch):
    from detector.monitor_http import HTTPTransport
    connection = Mock()
    response = Mock(status=201)
    response.read1.side_effect = [b'{"id":"example"}', b""]
    connection.getresponse.return_value = response
    constructor = Mock(return_value=connection)
    monkeypatch.setattr("detector.monitor_http.http.client.HTTPConnection", constructor)
    transport = HTTPTransport("http://127.0.0.1:8010", 2)
    assert transport.request("POST", "/api/birds/detections", b"{}", "application/json") == {"id": "example"}
    constructor.assert_called_with("127.0.0.1", 8010, timeout=2)
    connection.request.assert_called_with("POST", "/api/birds/detections", body=b"{}",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer backyard-test-token"})
    connection.close.assert_called_once()
    response.read1.side_effect = [b"x" * 8192] * 8 + [b"x"]
    with pytest.raises(ValueError, match="Oversized"):
        transport.request("POST", "/api/birds/detections", b"{}", "application/json")
    assert connection.close.call_count == 2


def test_status_reports_hop_ratio_and_queued_age():
    monitor = Monitor(config(overlap=1.5), FakeAnalyzer(), FakeCapture)
    monitor.anchor = StreamAnchor.now(monitor.config)
    monitor.metrics.set(windows_processed=2, inference_seconds_sum=2.4, inference_seconds=1.3)
    monitor.windows.put(window())
    status = monitor.status()
    assert status["realtime_ratio"] == .8
    assert status["inference_depth"] == 1 and status["inference_oldest_seconds"] >= 0
    assert status["capture_gaps"] == 0
    monitor.close()
    assert monitor.metrics.snapshot()["windows_abandoned"] == 1


def test_shutdown_clears_pending_postroll_and_outbound():
    analyzer = FakeAnalyzer()
    monitor = Monitor(config(), analyzer, FakeCapture)
    monitor.clips.put(candidate())
    monitor.outbound.put(upload_item())
    monitor.close()
    assert monitor.clips.status()[0] == monitor.outbound.status()[0] == 0
    assert monitor.metrics.snapshot()["clips_abandoned"] == 1
    assert monitor.metrics.snapshot()["uploads_abandoned"] == 1
    assert analyzer.closed


def test_alsa_negotiated_rate_mismatch_is_fatal():
    stop, stats = Event(), Metrics()
    capture = ALSACapture(config(), PCMRing(20), stats, stop, lambda _: stop.set())
    capture.process = Mock()
    capture.process.stderr.readline.return_value = b"  rate : 48000\n"
    capture._errors()
    assert stop.is_set()


def test_alsa_outer_plug_rate_wins_over_native_device_rate():
    stop, stats = Event(), Metrics()
    capture = ALSACapture(config(), PCMRing(20), stats, stop, lambda _: stop.set())
    capture.process = Mock()
    capture.process.stderr.readline.side_effect = [b"rate : 32000\n", b"rate : 48000\n", b""]
    capture._errors()
    assert capture.format_ready.is_set() and not stop.is_set()


def test_waiting_postroll_keeps_queue_order_and_age(monkeypatch):
    monkeypatch.setattr("detector.stream.time.monotonic", lambda: 100.0)
    ring, incoming, outgoing, stats = PCMRing(6 * RATE), LatestQueue(2), LatestQueue(2), Metrics()
    first, second = candidate(), candidate(start=RATE)
    incoming.put(first)
    incoming.put(second)
    monkeypatch.setattr("detector.stream.time.monotonic", lambda: 102.0)
    ClipExtractor(ring, incoming, outgoing, stats, RATE).tick()
    assert incoming.status() == (2, 2.0)
    assert incoming.put(candidate(start=2 * RATE)) == first
