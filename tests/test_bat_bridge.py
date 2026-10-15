"""Contract fixtures match BirdNET-Go 20261014's v2 detection response."""
import io
import json
from unittest.mock import Mock
import wave

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from detector.bat_bridge import Bridge, GoClient, payload_for
from detector.audio import inspect_audio
from observations.policy import Policy


def audio(rate=384000, duration=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b'\x01\x00' * int(rate * duration))
    return output.getvalue()


def record(identity=42, **changes):
    return dict(id=identity, modelType='bat', scientificName='Pipistrellus pipistrellus',
                commonName='Common Pipistrelle', confidence=.96,
                beginTime='2026-10-15T20:00:00+02:00',
                endTime='2026-10-15T20:00:01+02:00', clipName='test.wav', **changes)


@pytest.mark.parametrize('rate', [96000, 192000, 256000, 384000])
def test_ultrasound_preserves_samples_timing_unknown_and_single_aggregate(rate, tmp_path):
    wav = audio(rate)
    p = payload_for(record(), wav, Policy(), 'pi-test')
    candidate = p['candidates'][0]
    assert candidate['sample_rate'] == rate
    assert candidate['end_sample'] == p['clip_end_sample'] == rate
    assert candidate['utc_anchor'] == '2026-10-15T18:00:00+00:00'
    assert candidate['plausibility']['state'] == 'unknown'
    assert candidate['metadata']['inference_window_count'] is None
    assert candidate['metadata']['upstream_detection'] == record()
    assert p == payload_for(record(), wav, Policy(), 'pi-test')
    assert p['event_id'] != payload_for(record(), wav, Policy(), 'other-instance')['event_id']
    path = tmp_path / 'bat.wav'
    path.write_bytes(wav)
    assert inspect_audio(path).rate == rate


@pytest.mark.parametrize('change', ['bird', 'rate', 'timestamp', 'duration', 'truncated'])
def test_bad_or_non_ultrasonic_upstream_evidence_is_not_ingested(change):
    item, wav = record(), audio()
    if change == 'bird': item['modelType'] = 'bird'
    if change == 'rate': wav = audio(48000)
    if change == 'timestamp': item['beginTime'] = '2026-10-15T20:00:00'
    if change == 'duration': item['endTime'] = '2026-10-15T20:00:03+02:00'
    if change == 'truncated': wav = wav[:-100]
    with pytest.raises(ValueError):
        payload_for(item, wav, Policy(), 'pi-test')


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / 'db.sqlite3', storage_root=tmp_path / 'audio')
    with TestClient(create_app(settings), headers={'Authorization': 'Bearer backyard-test-token'}) as client:
        yield client


class Transport:
    def __init__(self, api):
        self.api = api
        self.fail_upload = False

    def request(self, method, path, body, content_type):
        if method == 'PUT' and self.fail_upload:
            raise OSError('synthetic network interruption')
        result = self.api.request(method, path, content=body, headers={'Content-Type': content_type})
        assert result.status_code in (200, 201), result.text
        return result.json()


def test_real_api_auth_retry_restart_review_audio_and_no_duplicate(api, tmp_path):
    wav = audio()
    upstream = Mock()
    upstream.audio.return_value = wav
    upstream.records.return_value = [record(), record()]
    downstream = Transport(api)
    state = tmp_path / 'bridge.json'
    bridge = Bridge(upstream, downstream, Policy(), state, 'pi-test', '2026-10-15')
    downstream.fail_upload = True
    with pytest.raises(OSError):
        bridge.once('2026-10-15')
    assert json.loads(state.read_text())['pending']
    assert len(api.get('/api/observations?domain=bat').json()) == 1
    downstream.fail_upload = False
    bridge = Bridge(upstream, downstream, Policy(), state, 'pi-test', '2026-10-15')
    assert bridge.once('2026-10-15') == 1
    assert bridge.once('2026-10-15') == 0
    items = api.get('/api/observations?domain=bat').json()
    assert len(items) == 1
    item = items[0]
    assert item['classification'] == 'unknown'
    assert item['supports'] == 1 and item['audio_available']
    assert api.get(item['audio_url']).content == wav
    assert api.get(item['audio_url'], headers={'Authorization': 'Bearer wrong'}).status_code == 401
    result = api.post(f"/api/observations/{item['id']}/confirm", json={'expected_status': item['status']})
    assert result.status_code == 200
    assert result.json()['status'] == 'human_confirmed'
    assert api.get(item['audio_url']).content == wav
    assert len(list((tmp_path / 'audio').rglob('*.wav'))) == 1
    assert api.get('/api/observations?domain=bird').json() == []


def test_changed_audio_on_retry_fails_closed(api, tmp_path):
    upstream, downstream = Mock(), Transport(api)
    upstream.audio.return_value = audio()
    downstream.fail_upload = True
    bridge = Bridge(upstream, downstream, Policy(), tmp_path / 'state.json', 'test', '2026-10-15')
    with pytest.raises(OSError): bridge.deliver(record())
    upstream.audio.return_value = audio(192000)
    with pytest.raises(ValueError, match='changed'): bridge.deliver(record())


def test_skip_birds_and_complete_daily_checkpoint(tmp_path):
    upstream, downstream = Mock(), Mock()
    bird = record(); bird['modelType'] = 'bird'
    upstream.records.return_value = [bird]
    bridge = Bridge(upstream, downstream, Policy(), tmp_path / 'state.json', 'test', '2026-10-14')
    assert bridge.once('2026-10-15') == 0
    assert bridge.state['date'] == '2026-10-15'
    upstream.audio.assert_not_called()
    downstream.request.assert_not_called()


def test_pagination_does_not_only_read_recent_detections():
    client = GoClient('http://127.0.0.1:8081')
    client.get = Mock(side_effect=[{'data': [record(i+1) for i in range(100)], 'total': 101},
                                  {'data': [record(101)], 'total': 101}])
    assert len(list(client.records('2026-10-15'))) == 101
    assert 'offset=100' in client.get.call_args.args[0]


@pytest.mark.parametrize('url', ['http://example.com', 'http://token@localhost:8081', 'http://localhost:8081/path'])
def test_go_client_only_uses_local_origin(url):
    with pytest.raises(ValueError): GoClient(url)


def test_operational_status_exposes_no_payload_or_credentials(tmp_path):
    from operations.bats import bridge_status
    state = tmp_path / 'bridge.json'
    state.write_text(json.dumps({'date': '2026-10-15', 'done': ['42'],
        'pending': {'record': {'private_field': 'do-not-print'}},
        'phase': 'retrying', 'last_poll_at': 100}), encoding='utf-8')
    env = {'BACKYARD_BAT_STATE': str(state)}
    report = bridge_status(env, 300)
    assert report['stale'] and report['pending_upload']
    assert 'do-not-print' not in json.dumps(report)
    assert report['completed_on_scan_date'] == 1


def test_changed_instance_never_reuses_checkpoint(tmp_path):
    state = tmp_path / 'bridge.json'
    state.write_text(json.dumps({'instance': 'original', 'date': '2026-10-15', 'done': [], 'pending': None}))
    with pytest.raises(ValueError, match='Instance'):
        Bridge(Mock(), Mock(), Policy(), state, 'new-database', '2026-10-15')


def test_detail_and_list_fields_produce_same_ingest_payload():
    item = record()
    detail = dict(item, weather={'temperature': 12}, verified='correct',
                  isNewSpecies=True, comments=[{'entry': 'changed later'}])
    assert payload_for(item, audio(), Policy(), 'test') == payload_for(detail, audio(), Policy(), 'test')


def test_go_minimum_export_length_fits_existing_ingest_limits(api):
    from pathlib import Path
    example = Path('deploy/birdnet-go-bats.yaml.example').read_text(encoding='utf-8')
    assert 'length: 10' in example and 'precapture: 4' in example
    item = record()
    item['endTime'] = '2026-10-15T20:00:06+02:00'
    wav = audio(384000, 10)
    payload = payload_for(item, wav, Policy(), 'export-test')
    assert payload['candidates'][0]['end_sample'] == 6 * 384000
    assert payload['clip_end_sample'] == 10 * 384000
    response = api.post('/api/observations', json=payload)
    assert response.status_code == 201, response.text
    identity = response.json()['id']
    uploaded = api.put(f'/api/observations/{identity}/audio', content=wav,
                       headers={'Content-Type': 'audio/wav'})
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()['audio']['duration_seconds'] == 10
