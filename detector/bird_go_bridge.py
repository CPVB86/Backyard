"""BirdNET-Go bird events through the existing Backyard policy/audio API.

One upstream aggregate is ONE support, never reconstructed inference windows.
Capture/inference are external; only the existing small geo model loads here.
"""
import argparse
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
from pathlib import Path
import signal
from threading import Event
import time

from detector.bat_bridge import Bridge, GoClient, payload_for, save_state
from detector.birdnet_adapter import Prediction
from detector.instance import exclusive_monitor
from detector.monitor_http import HTTPTransport
from detector.providers import AcousticTaxonomy, GeoPlausibility, geo_configuration, plausibility_at, birdnet_week
from observations.policy import Policy


class BirdAdapter:
    def __init__(self, geo, model_version):
        if not model_version or len(model_version) > 255:
            raise ValueError("Set BACKYARD_GO_BIRD_MODEL_VERSION to the actual installed Go model/version")
        self.geo, self.model_version = geo, model_version
        self.taxonomy = AcousticTaxonomy()

    def supports(self, record):
        return (record.get('modelType') == 'bird' and
                self.taxonomy.resolve(record['scientificName'])[0] == 'bird')

    def payload(self, record, wav, policy, instance):
        if not self.supports(record):
            raise ValueError("Unsupported bird taxonomy/model")
        prediction = Prediction(record['scientificName'], record['commonName'], record['confidence'], 0, 1)
        annotated = self.geo.annotate([prediction])[0]
        signal = plausibility_at(annotated.plausibility, record['beginTime'], policy)
        result = payload_for(record, wav, policy, instance, domain='bird',
                             plausibility=signal, model_version=self.model_version)
        result['candidates'][0]['metadata']['taxonomy'] = self.taxonomy.resolve(record['scientificName'])[1]
        return result

    def status(self):
        return self.geo.status | {'week': birdnet_week(datetime.now(timezone.utc).isoformat()),
                                 'week_convention': 'UTC month-quarter 1..48'}


class BirdBridge(Bridge):
    def __init__(self, *args, adapter, **kwargs):
        self.adapter = adapter
        super().__init__(*args, domain='bird', build_payload=adapter.payload,
                         runtime_status=adapter.status, **kwargs)

    def deliver(self, record):
        # BirdNET can emit mammals/human/noise labels as well as birds.
        if not self.adapter.supports(record):
            return False
        return super().deliver(record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check APIs and real geo model, without ingest')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    policy = Policy.load()
    origin = os.environ.get('BACKYARD_GO_BIRD_URL', 'http://127.0.0.1:8082')
    upstream = GoClient(origin)
    downstream = HTTPTransport(os.environ.get('BACKYARD_MONITOR_API_URL', 'http://127.0.0.1:8010'),
                               5, max_response=512 * 1024)
    remote = downstream.request('GET', '/api/observations/policy', b'', 'application/json')
    if remote['fingerprint'] != policy.fingerprint:
        raise ValueError('Backyard API/bridge policy mismatch')
    page = upstream.get('/api/v2/detections?limit=1')
    config = geo_configuration(os.environ.get('BACKYARD_MONITOR_GEOGRAPHY', '0').lower() in ('1', 'true', 'yes'),
                               os.environ.get('BACKYARD_MONITOR_LATITUDE'), os.environ.get('BACKYARD_MONITOR_LONGITUDE'))
    if config['error']:
        raise ValueError(config['error'])
    # Fail visibly rather than silently turn the bird population into unknown.
    geo = GeoPlausibility(config['latitude'], config['longitude'])
    adapter = BirdAdapter(geo, os.environ.get('BACKYARD_GO_BIRD_MODEL_VERSION'))
    if args.check:
        print(json.dumps({'api': 'ok', 'policy': 'ok', 'birdnet_go': 'ok',
                          'upstream_records': page['total'], 'geo': adapter.status(),
                          'supports_per_event': 1, 'capture_and_inference_verified': False}))
        return
    state = Path(os.environ.get('BACKYARD_GO_BIRD_STATE', 'data/bird-go-bridge.json'))
    instance = os.environ.get('BACKYARD_GO_BIRD_INSTANCE', 'pi-birds-go-1')
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Share the old monitor's lock: also protects against manual double ingestion.
    monitor_lock = os.environ.get('BACKYARD_MONITOR_LOCK_FILE', 'data/monitor.lock')
    with exclusive_monitor(monitor_lock), exclusive_monitor(str(state) + '.lock'):
        bridge = BirdBridge(upstream, downstream, policy, state, instance,
                            datetime.now().date().isoformat(), adapter=adapter,
                            not_before=datetime.now(timezone.utc).isoformat())
        # Persist the first activation boundary before any processing/restart.
        save_state(state, bridge.state)
        while not stop.is_set():
            try:
                bridge.once((datetime.now() - timedelta(minutes=10)).date().isoformat())
            except (OSError, ValueError, KeyError, RuntimeError, http.client.HTTPException) as error:
                bridge.state.update(phase='retrying', last_error_at=time.time(),
                                    last_error_type=type(error).__name__)
                save_state(state, bridge.state)
                print(json.dumps({'event': 'bird_bridge_error', 'type': type(error).__name__,
                                  'phase': 'retrying'}), flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once or stop.wait(30):
                break


if __name__ == '__main__':
    main()
