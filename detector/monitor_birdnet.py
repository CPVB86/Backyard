"""Persistent public BirdNET session, isolated for bounded shutdown on Linux."""
from functools import partial
import logging
import multiprocessing
import os
from pathlib import Path
import signal
import time
import tempfile

from detector.birdnet_adapter import normalize
from detector.providers import geo_configuration


class BirdNETSession:
    """One context and one worker across every run_arrays call, including warmup."""
    def __init__(self, threshold=0.60):
        self.threshold = threshold

    def __enter__(self):
        import birdnet
        from importlib.metadata import version
        if version("birdnet") != "1.1.1":
            raise RuntimeError("Monitor requires the verified birdnet==1.1.1")
        logging.getLogger("birdnet").setLevel(logging.WARNING)
        self.model = birdnet.load("acoustic", "3.0", "onnx", precision="fp32")
        self.context = self.model.predict_session(
            top_k=None, default_confidence_threshold=0.0, n_producers=1,
            n_workers=1, batch_size=1, prefetch_ratio=1, overlap_duration_s=0,
            half_precision=False, device="CPU", show_stats=None, max_n_files=1,
        )
        self.session = self.context.__enter__()
        return self

    def analyze(self, pcm, rate):
        import numpy as np
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        result = self.session.run_arrays((samples, rate))
        if len(result.unprocessable_inputs):
            raise RuntimeError("BirdNET could not process PCM window")
        # Apply >= ourselves; do not depend on library threshold equality rules.
        return normalize(row for row in result.to_structured_array()
                         if float(row["confidence"]) >= self.threshold)

    def cancel(self):
        self.session.cancel()

    def __exit__(self, *args):
        return self.context.__exit__(*args)


def session_worker(connection, threshold, temporary, *, geography=None):
    # Own temporary directory also contains library logs; parent removes it.
    os.environ["TMPDIR"] = temporary
    tempfile.tempdir = temporary
    # This supervisor and BirdNET's children are separate from the terminal's
    # process group. Ctrl+C is handled by the monitor; SIGTERM requests cancel.
    if os.name == "posix":
        os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    stopping = False
    analyzer = None

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if analyzer is not None:
            analyzer.cancel()

    signal.signal(signal.SIGTERM, stop)
    connection.send(("group_ready", None))
    try:
        os.environ.setdefault("BIRDNET_APP_DATA",
                              str(Path(".detector-test/model-cache").resolve()))
        with BirdNETSession(threshold) as loaded:
            analyzer = loaded
            if stopping:
                return
            # Ensures the real backend is loaded before opening the microphone.
            loaded.analyze(bytes(48000 * 3 * 2), 48000)
            geo = None
            if geography:
                from detector.providers import GeoPlausibility
                geo = GeoPlausibility(*geography)
            connection.send(("ready", geo.status if geo is not None else geo_configuration(False, None, None)))
            while not stopping:
                if not connection.poll(0.1):
                    continue
                item = connection.recv()
                if item is None:
                    break
                pcm, rate = item
                predictions = loaded.analyze(pcm, rate)
                if geo is not None:
                    predictions = geo.annotate(predictions)
                connection.send(("result", predictions))
    except (Exception, KeyboardInterrupt) as error:
        if not stopping:
            try:
                connection.send(("error", f"{type(error).__name__}: {error}"))
            except (OSError, EOFError):
                pass
    finally:
        connection.close()


class PersistentBirdNET:
    def __init__(self, threshold=0.60, target=session_worker, geography=None):
        self.geo_status = geo_configuration(bool(geography), *(geography or (None, None)))
        self.threshold = threshold
        self.temporary = None
        self.target = partial(target, geography=geography) if geography else target
        self.process = None
        self.connection = None
        self.group_ready = False

    def start(self, timeout=180):
        self.temporary = tempfile.TemporaryDirectory(prefix="backyard-monitor-")
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=self.target, args=(child, self.threshold, self.temporary.name), name="birdnet-session")
        try:
            self.process.start()
        finally:
            child.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connection.poll(0.1):
                kind, value = self.connection.recv()
                if kind == "group_ready":
                    self.group_ready = True
                elif kind == "ready":
                    if value is not None:
                        self.geo_status = value
                    return
                else:
                    self.geo_status.update(active=False, status="error", error=str(value))
                    raise RuntimeError(f"BirdNET startup failed: {value}")
            if not self.process.is_alive():
                raise RuntimeError("BirdNET exited during startup")
        raise RuntimeError("BirdNET startup exceeded 180s; verify cached model with analyze")

    def analyze(self, pcm, rate):
        self.connection.send((pcm, rate))
        # Called only by the inference thread. Main can cancel/kill the entire
        # process group if a native backend is stuck.
        while not self.connection.poll(0.1):
            if not self.process.is_alive():
                raise RuntimeError("BirdNET worker exited")
        kind, value = self.connection.recv()
        if kind != "result":
            raise RuntimeError(f"BirdNET inference failed: {value}")
        return value

    def temporary_bytes(self):
        if self.temporary is None:
            return 0
        return sum(path.stat().st_size for path in Path(self.temporary.name).rglob("*")
                   if path.is_file())

    def close(self):
        if self.process is None or self.process.pid is None:
            if self.temporary is not None:
                self.temporary.cleanup()
            return
        if self.process.is_alive():
            if os.name == "posix":
                try:
                    os.kill(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                # Production ALSA is Linux-only; used by hardware-free tests.
                self.process.terminate()
            self.process.join(timeout=5)
        forced = self.process.is_alive()
        if os.name == "posix" and self.group_ready:
            # Also reap any remaining library descendants after supervisor exit.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif forced:
            self.process.kill()
        self.process.join(timeout=1)
        self.connection.close()
        self.process.close()
        self.process = None
        self.temporary.cleanup()
        self.temporary = None
        return forced
