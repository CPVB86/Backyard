import io
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

from detector.audio import capture, dbfs, inspect_audio
from detector.birdnet_adapter import BirdNETFileAnalyzer, normalize
from detector.cli import main, parser, show_results


def wav(path, samples=(0, 16384, -16384, 0), rate=48000, channels=1):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_pcm_diagnostics(tmp_path):
    path = tmp_path / "audio.wav"
    wav(path)
    info = inspect_audio(path)
    assert info.rate == 48000 and info.channels == 1
    assert info.frames == 4 and info.peak == 0.5
    assert info.rms == pytest.approx(math.sqrt(0.125))
    assert dbfs(0) == "-inf"
    assert dbfs(0.5) == "-6.0"


def test_silence_and_truncation(tmp_path):
    path = tmp_path / "audio.wav"
    wav(path, (0, 0))
    assert inspect_audio(path).rms == 0
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="Onvolledige"):
        inspect_audio(path)


def test_empty_audio_rejected(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"")
    with pytest.raises(ValueError):
        inspect_audio(path)


def test_capture_never_overwrites_existing(tmp_path):
    path = tmp_path / "important.wav"
    path.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        capture(path, device="configured-device")
    assert path.read_bytes() == b"preserve"


def test_capture_process_failure_removes_only_owned_file(tmp_path, monkeypatch):
    path = tmp_path / "capture.wav"
    process = Mock(returncode=1)
    process.communicate.return_value = ("", "device not available")
    start = Mock(return_value=process)
    monkeypatch.setattr("detector.audio.subprocess.Popen", start)
    with pytest.raises(RuntimeError, match="device not available"):
        capture(path, device="plughw:CARD=Device,DEV=0")
    assert not path.exists()
    command = start.call_args.args[0]
    assert command[command.index("-D") + 1] == "plughw:CARD=Device,DEV=0"
    assert command[command.index("-d") + 1] == "6"
    assert start.call_args.kwargs.get("shell", False) is False


def test_capture_interrupt_stops_child(tmp_path, monkeypatch):
    process = Mock()
    process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
    monkeypatch.setattr("detector.audio.subprocess.Popen", Mock(return_value=process))
    path = tmp_path / "capture.wav"
    with pytest.raises(KeyboardInterrupt):
        capture(path, device="device")
    process.terminate.assert_called_once()
    assert not path.exists()


def row(start=0, end=3, score=0.87):
    return dict(species_name="Parus major_Great Tit", confidence=score,
                start_time=start, end_time=end)


def test_overlap_windows_are_preserved():
    results = normalize([row(1.5, 4.5), row()])
    assert len(results) == 2
    assert results[0].scientific_name == "Parus major"
    assert results[0].common_name == "Great Tit"
    assert [r.start_seconds for r in results] == [0, 1.5]


@pytest.mark.parametrize("change", [
    {"confidence": 1.1}, {"confidence": float("nan")},
    {"start_time": -1}, {"end_time": 0}, {"species_name": "bad"},
])
def test_result_contract_rejected(change):
    with pytest.raises(ValueError):
        normalize([row() | change])


def test_birdnet_adapter_uses_verified_contract(monkeypatch):
    result = SimpleNamespace(unprocessable_inputs=[], to_structured_array=lambda: [row()])
    model = Mock()
    model.predict.return_value = result
    load = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "birdnet", SimpleNamespace(load=load))
    analyzer = BirdNETFileAnalyzer()
    assert len(analyzer.analyze(Path("clip.wav"), overlap=1.5)) == 1
    load.assert_called_once_with("acoustic", "3.0", "onnx", precision="fp32")
    kwargs = model.predict.call_args.kwargs
    assert kwargs["default_confidence_threshold"] == 0
    assert kwargs["overlap_duration_s"] == 1.5
    assert kwargs["n_workers"] == 1
    result.unprocessable_inputs = [0]
    with pytest.raises(RuntimeError):
        analyzer.analyze(Path("clip.wav"))


@pytest.mark.parametrize("args", [
    ["live"], ["capture"], ["live", "--device", "x", "--threshold", "nan"],
    ["analyze", "x.wav", "--overlap", "3"],
    ["capture", "--device", "x", "--duration", "0"],
    ["analyze", "x.wav", "--started-at", "2026-09-30T12:00:00"],
])
def test_cli_validation(args):
    with pytest.raises(SystemExit):
        parser().parse_args(args)


def test_low_scores_still_displayed(capsys):
    show_results(normalize([row(score=0.1)]), 0.6)
    output = capsys.readouterr().out
    assert "0.1000" in output and "0 vensterresultaten" in output


def test_normal_ctrl_c_no_traceback(monkeypatch, capsys, tmp_path):
    path = tmp_path / "test.wav"
    wav(path)
    monkeypatch.setattr("detector.cli.BirdNETFileAnalyzer", Mock(side_effect=KeyboardInterrupt()))
    assert main(["analyze", str(path)]) == 130
    result = capsys.readouterr()
    assert "gestopt" in result.out and "Traceback" not in result.err
