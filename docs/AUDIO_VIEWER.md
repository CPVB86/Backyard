# Audio viewer (Birds and Bats)

Authenticated existing-observation routes:
- GET /api/observations/{id}/audio-view returns preview metadata and a PNG
  spectrogram encoded as base64 (spectrogram_png), waveform, duration_seconds,
  sample_rate, time_expansion, playback_duration_seconds, gain_db, peak_dbfs,
  frequency_max_hz and detection_window.
- GET /api/observations/{id}/audio-listen returns the derived PCM16 WAV.
- Existing /audio keeps returning the unchanged evidence.

Bats: samples played at one tenth the original rate, with unchanged sample count.
This is true time expansion, not resampling that would discard ultrasound.
Birds: same playback speed. Both: peak normalization to roughly -1 dBFS with
amplification capped at 16x (~24 dB); silence stays silence. No denoising or
quality/identity claim. No detector policy changes.

Spectrogram: Hann-window FFT, 256 time columns, 256 frequency rows, linear
original frequency axis, -90..0 dBFS intensity. Original mono PCM16 up to
8 MiB/60 seconds; unsupported input produces an explicit error. NumPy provides efficient full-clip FFT processing; Pillow renders the PNG.
The FFT window is 2048 samples for bats, 1024 for birds.
All audio is scanned with 50% overlap and maximum intensity pooled into display
columns, preserving brief pulses. This overview is not automatic call localization.
FFT reference: https://numpy.org/doc/stable/reference/generated/numpy.fft.rfft.html

detection_window is relative to original clip start, clamped to clip duration.
It is an analysis window, explicitly exact_call_location=false. Go aggregate
events cannot supply exact individual-call positions. Cursor original time =
playback currentTime / time_expansion. PNG is computed from ORIGINAL evidence,
before playback normalization.

Cache under storage/audio-previews keyed by source content, domain and version.
Single-worker generation lock (production API uses one worker), atomic output
publication, 128 MiB LRU file bound. Originals never altered. Every route checks
current evidence availability before reading cache; deletion revokes access.
Derived leftovers are evicted with cache pressure; no permanent duplicate store.
Preview preparation is lazy, not started for every row on page load.

## WordPress

Shared viewer in Birds/Bats admin tables, including confirmed/rejected lists.
Correction-dialog's existing raw audio link is retained for compatibility;
use the table viewer for spectrogram/time expansion.
Public: [backyard_audio module="bats" observation_id="UUID"] (birds also supported).
Builder has an Audio + spectrogram section for module and observation ID.
Only accepted observations with current audio are accessible publicly; admin
review requires capability and nonce. Every proxy request rechecks the row.
No private token, backend URL or remote station URL in browser output.
Explicit publication of a clip should be deliberate: acceptance of a species
does not itself establish whether unrelated background speech can be published.

## Deploy

On Pi 5 retain current production branch, fetch origin and merge origin/main.
Install requirements.txt in the API .venv (NumPy), then restart only
backyard-api.service after successful merge. No database migration or microphone/Go restart. Original recordings remain.
WordPress: deploy the plugin changes listed in its README. Purge JS/CSS cache.
Cold previews are more expensive than cached reads; verify responsiveness on Pi.
Tests use synthetic tones and WordPress doubles, not real ultrasound or live WP.
