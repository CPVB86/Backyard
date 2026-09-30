"""Verified public API seam plus a real spawned, persistent fake worker."""
import os
from pathlib import Path
import signal
import struct
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest

from detector.monitor_birdnet import BirdNETSession, PersistentBirdNET


class FakeArray:
    def __init__(self, values):
        self.values = values
    def astype(self, dtype):
        assert dtype == "float32"
        return self
    def __truediv__(self, divisor):
        return [value / divisor for value in self.values]


def test_public_session_is_entered_once_and_reused_for_arrays(monkeypatch):
    session = MagicMock()
    result = SimpleNamespace(unprocessable_inputs=[], to_structured_array=lambda: [
        dict(species_name="Parus major_Great Tit", confidence=.9, start_time=0, end_time=3)])
    session.run_arrays.return_value = result
    context = MagicMock()
    context.__enter__.return_value = session
    model, load = Mock(), Mock()
    load.return_value = model
    model.predict_session.return_value = context
    monkeypatch.setitem(sys.modules, "birdnet", SimpleNamespace(load=load))
    monkeypatch.setattr("importlib.metadata.version", lambda _: "1.1.1")
    def frombuffer(data, dtype):
        assert dtype == "<i2"
        return FakeArray(struct.unpack("<hh", data))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(frombuffer=frombuffer, float32="float32"))
    with BirdNETSession(.6) as analyzer:
        for _ in range(3):
            predictions = analyzer.analyze(struct.pack("<hh", -32768, 16384), 48000)
            assert predictions[0].scientific_name == "Parus major"
        result.unprocessable_inputs = [0]
        with pytest.raises(RuntimeError):
            analyzer.analyze(struct.pack("<hh", 0, 0), 48000)
        analyzer.cancel()
    load.assert_called_once_with("acoustic", "3.0", "onnx", precision="fp32")
    model.predict_session.assert_called_once()
    kwargs = model.predict_session.call_args.kwargs
    assert kwargs["top_k"] is None and kwargs["default_confidence_threshold"] == 0.0
    assert kwargs["n_workers"] == 1 and kwargs["max_n_files"] == 1
    assert kwargs["overlap_duration_s"] == 0 and kwargs["half_precision"] is False
    model.predict.assert_not_called()
    assert session.run_arrays.call_args_list[0].args == (([-1.0, .5], 48000),)
    context.__enter__.assert_called_once()
    context.__exit__.assert_called_once()
    session.cancel.assert_called_once()


def fake_worker(connection, threshold, temporary):
    if os.name == "posix":
        os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    connection.send(("group_ready", None))
    connection.send(("ready", None))
    number = 0
    try:
        while True:
            data, rate = connection.recv()
            number += 1
            Path(temporary, "test.log").write_text("fake worker")
            connection.send(("result", (number, len(data), rate, threshold, os.getpid())))
    finally:
        connection.close()


def failing_worker(connection, threshold, temporary):
    connection.send(("error", "injected startup failure"))
    connection.close()


def test_spawned_worker_reused_and_temporary_directory_removed():
    worker = PersistentBirdNET(.7, target=fake_worker)
    try:
        worker.start(timeout=10)
        directory = Path(worker.temporary.name)
        first = worker.analyze(b"ab", 48000)
        second = worker.analyze(b"cd", 48000)
        assert first[0] == 1 and second[0] == 2
        assert first[-1] == second[-1]
        assert first[3] == .7 and worker.temporary_bytes() > 0
    finally:
        worker.close()
    assert not directory.exists()
    worker.close()


def test_startup_error_is_reported_and_process_reaped():
    worker = PersistentBirdNET(target=failing_worker)
    try:
        with pytest.raises(RuntimeError, match="injected startup failure"):
            worker.start(timeout=10)
        directory = Path(worker.temporary.name)
    finally:
        worker.close()
    assert not directory.exists()
