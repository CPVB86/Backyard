# System status for WordPress

GET /api/system/status uses the existing Authorization: Bearer token.
No new credentials, table, timer, audio probe or runtime dependency is needed.
The API checks its database and sequentially reads two configured LAN health
URLs (2 second socket timeout each, 8 KiB response limit, no proxy, redirect
or Authorization forwarding). Configure only administrator-controlled URLs,
both pointing to the same BYRemote station. Never publish remote ports online.

Example response:

```json
{
  "schema_version": 1,
  "checked_at": "2026-11-09T16:00:00+00:00",
  "components": {
    "backyard": {"status":"online","online":true,"reason":"api_database_ready","last_audio_at":null},
    "backyard_remote": {"status":"online","online":true,"reason":"remote_responding","last_audio_at":null},
    "bird_mic": {"status":"online","online":true,"reason":"audio_transmitting","last_audio_at":"2026-11-09T15:59:59+00:00"},
    "bat_mic": {"status":"unknown","online":null,"reason":"no_active_receiver","last_audio_at":null}
  }
}
```

Stable keys: backyard, backyard_remote, bird_mic, bat_mic.
status is online/offline/unknown; online is true/false/null. Never cast null
to false. checked_at is UTC and indicates completion of the server checks.
Cache-Control: no-store; WP may deliberately share a short 30 second cache
across widgets and its backend card. Expire positive results after 60 seconds.

Backyard means API + database; it does not mean inference/bridges are healthy.
Remote means at least one configured HTTP service responded. Both transport
checks failing means offline from the Pi 5's perspective, not proven power-off.
One missing configuration and one failure means unknown. An older remote with
HTTP 404 is reachable but microphone status is unknown.
Mic online means active transmission from its existing capture service with
a successful stream write in the last 15 seconds (monotonic local clock).
Capture failure or stalled active transmission is offline. No active receiver,
initial capture startup, missing config, unsupported/invalid response or
unreachable remote produces unknown. Silence still contains audio data.
This is not proof of physical microphone quality, species identity, inference,
or observation import. No detector policy changes.

If WP cannot reach this endpoint, show Backyard offline for a transport failure
and the other components unknown. For 401/403/404 or invalid JSON show unknown
with configuration/error explanation. Never keep stale green indicators.

## Pi 3B / BYRemote update

The remote source currently lives in CPVB86/AvianVisitors, remote_capture/stream.py.
Use the version containing GET /health (not the old HEAD audio endpoint).
On BYRemote, with the updated remote_capture directory copied there:

```sh
sudo sh remote_capture/install.sh
curl --fail http://127.0.0.1:8085/health
curl --fail http://127.0.0.1:8086/health
```

Installer preserves /etc/byremote/{birds,bats}.conf but restarts both capture
services: brief audio interruption, existing Go HTTP clients must reconnect.
If BIND is a specific LAN address, substitute that address for 127.0.0.1.
The health route never acquires the capture lock or starts a second recorder.

Alternative for an existing installation, download the committed stream.py to
a temporary file, inspect it, then install it as /opt/byremote/stream.py and
restart byremote-audio@birds and byremote-audio@bats. Do not replace live files
with an unchecked download.

## Pi 5 / Backyard update

Check git status -sb first. The production Pi has previously used
codex/audio-retention with a local merge of main. Preserve that branch:
git fetch origin; git merge --no-edit origin/main. Do not reset or switch
to an older local main; stop on conflicts.

Add to /etc/backyard/backyard.env using the same remote host as Go's existing
birds.flac / bats.flac URLs (replace BYREMOTE_LAN_IP):

```sh
BACKYARD_REMOTE_BIRD_HEALTH_URL=http://BYREMOTE_LAN_IP:8085/health
BACKYARD_REMOTE_BAT_HEALTH_URL=http://BYREMOTE_LAN_IP:8086/health
```

Restart only backyard-api.service. No model, mic or bridge settings change.
Check health URLs directly from Pi 5 first. Then fetch /api/system/status with
the existing Bearer authentication. Do not paste environment/token values.

## WP implementation handoff

Reuse the existing server-side authenticated API client. Provide ONE Elementor
status widget with a component selector containing the four stable keys, so
each instance renders one status. Backend Home: one card with all four rows.
Share one cached request, not four polling requests. Green/red/grey plus
accessible text; optional label override and last-check time. No secrets, LAN
URLs or direct BYRemote requests in the browser. No capture/restart/generation
actions from this read-only widget. Preserve Birds/Bats reviews and counters.

Tests: all three states, partial outage, stale cache, API authentication/error,
invalid response, four independent widget instances and combined Home card.
Do not describe microphone online as confirmed BirdNET inference.

Local automated tests mock capture/provider inputs; Pi hardware and reconnect
behavior still need deployment verification.
