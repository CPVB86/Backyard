"""Bounded derived audio previews. Original evidence is never modified."""
import base64
from datetime import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import threading
import time
import wave
from fastapi import HTTPException
from PIL import Image
from app.modules.birds.storage import audio_path

LOCK = threading.Lock()
VERSION = "1"
MAX_SOURCE = 8 * 1024 * 1024
MAX_CACHE = 128 * 1024 * 1024


def derive(data, domain):
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate, count = wav.getframerate(), wav.getnframes()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE" or count < 1:
            raise HTTPException(422, "Preview requires original mono PCM16 audio")
        samples = np.frombuffer(wav.readframes(count), dtype="<i2").astype(np.float64)
    if len(samples) != count or count / rate > 60:
        raise HTTPException(422, "Invalid audio length")
    factor = 10 if domain == "bat" else 1
    if rate % factor:
        raise HTTPException(422, "Unsupported time expansion sample rate")
    peak = float(np.max(np.abs(samples))) / 32768
    gain = min(16, .89 / peak) if peak else 1
    # Never attenuate a quiet recording below unity; cap amplification at 24 dB.
    output = np.rint(samples * gain).astype("<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate // factor)
        wav.writeframes(output.tobytes())
    width, height = 256, 256
    n = 2048 if domain == "bat" else 1024
    window = np.hanning(n)
    padded = np.pad(samples / 32768, (n//2, n))
    spectrum = np.zeros((width, height))
    # Scan the ENTIRE clip at 50% overlap; max-pool to screen columns so short
    # ultrasonic pulses between display pixels are not skipped.
    for center in range(0, count + n//2, n//2):
        bins = np.abs(np.fft.rfft(padded[center:center+n] * window))[:n//2] * 2 / window.sum()
        column = min(width-1, center * width // count)
        pooled = bins.reshape(height, -1).max(axis=1)
        end_column = min(width, max(column + 1, (center + n//2) * width // count))
        spectrum[column:end_column] = np.maximum(spectrum[column:end_column], pooled)
    intensity = np.clip((20*np.log10(np.maximum(spectrum, 1e-12))+90)/90, 0, 1).T[::-1]
    rgb = np.stack((255*intensity, 230*intensity**2, 180*intensity**4), axis=-1).astype("uint8")
    image = Image.fromarray(rgb)
    envelope = []
    for x in range(width):
        lo, hi = x * count // width, min(count, max(x * count // width + 1, (x+1) * count // width))
        envelope.append(round(float(np.max(np.abs(samples[lo:hi])))/32768,4) if hi>lo else 0)
    png = io.BytesIO()
    image.save(png, format="PNG")
    return target.getvalue(), dict(
        duration_seconds=count / rate, sample_rate=rate, time_expansion=factor,
        playback_duration_seconds=count / rate * factor,
        gain_db=round(20 * math.log10(gain), 2), peak_dbfs=round(20*math.log10(peak), 2) if peak else None,
        spectrogram_png=base64.b64encode(png.getvalue()).decode(),
        frequency_max_hz=rate / 2, spectrogram_dbfs_min=-90, waveform=envelope)


def preview(record, root):
    if not record.audio or record.evidence_kind == "deleted" or not record.storage_key:
        raise HTTPException(404, "Evidence unavailable")
    source = audio_path(root, record.storage_key)
    if not source.is_file():
        raise HTTPException(404, "Evidence unavailable")
    if source.stat().st_size > MAX_SOURCE:
        raise HTTPException(413, "Audio too large for interactive preview")
    data = source.read_bytes()
    if len(data) > MAX_SOURCE:
        raise HTTPException(413, "Audio too large for interactive preview")
    key = hashlib.sha256(VERSION.encode() + record.domain.encode() + data).hexdigest()
    cache = root / "audio-previews"
    # Deployed API has one worker; serialize cold preview work and cache pruning.
    with LOCK:
        cache.mkdir(exist_ok=True)
        meta, wav = cache / (key + ".json"), cache / (key + ".wav")
        if not meta.exists() or not wav.exists():
            audio, value = derive(data, record.domain)
            wav_tmp = wav.with_suffix(".wav.tmp")
            meta_tmp = meta.with_suffix(".json.tmp")
            wav_tmp.write_bytes(audio)
            meta_tmp.write_text(json.dumps(value))
            wav_tmp.replace(wav)
            meta_tmp.replace(meta)
        value = json.loads(meta.read_text())
        now = time.time()
        import os
        os.utime(meta, (now, now)); os.utime(wav, (now, now))
        entries = sorted(cache.glob("*"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in entries if p.is_file())
        for path in entries:
            if total <= MAX_CACHE:
                break
            if path not in (meta, wav) and path.is_file():
                total -= path.stat().st_size
                path.unlink()
        # Read while holding the lock so another request cannot evict this file.
        rendered = wav.read_bytes()
    clip_start = datetime.fromisoformat(record.clip["start_at"])
    start = max(0, (record.start_at - clip_start).total_seconds())
    end = min(value["duration_seconds"], (record.end_at - clip_start).total_seconds())
    value["detection_window"] = ({"start_seconds":start, "end_seconds":end,
                                  "kind":"analysis_window", "exact_call_location":False}
                                 if end > start else None)
    value["observation_id"] = record.id
    value["domain"] = record.domain
    return value, rendered
