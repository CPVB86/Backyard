# Vleermic -> BattyBirdNET -> Backyard

## What was missing

The existing `birdnet==1.1.1` Python library is not a newer version of
BirdNET-Go. These are different products. Backyard's continuous bird monitor
loads acoustic v3 at 32 kHz model input, and deliberately excludes bat taxonomy.
An ultrasonic USB mic alone cannot make that bird model a bat classifier.

Keep `backyard-detector.service` on its existing microphone. Use a separate
BirdNET-Go **20261014** instance with the **BattyBirdNET Europe** classifier for
Vleermic capture and inference. The new `backyard-bats.service` imports only
explicit `modelType=bat` records through the existing authenticated observation
POST / audio PUT routes. No BirdNET-Go database is opened by Backyard.

Already present: bat species catalog, domain-separated observations, audio,
confirm/reject, species/Atlas and shared presentation APIs. Added: original
ultrasound acceptance up to 384 kHz, capture diagnostic support, bounded bat
HTTP producer, retry checkpoint and optional operations reporting.

## Important acceptance boundary

There is no validated bat geo/season provider in this repository. Regional model
membership does NOT establish local seasonal plausibility. The bird geomodel
must not be reused for bats. Imported records therefore remain `unknown` under
existing policy, including high confidence. Their original audio is retained;
confirm/reject use existing routes. Accepted totals/Atlas change only after
confirmation. `/api/observations/review` deliberately does not turn `unknown`
into human-review work. Inspect `/api/observations?domain=bat` to manage these
initial detections. This change does not alter policy or the WordPress UI.

Go exposes aggregate events, not all inference windows. Each event is one
candidate; metadata explicitly says `aggregate_detector_event` and leaves
`inference_window_count=null`. Supports must not be read as independent calls.
`unlikely` is preserved as upstream evidence, not translated into geographic
`unusual`. Original begin/end, confidence, species and upstream detection fields survive.
Mutable Go review/weather/novelty/display fields are excluded so list and detail
responses produce the same idempotent payload.

## 1. Identify and test the USB microphone (Pi)

Connect the Vleermic. Run:

```bash
cd /home/cpvb86/Backyard
git pull --ff-only
sudo apt-get update
sudo apt-get install -y alsa-utils
arecord -l
cat /proc/asound/cards
arecord -L
```

Choose the **Vleermic** card, not the current bird microphone. Use its stable
ALSA CARD name, not a numeric card index. Replace the one placeholder below:

```bash
BAT_DEVICE='hw:CARD=REPLACE_WITH_VLEERMIC_CARD,DEV=0'
arecord -D "$BAT_DEVICE" --dump-hw-params -t raw -f S16_LE -r 384000 -c 1 -d 1 /dev/null
.venv/bin/python -m detector.cli capture --device "$BAT_DEVICE" --rate 384000 --duration 6 --replace
```

Expected: mono PCM16, **384000 Hz**, approximately six seconds and nonzero RMS.
This overwrites only `.detector-test/capture.wav`, the existing diagnostic file.
If native hardware rejects this rate/format, stop and inspect the hardware
parameters; do not use `plughw` to conceal resampling or guess 256 kHz support.
A quiet recording is not proof of bat inference. The Python `analyze/live` bird
commands do not analyze bats; this command tests capture only.

## 2. Dedicated Go runtime (first installation)

These steps create a NEW local-only container, not a replacement bird service.
If a BirdNET-Go installation already exists, inspect it first rather than
creating another process that opens the same Vleermic.

```bash
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
mkdir -p "$HOME/backyard-bats-go/config" "$HOME/backyard-bats-go/data"
test ! -e "$HOME/backyard-bats-go/config/config.yaml" && \
  cp deploy/birdnet-go-bats.yaml.example "$HOME/backyard-bats-go/config/config.yaml"
nano "$HOME/backyard-bats-go/config/config.yaml"
```

Replace `device` with the tested `$BAT_DEVICE`. Check coordinates (the example
uses the supplied garden coordinates). Keep WAV, mono, no normalization,
export length=6, precapture=0, extended capture disabled. These settings keep
native ultrasound, preserve verified clip timing, and fit Backyard's existing
9-second event / 8 MiB audio limits. Do not change them during the first test.
The model uses its own upstream signal preparation; do not manually slow down
or downsample the recording sent to inference.

```bash
sudo docker pull ghcr.io/tphakala/birdnet-go:20261014
sudo docker run -d --name backyard-bats-go --restart unless-stopped \
  --device /dev/snd:/dev/snd \
  -p 127.0.0.1:8081:8080 \
  -e TZ=Europe/Amsterdam -e BIRDNET_UID="$(id -u)" -e BIRDNET_GID="$(id -g)" \
  -v "$HOME/backyard-bats-go/config:/config" \
  -v "$HOME/backyard-bats-go/data:/data" \
  --log-opt max-size=10m --log-opt max-file=3 \
  ghcr.io/tphakala/birdnet-go:20261014
sudo docker logs --tail 100 backyard-bats-go
```

From Windows PowerShell, open a tunnel and leave it running:

```powershell
ssh -N -L 8081:127.0.0.1:8081 cpvb86@192.168.1.31
```

Open http://127.0.0.1:8081 in that computer's browser. Complete setup, install
**BattyBirdNET Europe** from the model gallery and enable the bat model. Set
Vleermic's analysis target to **bat only**; disable the unused bird model in
this Go instance. Keep the existing Backyard Python bird service running.
Check that the source reports native 384 kHz and the bat model is loaded.
Temporarily disable `nighttimeonly` for a daytime inference check, then restore
it for night operation. Night mode uses the configured location/timezone.
Do not expose this container port to the LAN/internet: its API is only consumed
locally, and the SSH tunnel provides administrative access. Backyard's own
Bearer authentication remains unchanged. Never put its token in Go settings.

## 3. Backyard bridge and services

No new Python dependencies or DB migration. On the Pi:

```bash
cd /home/cpvb86/Backyard
sudoedit /etc/backyard/backyard.env
```

Add the following, retaining the existing token, policy and bird settings:

```dotenv
BACKYARD_BIRDNET_GO_URL=http://127.0.0.1:8081
BACKYARD_BAT_INSTANCE=pi-bats-1
BACKYARD_BAT_STATE=/home/cpvb86/Backyard/data/bat-bridge.json
```

Keep the instance ID stable. A replaced/reset Go database requires a new ID
and new checkpoint filename so reused upstream numeric IDs cannot collide.
The bridge reuses `BACKYARD_API_TOKEN` and `BACKYARD_MONITOR_API_URL`.

```bash
sudo systemctl restart backyard-api.service
sudo install -m 644 deploy/systemd/backyard-bats.service /etc/systemd/system/backyard-bats.service
sudo systemctl daemon-reload
sudo systemd-run --wait --pipe --collect --uid=cpvb86 \
  --working-directory=/home/cpvb86/Backyard \
  --property=EnvironmentFile=/etc/backyard/backyard.env \
  /home/cpvb86/Backyard/.venv/bin/python -m detector.bat_bridge --check
sudo systemctl enable --now backyard-bats.service
sudo systemctl status backyard-api backyard-detector backyard-bats --no-pager
sudo journalctl -u backyard-bats -n 50 --no-pager
```

`--check` proves HTTP and policy connectivity only. It explicitly reports
`capture_and_inference_verified=false`. No synthetic production observations
are created. A running bridge with zero imports is not evidence that the
microphone/model is working.

## 4. Verify a real detection end to end

Watch both processes:

```bash
sudo docker logs --since 10m -f backyard-bats-go
# Another SSH terminal:
sudo journalctl -u backyard-bats -f
```

Go must show actual processed windows on the Vleermic source, without capture
or inference errors. Once an actual bat event exists, the bridge should log
`bat_imported` with its upstream ID and Backyard observation ID. Inspect saved
records without printing the API token:

```bash
sudo systemd-run --wait --pipe --collect --uid=cpvb86 \
  --working-directory=/home/cpvb86/Backyard \
  --property=EnvironmentFile=/etc/backyard/backyard.env \
  /home/cpvb86/Backyard/.venv/bin/python -c 'import json,os; from detector.monitor_http import HTTPTransport; c=HTTPTransport(os.environ.get("BACKYARD_MONITOR_API_URL","http://127.0.0.1:8010"),5,max_response=524288); print(json.dumps(c.request("GET","/api/observations?domain=bat&limit=5",None,"application/json"),indent=2))'
sudo .venv/bin/python -m operations.status --hours 1
```

Check `domain=bat`, species, timestamp, `classification=unknown`,
`audio_available=true`, and the original sample rate in `clip`/`audio`.
Download audio using the returned authenticated `audio_url`; confirm/reject
via the existing observation API if desired. Original ultrasound will not
sound like ordinary audible audio played at normal speed. Go offers separate
bat playback/spectrogram tools; Backyard preserves the evidence unchanged.

Optional one-event retry/test (replace 42 with a **real Go bat detection ID**):

```bash
sudo systemctl stop backyard-bats
sudo systemd-run --wait --pipe --collect --uid=cpvb86 \
  --working-directory=/home/cpvb86/Backyard \
  --property=EnvironmentFile=/etc/backyard/backyard.env \
  /home/cpvb86/Backyard/.venv/bin/python -m detector.bat_bridge --detection-id 42
sudo systemctl start backyard-bats
```

## Recovery, bounds and limitations

The bridge polls every 30 seconds, paginates the whole current day and catches
up one historical day per iteration. Checkpoints are atomically replaced;
only one process can hold the bridge lock. The exact pending POST is frozen
before sending, including its policy fingerprint and audio SHA256. Repeated
IDs use existing Backyard idempotency; changed audio during retry is rejected.
Audio uploads happen before an ID is marked complete. Existing confirmed or
rejected observations are never intentionally revised by this producer.

Memory: one <=8 MiB WAV and one <=2 MiB API page. No capture spool or second
inference queue. Checkpoint: <=10,000 completed bat IDs per day plus one pending
request. >10,000 upstream records/day causes a visible error, not silent
truncation. Go handles continuous capture, bounded buffers and its own inference
backpressure. Upstream clip retention is bounded by disk usage. If imports
are blocked, act before upstream retention removes the original clips.

The previous day is finalized after 00:10 local time. Imports inserted into
older days after that require an explicit replay using `--from-date YYYY-MM-DD`
and a different `--state data/bat-replay.json` while the normal bridge is stopped.
This reuses event IDs and does not intentionally duplicate observations.
`--from-date` initializes a NEW checkpoint only; it never rewinds an existing
checkpoint. Keep both Pi and container timezone Europe/Amsterdam.

A malformed/unsupported event, missing clip or changed evidence deliberately
blocks that day's cursor and is visible as `bat_bridge_error` / retrying status.
Inspect Go's original event and clip; do not delete the checkpoint to conceal
failure. Original event metadata and export must remain stable for retries. A replay
after a policy or upstream evidence change can return 409; inspect it instead
of assigning a new event ID to the same detection.
The Go export interval is an aggregate event interval, not a precisely isolated
individual call. This integration does not add acoustic call segmentation.

This was tested without the physical mic or a live Go container. Hardware,
model download, actual model accuracy, concurrent Pi CPU/RAM load and overnight
capture need the Pi checks above. Recording first bats tonight depends on those
checks succeeding and bats being present.

## Upstream sources checked

- https://github.com/tphakala/birdnet-go/releases/tag/20261014
- https://birdnet-team.github.io/birdnet/models.html
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/classifier/model_registry.go
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/api/v2/detections/detections.go
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/analysis/processor/processor.go
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/conf/capture_window.go
- https://github.com/tphakala/birdnet-go/blob/20261014/config.schema.json
- https://www.veldshop.nl/nl/vleermic-384khz-ultrasone-microfoon.html
