from dataclasses import replace
from datetime import datetime, timezone
from threading import Event
import time
from detector.birdnet_adapter import Prediction
from detector.stream import MonitorConfig, StreamAnchor, LatestQueue, Metrics, PCMRing, Window, ClipExtractor
from detector.observation_pipeline import ObservationPipeline
from detector.monitor import Monitor
from observations.policy import Policy


def setup_pipeline(**config_changes):
    config = MonitorConfig(**config_changes)
    incoming, clips, metrics = LatestQueue(4), LatestQueue(32), Metrics()
    anchor = StreamAnchor("session", "test-device", config.rate, datetime(2026,10,1,tzinfo=timezone.utc), time.monotonic())
    pipeline = ObservationPipeline(config, Policy(), incoming, clips, metrics, lambda: anchor)
    return config, incoming, clips, metrics, pipeline


def prediction(name="Parus major", confidence=.94):
    return Prediction(name, name, confidence, 0, 3, {"state":"normal","provider":"test"})


def test_chimp_regression_pipeline_creates_no_clip_or_observation():
    config, incoming, clips, metrics, pipeline = setup_pipeline(overlap=1.5)
    for index in range(3):
        start = index * 72000
        incoming.put((Window(start, start+144000, b"", time.monotonic()), [prediction("Pan troglodytes", .68)]))
        pipeline.tick()
    assert metrics.snapshot()["unsupported_domain_candidates"] == 3
    assert metrics.snapshot()["discarded_candidates"] == 3
    assert clips.status()[0] == 0 and pipeline.aggregator.active == {}
    assert metrics.snapshot()["last_discard_reasons"] == ["outside_target_domain"]


def test_three_overlapping_candidates_create_one_clip_with_context():
    config, incoming, clips, metrics, pipeline = setup_pipeline(overlap=1.5)
    for index, score in enumerate((.71,.91,.84)):
        start = index * 72000
        incoming.put((Window(start,start+144000,b"",time.monotonic()),[prediction(confidence=score)]))
        pipeline.tick()
    assert clips.status()[0] == 0
    incoming.put((Window(216000,360000,b"",time.monotonic()),[]))
    pipeline.tick()
    assert clips.status()[0] == 1
    ring, outgoing = PCMRing(48000*10), LatestQueue(4)
    ring.append(bytes(48000*7*2))
    ClipExtractor(ring, clips, outgoing, metrics, config.rate).tick()
    upload = outgoing.take()
    assert len(upload.payload["candidates"]) == 3
    assert upload.payload["clip_start_sample"] == 0
    assert upload.payload["clip_end_sample"] == 7*48000
    assert outgoing.take() is None
    assert metrics.snapshot()["auto_accepted"] == 1
    assert metrics.snapshot()["aggregation_count"] == 2


class Capture:
    def __init__(self, config, ring, metrics, stop, fail):
        self.ring, self.last_read = ring, time.monotonic()
    def start(self): pass
    def close(self): pass


class Analyzer:
    def start(self): pass
    def analyze(self, pcm, rate): return [prediction()]
    def close(self): pass


def wait_for(test):
    deadline = time.monotonic()+3
    while not test():
        assert time.monotonic() < deadline
        Event().wait(.01)


def test_blocked_policy_does_not_block_inference_or_capture():
    monitor = Monitor(MonitorConfig(policy_queue=2), Analyzer(), Capture)
    entered, release = Event(), Event()
    def blocked():
        entered.set()
        assert release.wait(3)
    monitor.observations.tick = blocked
    try:
        monitor.start()
        assert entered.wait(1)
        for index in range(6):
            monitor.ring.append(bytes(48000*3*2))
            wait_for(lambda: monitor.metrics.snapshot().get("windows_processed",0) >= index+1)
        assert monitor.ring.bounds()[1] == 18*48000
        assert monitor.policy_jobs.status()[0] == 2
        assert monitor.metrics.snapshot()["policy_batches_dropped"] >= 4
    finally:
        release.set()
        monitor.close()
    assert all(not thread.is_alive() for thread in monitor.threads)

def test_inference_policy_clip_and_http_full_observation_flow(tmp_path):
    from fastapi.testclient import TestClient
    from app.core.config import Settings
    from app.main import create_app
    from test_observation_debug import APITransport
    settings = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path/"audio")
    class Model(Analyzer):
        def analyze(self, pcm, rate):
            return [prediction(confidence=.94), prediction("Pan troglodytes", .68)]
    with TestClient(create_app(settings)) as client:
        monitor = Monitor(MonitorConfig(overlap=1.5), Model(), Capture, APITransport(client))
        monitor.anchor = StreamAnchor.now(monitor.config)
        monitor.ring.append(bytes(48000*8*2))
        for start in (0,72000,144000):
            monitor.windows.put(Window(start,start+144000,bytes(144000*2),time.monotonic()))
            monitor.infer_once()
            monitor.observations.tick()
        monitor.policy_jobs.put((Window(216000,360000,b"",time.monotonic()),[]))
        monitor.observations.tick()
        monitor.extractor.tick()
        monitor.upload_once()
        items = client.get("/api/observations").json()
        assert len(items) == 1 and items[0]["scientific_name"] == "Parus major"
        assert items[0]["supporting_candidate_count"] == 3
        assert items[0]["status"] == "auto_accepted"
        assert client.get(items[0]["audio_url"]).status_code == 200
        status = monitor.status()
        assert status["raw_candidates"] == 6
        assert status["unsupported_domain_candidates"] == 3
        assert status["observations_created"] == status["permanent_clips"] == 1
        assert status["aggregation_count"] == 2
        monitor.close()
