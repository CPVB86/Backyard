"""BirdNET-Go bat CLI and shared bounded bridge; capture/inference stay in Go.

No models, observation DB, species policy or audio spool in this process.
The upstream API exposes aggregate events, NOT individual inference windows.
"""
import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import signal
import time
from threading import Event
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5
import wave

from detector.instance import exclusive_monitor
from detector.monitor_http import HTTPTransport
from observations.policy import Policy, RawCandidate, validate_event

MAX_AUDIO = 8 * 1024 * 1024
MAX_EVENTS = 10000
PAGE_SIZE = 100


def utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.year < 2020:
        raise ValueError('Upstream timestamp must have a valid timezone')
    return stamp.astimezone(timezone.utc)


def payload_for(record, wav, policy, instance, *, domain="bat", plausibility=None, model_version=None):
    """Preserve upstream timing; never invent support windows or bat geography."""
    if domain not in ('bird', 'bat') or record.get('modelType') != domain:
        raise ValueError('Expected an explicit matching BirdNET-Go model type')
    identity = record['id']
    if type(identity) is not int or identity <= 0:
        raise ValueError('Invalid upstream detection ID')
    if not 44 <= len(wav) <= MAX_AUDIO:
        raise ValueError('Expected a bounded original ultrasonic WAV')
    with wave.open(io.BytesIO(wav), 'rb') as stream:
        rate, frames = stream.getframerate(), stream.getnframes()
        if (stream.getnchannels() != 1 or stream.getsampwidth() != 2
                or stream.getcomptype() != 'NONE'
                or not ((96000 <= rate <= 384000) if domain == 'bat' else rate in (32000, 44100, 48000, 96000))
                or not 0 < frames / rate <= 30):
            raise ValueError('Need original mono PCM16 WAV at a supported domain sample rate, <=30s')
        if len(stream.readframes(frames + 1)) != frames * 2:
            raise ValueError('Truncated upstream audio')
    start, end = utc(record['beginTime']), utc(record['endTime'])
    event_frames = round((end - start).total_seconds() * rate)
    if not 0 < event_frames <= frames or frames - event_frames > 10 * rate:
        raise ValueError('Upstream event/clip timing mismatch; check export settings')
    event_id = str(uuid5(NAMESPACE_URL, f'backyard:birdnet-go:{instance}:{identity}'))
    candidate = RawCandidate(
        candidate_id=event_id, window_id='aggregate:' + event_id,
        source='birdnet-go:' + instance, stream_id=event_id,
        domain=domain, scientific_name=record['scientificName'],
        common_name=record['commonName'], confidence=record['confidence'],
        sample_rate=rate, start_sample=0, end_sample=event_frames,
        utc_anchor=start.isoformat(), model_version=model_version or 'BattyBirdNET-via-BirdNET-Go',
        plausibility=plausibility if plausibility is not None else {'state': 'unknown', 'provider': 'unavailable',
                      'reason': 'bat_geo_season_provider_not_configured'},
        metadata={'upstream_detection': {key: record[key] for key in (
                      'id', 'modelType', 'scientificName', 'commonName', 'confidence',
                      'beginTime', 'endTime', 'date', 'time', 'timestamp', 'clipName', 'unlikely', 'source')
                      if key in record}, 'upstream_instance': instance,
                  'evidence_unit': 'aggregate_detector_event',
                  'inference_window_count': None,
                  'clip_start_source': 'BirdNET-Go BeginTime',
                  'ultrasonic_original': domain == 'bat'})
    validate_event([candidate], policy)
    return {'event_id': event_id, 'policy_fingerprint': policy.fingerprint,
            'candidates': [asdict(candidate)], 'clip_start_sample': 0,
            'clip_end_sample': frames}


class GoClient:
    """Local read-only connection. Never forward the Backyard Bearer token."""
    def __init__(self, origin):
        self.origin = urlsplit(origin)
        if (self.origin.scheme != 'http' or self.origin.hostname not in ('127.0.0.1', '::1', 'localhost')
                or self.origin.path not in ('', '/') or self.origin.query or self.origin.fragment
                or self.origin.username or self.origin.password):
            raise ValueError('BirdNET-Go URL must be a local HTTP origin')

    def get(self, path, *, audio=False):
        limit = MAX_AUDIO if audio else 2 * 1024 * 1024
        connection = http.client.HTTPConnection(self.origin.hostname, self.origin.port, timeout=10)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f'BirdNET-Go HTTP {response.status}')
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Oversized BirdNET-Go response')
            return data if audio else json.loads(data)
        finally:
            connection.close()

    def records(self, day):
        # Scan complete pages, not only recent N: bursts must not disappear silently.
        for offset in range(0, MAX_EVENTS, PAGE_SIZE):
            query = urlencode({'date': day, 'queryType': 'hourly', 'hour': '0',
                               'duration': 24, 'limit': PAGE_SIZE, 'offset': offset,
                               'sortBy': 'date_asc'})
            page = self.get('/api/v2/detections?' + query)
            rows = page['data']
            if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
                raise ValueError('Unexpected upstream pagination contract')
            yield from rows
            if offset + len(rows) >= page['total']:
                return
            if not rows:
                raise ValueError('Upstream pagination made no progress')
        raise ValueError('Daily backlog limit reached; operator intervention required')

    def audio(self, identity):
        return self.get(f'/api/v2/audio/{int(identity)}', audio=True)


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Bridge:
    def __init__(self, upstream, downstream, policy, state_path, instance, start_date,
                 *, domain="bat", build_payload=payload_for, runtime_status=None, not_before=None):
        if not instance or len(instance) > 100 or not all(c.isalnum() or c in '-_' for c in instance):
            raise ValueError('Use a stable instance ID of 1..100 letters/digits/-/_')
        if domain not in ("bird", "bat"):
            raise ValueError("Invalid bridge domain")
        self.domain, self.build_payload = domain, build_payload
        self.runtime_status = runtime_status
        self.upstream, self.downstream, self.policy = upstream, downstream, policy
        self.path, self.instance = Path(state_path), instance
        if self.path.exists() and self.path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Oversized bridge checkpoint')
        self.state = (json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists()
                      else {'instance': instance, 'date': start_date, 'done': [], 'pending': None,
                            'domain': domain, 'not_before': not_before})
        if self.state.get('domain', 'bat') != domain:
            raise ValueError('Domain differs from checkpoint')
        if self.state['instance'] != instance:
            raise ValueError('Instance differs from checkpoint; do not reuse upstream database IDs')
        date.fromisoformat(self.state['date'])

    def deliver(self, record):
        key = str(record['id'])
        if key in self.state['done'] or record.get('modelType') != self.domain:
            return False
        cutoff = self.state.get('not_before')
        if cutoff and utc(record['beginTime']) < utc(cutoff):
            return False
        if len(self.state['done']) >= MAX_EVENTS:
            raise ValueError('Daily checkpoint limit reached')
        wav = self.upstream.audio(record['id'])
        pending = self.state['pending']
        if pending is None:
            payload = self.build_payload(record, wav, self.policy, self.instance)
            self.state['pending'] = {
                'record': payload['candidates'][0]['metadata']['upstream_detection'],
                'payload': payload, 'sha256': hashlib.sha256(wav).hexdigest()}
            # Freeze the request BEFORE POST, also across policy changes/restarts.
            save_state(self.path, self.state)
        else:
            if pending['record']['id'] != record['id']:
                raise ValueError('Retry pending event before proceeding')
            payload = pending['payload']
            if hashlib.sha256(wav).hexdigest() != pending['sha256']:
                raise ValueError('Upstream audio changed during retry')
        result = self.downstream.request('POST', '/api/observations',
                    json.dumps(payload, allow_nan=False).encode(), 'application/json')
        if result.get('status') != 'discarded' and not result.get('audio_available'):
            self.downstream.request('PUT', f"/api/observations/{result['id']}/audio", wav, 'audio/wav')
        self.state['done'].append(key)
        self.state['pending'] = None
        save_state(self.path, self.state)
        print(json.dumps({'event': self.domain + '_imported', 'upstream_id': record['id'],
                          'observation_id': result.get('id'),
                          'classification': result.get('classification')}), flush=True)
        return True

    def once(self, today):
        count = 0
        if self.state['pending']:
            count += self.deliver(self.state['pending']['record'])
        day = self.state['date']
        for record in self.upstream.records(day):
            count += self.deliver(record)
        # Finish historical days one at a time. Leave yesterday open until 00:10
        # to accommodate asynchronous export. Explicit replay handles older imports.
        if day < today:
            self.state['date'] = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            self.state['done'] = []
            save_state(self.path, self.state)
        self.state['last_poll_at'] = time.time()
        self.state['phase'] = 'running'
        if self.runtime_status is not None:
            self.state['geo'] = self.runtime_status()
        save_state(self.path, self.state)
        print(json.dumps({'event': self.domain + '_bridge_status', 'date': day,
                          'imported': count, 'phase': 'running',
                          **({'plausibility': 'unknown', 'reason': 'bat_geo_season_provider_not_configured'}
                             if self.domain == 'bat' else {'geo': self.state.get('geo')})}), flush=True)
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--check', action='store_true', help='Read-only API/policy connectivity check')
    parser.add_argument('--detection-id', type=int, help='Import just one upstream bat detection')
    parser.add_argument('--from-date', default=date.today().isoformat(), type=date.fromisoformat)
    parser.add_argument('--state', default=os.environ.get('BACKYARD_BAT_STATE', 'data/bat-bridge.json'))
    args = parser.parse_args()
    policy = Policy.load()
    upstream = GoClient(os.environ.get('BACKYARD_BIRDNET_GO_URL', 'http://127.0.0.1:8081'))
    downstream = HTTPTransport(os.environ.get('BACKYARD_MONITOR_API_URL', 'http://127.0.0.1:8010'),
                               5, max_response=512 * 1024)
    remote_policy = downstream.request('GET', '/api/observations/policy', b'', 'application/json')
    if remote_policy['fingerprint'] != policy.fingerprint:
        raise ValueError('Backyard API/bridge policy mismatch')
    if args.check:
        page = upstream.get('/api/v2/detections?limit=1')
        print(json.dumps({'api': 'ok', 'policy': 'ok', 'birdnet_go': 'ok',
                          'upstream_records': page['total'], 'capture_and_inference_verified': False}))
        return
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with exclusive_monitor(str(args.state) + '.lock'):
        bridge = Bridge(upstream, downstream, policy, args.state,
                        os.environ.get('BACKYARD_BAT_INSTANCE', 'pi-bats-1'), args.from_date.isoformat())
        if args.detection_id:
            if bridge.state['pending']:
                bridge.deliver(bridge.state['pending']['record'])
            record = upstream.get(f'/api/v2/detections/{args.detection_id}')
            if record.get('modelType') != 'bat':
                raise ValueError('Requested detection is not a bat-model event')
            bridge.deliver(record)
            return
        while not stop.is_set():
            try:
                # Pi and Go must share the documented Europe/Amsterdam timezone.
                today = (datetime.now() - timedelta(minutes=10)).date().isoformat()
                bridge.once(today)
            except (OSError, ValueError, KeyError, RuntimeError, http.client.HTTPException) as error:
                bridge.state['phase'] = 'retrying'
                bridge.state['last_error_at'] = time.time()
                bridge.state['last_error_type'] = type(error).__name__
                save_state(bridge.path, bridge.state)
                # No URLs, credentials or full upstream bodies in journal messages.
                print(json.dumps({'event': 'bat_bridge_error', 'type': type(error).__name__,
                                  'phase': 'retrying', 'date': bridge.state['date'], 'message': str(error)[:200],
                                  'pending_id': (bridge.state.get('pending') or {}).get('record', {}).get('id')}), flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once or stop.wait(30):
                break


if __name__ == '__main__':
    main()
