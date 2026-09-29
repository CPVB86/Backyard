"""DIAGNOSTIC ONLY: sequential live mode has blind intervals during inference."""
import argparse
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import wave

from detector.audio import capture, describe, inspect_audio
from detector.birdnet_adapter import BirdNETFileAnalyzer

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".detector-test"


def bounded_float(low, high, *, inclusive=True):
    def parse(value):
        number = float(value)
        if not math.isfinite(number) or number < low or (number > high if inclusive else number >= high):
            raise argparse.ArgumentTypeError(f"Waarde buiten bereik {low}..{high}")
        return number
    return parse


def positive_duration(value):
    number = int(value)
    if not 1 <= number <= 30:
        raise argparse.ArgumentTypeError("Gebruik 1-30 seconden")
    return number


def aware_time(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("Tijdzone vereist")
    return stamp.astimezone(timezone.utc)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    cap = commands.add_parser("capture", help="Een korte ALSA-opname; geen BirdNET nodig")
    cap.add_argument("--replace", action="store_true", help="Vervang alleen .detector-test/capture.wav")
    analyze = commands.add_parser("analyze", help="Analyseer eerst een bestaand WAV-bestand")
    analyze.add_argument("file", type=Path)
    analyze.add_argument("--started-at", type=aware_time, help="Bekende opname-start, ISO8601 met tijdzone")
    live = commands.add_parser("live", help="DIAGNOSTISCH: sequentieel, niet geschikt voor 24/7")
    live.add_argument("--pause", type=bounded_float(0, 60), default=0.5)
    for command in (cap, live):
        command.add_argument("--device", required=True, help="Exacte ALSA-naam uit arecord -L")
        command.add_argument("--rate", type=int, choices=(8000, 16000, 32000, 44100, 48000, 96000), default=48000)
        command.add_argument("--channels", type=int, choices=(1, 2), default=1)
        command.add_argument("--duration", type=positive_duration, default=6)
    for command in (analyze, live):
        command.add_argument("--threshold", type=bounded_float(0, 1), default=0.60)
        command.add_argument("--top-k", type=int, choices=range(1, 21), default=5)
        command.add_argument("--overlap", type=bounded_float(0, 3, inclusive=False), default=0.0,
                             help="Overlap binnen bestand, seconden; modelvenster is 3s")
    return root


def show_results(rows, threshold, started_at=None):
    if not rows:
        print("Geen resultaten teruggegeven; controleer audio/model.")
    accepted = 0
    for row in rows:
        passed = row.confidence >= threshold
        accepted += passed
        absolute = ""
        if started_at is not None:
            absolute = " | geschatte UTC " + (started_at + timedelta(seconds=row.start_seconds)).isoformat()
        print(f"{'*' if passed else '-'} [{row.start_seconds:.3f}..{row.end_seconds:.3f}s] "
              f"{row.scientific_name} / {row.common_name} | {row.confidence:.4f}{absolute}")
    print(f"{accepted} vensterresultaten >= {threshold:.2f}; geen overlap-deduplicatie.")


def analyze_file(analyzer, path, args, started_at=None):
    info = inspect_audio(path)
    print(f"BirdNET analyseert: {path}\n{describe(info)}")
    clock = time.monotonic()
    rows = analyzer.analyze(path, top_k=args.top_k, overlap=args.overlap)
    elapsed = time.monotonic() - clock
    show_results(rows, args.threshold, started_at)
    print(f"Analyse: {elapsed:.2f}s; bestandduur: {info.duration:.2f}s; "
          f"verhouding analyse/audio: {elapsed / info.duration:.2f} (diagnostiek, geen realtime-garantie)")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "capture":
            SCRATCH.mkdir(exist_ok=True)
            path = SCRATCH / "capture.wav"
            if args.replace:
                path.unlink(missing_ok=True)
            info, start, elapsed = capture(path, device=args.device, rate=args.rate,
                                          channels=args.channels, duration=args.duration)
            print(f"Opgeslagen: {path}\n{describe(info)}")
            print(f"Geschatte start UTC: {start.isoformat()} | opnameproces {elapsed:.2f}s")
            print("Niveaucontrole is geen herkenningstest; nul/zeer laag niveau eerst onderzoeken.")
            return 0
        if args.command == "analyze":
            inspect_audio(args.file)  # Fail before downloading/loading a model.
        os.environ.setdefault("BIRDNET_APP_DATA", str(SCRATCH / "model-cache"))
        print("Laden: BirdNET 1.1.1 acoustic 3.0 preview, ONNX FP32, CPU, 1 worker.")
        print("Eerste keer: model/labels downloaden; wacht op afronding.")
        analyzer = BirdNETFileAnalyzer()
        if args.command == "analyze":
            analyze_file(analyzer, args.file, args, args.started_at)
        else:
            print("DIAGNOSTISCHE LIVE-TEST: OPNAMEGATEN TIJDENS ANALYSE. NIET VOOR 24/7.")
            # One owned temporary WAV; no queue, permanent storage or background daemon.
            with tempfile.TemporaryDirectory(prefix="backyard-detector-") as directory:
                path = Path(directory) / "window.wav"
                while True:
                    info, start, _ = capture(path, device=args.device, rate=args.rate,
                                             channels=args.channels, duration=args.duration)
                    try:
                        analyze_file(analyzer, path, args, start)
                    finally:
                        path.unlink(missing_ok=True)
                    time.sleep(args.pause)
        return 0
    except KeyboardInterrupt:
        print("\nDiagnostische test gestopt.")
        return 130
    except (OSError, ValueError, RuntimeError, ImportError, wave.Error, EOFError, subprocess.SubprocessError) as error:
        print(f"Fout: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
