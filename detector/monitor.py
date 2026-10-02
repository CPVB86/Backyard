"""Production continuous-capture pipeline. Diagnostic detector.cli is separate."""
import argparse
from dataclasses import fields
from datetime import datetime, timezone
import json
import os
import signal
import shutil
import sys
from threading import Event, Thread
import time

from detector.instance import exclusive_monitor
from detector.providers import geo_configuration, birdnet_week
from detector.monitor_birdnet import PersistentBirdNET
from detector.monitor_capture import ALSACapture
from detector.monitor_http import Uploader
from detector.observation_pipeline import ObservationPipeline
from observations.policy import Policy
from detector.stream import (MonitorConfig, StreamAnchor, PCMRing, LatestQueue,
                             Metrics, Scheduler, ClipExtractor)


class Monitor:
    def __init__(self, config, analyzer=None, capture_factory=ALSACapture, transport=None, policy=None):
        self.config = config
        self.policy = policy or Policy.load()
        if not config.capture_only:
            if config.threshold > self.policy.review_lower:
                raise ValueError("Base threshold must not exceed policy review_lower")
            if config.ring_seconds < self.policy.max_event_seconds + self.policy.idle_seconds + config.pre_roll + config.post_roll:
                raise ValueError("Ring too short for aggregation span + idle + pre/post context")
        self.analyzer = analyzer if analyzer is not None else PersistentBirdNET(
            config.threshold, geography=(config.latitude, config.longitude) if config.geography else None)
        self.capture_factory, self.transport = capture_factory, transport
        self.stop = Event()
        self.metrics = Metrics()
        self.ring = PCMRing(round(config.ring_seconds * config.rate))
        self.windows = LatestQueue(config.inference_queue)
        self.policy_jobs = LatestQueue(config.policy_queue)
        self.clips = LatestQueue(config.clip_queue)
        self.outbound = LatestQueue(config.outbound_queue)
        self.scheduler = Scheduler(config, self.ring, self.windows, self.metrics)
        self.extractor = ClipExtractor(self.ring, self.clips, self.outbound, self.metrics, config.rate)
        self.uploader = Uploader(config, self.metrics, self.stop, transport)
        self.threads = []
        self.capture = None
        self.anchor = None
        self.observations = ObservationPipeline(config, self.policy, self.policy_jobs, self.clips, self.metrics, lambda: self.anchor)
        self.error = None
        self.phase = "initializing"

    def fail(self, message):
        if self.error is None:
            self.error = message
        self.stop.set()

    def _launch(self, name, action, period):
        def loop():
            try:
                while not self.stop.is_set():
                    action()
                    self.stop.wait(period)
            except Exception as error:
                if not self.stop.is_set():
                    self.fail(f"{name}: {type(error).__name__}: {error}")
        thread = Thread(target=loop, name=name, daemon=True)
        self.threads.append(thread)
        thread.start()

    def start(self):
        if self.capture_factory is ALSACapture:
            if not sys.platform.startswith("linux") or shutil.which("arecord") is None:
                raise RuntimeError("Continuous capture requires Linux ALSA and arecord (alsa-utils)")
        if not self.config.capture_only:
            print("BirdNET model/session laden en opwarmen; microfoon start daarna.", flush=True)
            self.analyzer.start()
        self.anchor = StreamAnchor.now(self.config)
        self.capture = self.capture_factory(self.config, self.ring, self.metrics, self.stop, self.fail)
        self.capture.start()
        if not self.config.capture_only:
            self._launch("window-scheduler", self.scheduler.tick, 0.02)
            self._launch("birdnet-inference", self.infer_once, 0.01)
            self._launch("observation-policy", self.observations.tick, 0.02)
            self._launch("clip-extractor", self.extractor.tick, 0.02)
            self._launch("http-uploader", self.upload_once, 0.02)
        self.phase = "running"
        print(f"Monitor gestart: stream={self.anchor.session_id} device={self.config.device} "
              f"rate={self.config.rate} window=3 overlap={self.config.overlap} "
              f"capture_only={self.config.capture_only}", flush=True)
        print(json.dumps(self.status(), ensure_ascii=True), flush=True)

    def infer_once(self):
        window = self.windows.take()
        if window is None:
            return
        started = time.monotonic()
        self.metrics.set(inference_started_monotonic=started)
        predictions = self.analyzer.analyze(window.pcm, self.config.rate)
        finished = time.monotonic()
        elapsed = finished - started
        self.metrics.set(inference_started_monotonic=0)
        if self.stop.is_set():
            self.metrics.add("windows_cancelled")
            return
        self.metrics.add("windows_processed")
        self.metrics.add("inference_seconds_sum", elapsed)
        self.metrics.set(queue_wait_seconds=started - window.scheduled,
                         inference_seconds=elapsed,
                         latency_seconds=finished - self.anchor.monotonic - window.end / self.config.rate)
        self.metrics.add("detections", len(predictions))
        self.metrics.add("raw_candidates", len(predictions))
        if self.policy_jobs.put((window, predictions)) is not None:
            self.metrics.add("policy_batches_dropped")

    def upload_once(self):
        item = self.outbound.take()
        if item is not None:
            self.uploader.send(item)

    def status(self):
        result = {key: 0 for key in (
            "samples_captured", "capture_gaps", "alsa_overruns", "ring_overruns",
            "windows_processed", "windows_dropped", "detections", "clips_expired",
            "clips_dropped", "uploads_ok", "uploads_failed", "uploads_dropped",
            "http_failures", "queue_wait_seconds", "inference_seconds", "latency_seconds",
            "raw_candidates", "policy_candidates", "unsupported_domain_candidates", "observations_created",
            "observations_planned",
            "auto_accepted", "review_observations", "discarded_candidates", "permanent_clips",
            "review_clips", "discarded_clips", "aggregation_count", "policy_batches_dropped",
            "plausibility_normal", "plausibility_unusual", "plausibility_unknown")}
        result.update(self.metrics.snapshot())
        result["relevant_domain_candidates"] = max(0, result["policy_candidates"] - result["unsupported_domain_candidates"])
        result.update(event="monitor_status", phase=self.phase, error=self.error,
                      recorded_at=datetime.now(timezone.utc).isoformat(),
                      stream_id=self.anchor.session_id if self.anchor else None)
        geo = getattr(self.analyzer, "geo_status", None)
        geo = dict(geo) if isinstance(geo, dict) else geo_configuration(
            self.config.geography, self.config.latitude, self.config.longitude)
        if self.phase == "stopped":
            geo.update(active=False, status="stopped")
        if self.error:
            geo.update(active=False, status="error", error=self.error)
        result["geo"] = geo | {"week": birdnet_week(result["recorded_at"]),
                               "week_convention": "UTC month-quarter 1..48"}
        now = time.monotonic()
        if self.anchor:
            result["uptime_seconds"] = round(now - self.anchor.monotonic, 2)
            expected_utc = self.anchor.utc.timestamp() + now - self.anchor.monotonic
            result["wall_clock_shift_seconds"] = round(datetime.now(timezone.utc).timestamp() - expected_utc, 3)
            result["capture_clock_lag_seconds"] = round(
                now - self.anchor.monotonic - result["samples_captured"] / self.config.rate, 3)
        active = result.pop("inference_started_monotonic", 0)
        result["inference_active_seconds"] = round(now - active, 3) if active else 0
        count = result["windows_processed"]
        average = result.get("inference_seconds_sum", 0) / count if count else 0
        result["inference_mean_seconds"] = round(average, 3)
        result["realtime_ratio"] = round(average / (self.config.hop_samples / self.config.rate), 3)
        result["realtime_ratio_last"] = round(result["inference_seconds"] / (self.config.hop_samples / self.config.rate), 3)
        for name, queue in (("inference", self.windows), ("policy", self.policy_jobs), ("clips", self.clips), ("outbound", self.outbound)):
            depth, age = queue.status()
            result[f"{name}_depth"] = depth
            result[f"{name}_oldest_seconds"] = round(age, 3)
        return result

    def check_progress(self):
        now = time.monotonic()
        if now - self.capture.last_read > 3:
            self.metrics.add("capture_gaps")
            self.fail("No PCM received for >3s; stopping untrustworthy stream")
        active = self.metrics.snapshot().get("inference_started_monotonic", 0)
        if not self.config.capture_only and active and now - active > self.config.inference_timeout:
            self.metrics.add("inference_timeouts")
            self.fail(f"Inference exceeded {self.config.inference_timeout}s; stopping for service recovery")

    def run(self):
        self.start()
        next_status = time.monotonic() + self.config.status_seconds
        while not self.stop.wait(0.1):
            self.check_progress()
            if time.monotonic() >= next_status:
                if not self.config.capture_only:
                    size = self.analyzer.temporary_bytes()
                    self.metrics.set(birdnet_temp_bytes=size)
                    if size > 8 * 1024 * 1024:
                        self.fail("BirdNET temporary logs exceed 8 MiB; inspect repeated warnings")
                print(json.dumps(self.status(), ensure_ascii=True), flush=True)
                next_status = time.monotonic() + self.config.status_seconds
        if self.error:
            raise RuntimeError(self.error)

    def close(self):
        self.phase = "stopped"
        self.stop.set()
        if self.capture is not None:
            self.capture.close()
        if not self.config.capture_only:
            forced = self.analyzer.close()
            self.metrics.set(forced_worker_stop=bool(forced))
        # A stalled DNS call cannot hold up process exit: uploader is a daemon.
        # In-flight HTTP may have committed; never issue compensating deletion.
        deadline = time.monotonic() + self.config.http_timeout + 0.5
        for thread in self.threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        self.observations.abandon()
        self.metrics.set(policy_batches_abandoned=self.policy_jobs.clear())
        self.metrics.set(shutdown_threads_remaining=sum(t.is_alive() for t in self.threads),
                         windows_abandoned=self.windows.clear(),
                         clips_abandoned=self.clips.clear(),
                         uploads_abandoned=self.outbound.clear())


def parser():
    result = argparse.ArgumentParser(description="Continuous ALSA/BirdNET monitor (foreground or systemd)")
    defaults = MonitorConfig()
    for field in fields(defaults):
        value = getattr(defaults, field.name)
        flag = "--" + field.name.replace("_", "-")
        if isinstance(value, bool):
            result.add_argument(flag, action="store_true", default=os.environ.get(
                "BACKYARD_MONITOR_" + field.name.upper(), "0").lower() in ("1", "true", "yes"))
        else:
            result.add_argument(flag, type=type(value), default=os.environ.get(
                "BACKYARD_MONITOR_" + field.name.upper(), value))
    return result


class ShutdownRequested(BaseException):
    """SIGTERM interrupts startup as well as the running main loop."""


def _service_stop(_signum, _frame):
    raise ShutdownRequested()


def run_monitor(monitor):
    code = 0
    try:
        monitor.run()
    except ShutdownRequested:
        print("SIGTERM: monitor wordt gecontroleerd gestopt.", flush=True)
    except KeyboardInterrupt:
        code = 130
        print("Ctrl+C: monitor wordt gestopt.", flush=True)
    except (OSError, RuntimeError, ValueError, EOFError, ImportError) as error:
        code = 1
        monitor.error = str(error)
        print(f"Monitor fout: {error}", flush=True)
    finally:
        # A repeated stop signal must not interrupt child cleanup.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        monitor.close()
        print("Monitor gestopt: " + json.dumps(monitor.status(), ensure_ascii=True), flush=True)
    return code


def main(argv=None):
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        config = MonitorConfig(**vars(args))
        if not config.capture_only and not config.geography:
            raise ValueError("Production monitoring requires BACKYARD_MONITOR_GEOGRAPHY=1 and valid latitude/longitude")
        monitor = Monitor(config)
    except (ValueError, TypeError, OSError) as error:
        argument_parser.error(str(error))
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.signal(signal.SIGTERM, _service_stop)
    try:
        with exclusive_monitor(config.lock_file):
            return run_monitor(monitor)
    except ShutdownRequested:
        return 0  # Stopped before hardware/model startup.
    except (OSError, RuntimeError) as error:
        print(f"Monitor fout: {error}", flush=True)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
