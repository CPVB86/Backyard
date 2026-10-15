import json
from unittest.mock import Mock

import pytest

from detector.bird_go_bridge import BirdAdapter, BirdBridge
from detector.providers import GeoPlausibility, geo_configuration
from observations.policy import Policy
from operations.bird_source import python_fallback, switch_to_go
from test_bat_bridge import audio, Transport, api


def bird_record(identity=1, confidence=.96, name='Turdus merula'):
    return dict(id=identity, modelType='bird', scientificName=name, commonName='Common Blackbird',
                confidence=confidence, beginTime='2026-10-15T20:00:00+02:00',
                endTime='2026-10-15T20:00:06+02:00', clipName='bird.wav')


def adapter(score=.1):
    # Exercise the real existing annotation/period rules without loading ML.
    geo = object.__new__(GeoPlausibility)
    geo.latitude, geo.longitude = 51.95, 5.91
    geo.scores = {'Turdus merula': [score] * 48}
    geo.status = geo_configuration(True, geo.latitude, geo.longitude) | {'active': True, 'status': 'ready'}
    return BirdAdapter(geo, 'BirdNET-Go-20261014-BirdNET_V2.4')


@pytest.mark.parametrize('score,confidence,classification', [(.1,.96,'accepted'),(.1,.7,'low_evidence'),
                                                            (.001,.96,'human_review'),(.001,.7,'discarded')])
def test_go_bird_uses_existing_policy_geo_and_audio(api, tmp_path, score, confidence, classification):
    upstream = Mock()
    wav = audio(48000, 10)
    upstream.audio.return_value = wav
    bridge = BirdBridge(upstream, Transport(api), Policy(), tmp_path/'bird.json', 'birds',
                        '2026-10-15', adapter=adapter(score))
    assert bridge.deliver(bird_record(confidence=confidence))
    assert not bridge.deliver(bird_record(confidence=confidence))
    items = api.get('/api/observations?domain=bird').json()
    if classification == 'discarded':
        assert items == []
        return
    assert len(items) == 1
    item = items[0]
    assert item['classification'] == classification
    assert item['supports'] == 1 and item['audio_available']
    assert api.get(item['audio_url']).content == wav
    assert api.get('/api/observations?domain=bat').json() == []
    life = api.get('/api/avian-visitors/lifelist').json()['species']
    assert len(life) == (1 if classification == 'accepted' else 0)
    if life:
        assert life[0]['scientific_name'] == 'Turdus merula'


def test_retry_freezes_geo_payload_and_no_duplicate(api, tmp_path):
    upstream, downstream = Mock(), Transport(api)
    upstream.audio.return_value = audio(48000, 10)
    state = tmp_path/'bird.json'
    bridge = BirdBridge(upstream, downstream, Policy(), state, 'birds', '2026-10-15', adapter=adapter())
    downstream.fail_upload = True
    with pytest.raises(OSError): bridge.deliver(bird_record())
    frozen = json.loads(state.read_text())['pending']['payload']
    downstream.fail_upload = False
    bridge = BirdBridge(upstream, downstream, Policy(), state, 'birds', '2026-10-15', adapter=adapter(.001))
    assert bridge.deliver(bird_record())
    items = api.get('/api/observations?domain=bird').json()
    assert len(items) == 1 and items[0]['classification'] == 'accepted'
    assert frozen['candidates'][0]['plausibility']['week'] == 39


def test_aggregate_never_invents_supports_and_keeps_upstream_evidence():
    record = bird_record()
    record['count'] = 99  # Not an inference-window contract; must not become supports.
    result = adapter().payload(record, audio(48000,10), Policy(), 'birds')
    assert len(result['candidates']) == 1
    candidate = result['candidates'][0]
    assert candidate['metadata']['inference_window_count'] is None
    assert candidate['metadata']['upstream_detection']['scientificName'] == 'Turdus merula'
    assert candidate['plausibility']['state'] == 'normal'
    assert candidate['model_version'] == 'BirdNET-Go-20261014-BirdNET_V2.4'


def test_old_events_bats_and_nonbirds_not_imported(tmp_path):
    upstream, downstream = Mock(), Mock()
    bridge = BirdBridge(upstream, downstream, Policy(), tmp_path/'s.json', 'birds', '2026-10-15',
                        adapter=adapter(), not_before='2026-10-15T18:01:00+00:00')
    assert not bridge.deliver(bird_record())
    assert not bridge.deliver(dict(bird_record(), modelType='bat'))
    assert not bridge.deliver(bird_record(name='Homo sapiens'))
    upstream.audio.assert_not_called()
    downstream.request.assert_not_called()


def test_bat_checkpoint_cannot_be_reused_for_birds(tmp_path):
    state = tmp_path/'s.json'
    state.write_text(json.dumps({'instance':'birds','date':'2026-10-15','done':[],'pending':None}))
    with pytest.raises(ValueError, match='Domain'):
        BirdBridge(Mock(), Mock(), Policy(), state, 'birds','2026-10-15',adapter=adapter())


def test_fallback_stops_both_go_parts_before_starting_python():
    run=Mock(return_value='false')
    python_fallback(run)
    assert [c.args for c in run.call_args_list] == [
        ('systemctl','disable','--now','backyard-birds.service'),
        ('systemctl','disable','--now','backyard-birdnet-go.service'),
        ('docker','inspect','--format','{{.State.Running}}','backyard-birds-go'),
        ('systemctl','enable','--now','backyard-detector.service'),
        ('systemctl','is-active','--quiet','backyard-detector.service')]


def test_go_start_failure_restores_python_without_touching_bats():
    run=Mock(side_effect=lambda *args: 'false' if '{{.State.Running}}' in args else 'no')
    with pytest.raises(RuntimeError, match='Python detector restored'):
        switch_to_go({},run=run,wait_ready=Mock(side_effect=RuntimeError('not ready')))
    calls=[c.args for c in run.call_args_list]
    assert calls[-5:] == [('systemctl','disable','--now','backyard-birds.service'),
        ('systemctl','disable','--now','backyard-birdnet-go.service'),
        ('docker','inspect','--format','{{.State.Running}}','backyard-birds-go'),
        ('systemctl','enable','--now','backyard-detector.service'),
        ('systemctl','is-active','--quiet','backyard-detector.service')]
    assert not any('backyard-bats' in str(c) for c in calls)


def test_switch_success_disables_python_before_go_and_sets_boot():
    run=Mock(return_value='no')
    switch_to_go({},run=run,wait_ready=Mock())
    calls=[c.args for c in run.call_args_list]
    assert calls.index(('systemctl','disable','--now','backyard-detector.service')) < calls.index(('systemctl','start','backyard-birdnet-go.service'))
    assert calls[-1] == ('systemctl','enable','backyard-birdnet-go.service','backyard-birds.service')


def test_container_restart_policy_must_not_bypass_exclusion():
    run=Mock(return_value='unless-stopped')
    with pytest.raises(ValueError,match='restart=no'): switch_to_go({},run=run)
    assert run.call_count == 1


def test_operations_go_status_does_not_treat_stopped_python_as_failure():
    from operations.status import warnings
    services = {unit: {'ActiveState':'active', 'SubState':'running',
                      'UnitFileState':'enabled','User':'cpvb86'} for unit in
                ('backyard-api.service','backyard-birds.service','backyard-birdnet-go.service')}
    services['backyard-detector.service'] = {'ActiveState':'inactive','UnitFileState':'disabled'}
    notes = warnings({'bird_source':'go','services':services,
                      'geo':{'active':True,'status':'ready'},'birds':{'phase':'running','stale':False},
                      'audio':{'complete':True}})
    assert not any('backyard-detector.service' in n or 'No detector status' in n for n in notes)
    assert any('does not prove capture/inference' in n for n in notes)


def test_rollback_failure_is_not_reported_as_success():
    def run(*args):
        if args == ('systemctl','enable','--now','backyard-detector.service'):
            raise OSError('Python could not start')
        return 'false' if '{{.State.Running}}' in args else 'no'
    with pytest.raises(OSError, match='Python could not start'):
        switch_to_go({}, run=run, wait_ready=Mock(side_effect=RuntimeError('Go failed')))


def test_checkpoint_restart_keeps_activation_boundary(tmp_path):
    from detector.bat_bridge import save_state
    path = tmp_path/'state.json'
    first = BirdBridge(Mock(), Mock(), Policy(), path, 'birds','2026-10-15',
                       adapter=adapter(), not_before='2026-10-15T18:01:00+00:00')
    save_state(path, first.state)
    second = BirdBridge(Mock(), Mock(), Policy(), path, 'birds','2026-10-16',
                        adapter=adapter(), not_before='2026-10-16T18:01:00+00:00')
    assert second.state['not_before'] == '2026-10-15T18:01:00+00:00'


def test_systemd_go_services_conflict_with_python_and_capture_is_supervised():
    from app.core.config import ROOT
    for unit in ('backyard-birds','backyard-birdnet-go'):
        text = (ROOT/'deploy/systemd'/f'{unit}.service').read_text()
        assert 'Conflicts=backyard-detector.service' in text
        assert 'backyard-detector.service' in next(line for line in text.splitlines() if line.startswith('After='))
    text = (ROOT/'deploy/systemd/backyard-birdnet-go.service').read_text()
    assert 'docker start --attach backyard-birds-go' in text
    assert 'docker stop --time 25 backyard-birds-go' in text


def test_fallback_refuses_to_start_python_if_go_still_captures():
    run = Mock(return_value='true')
    with pytest.raises(RuntimeError, match='not confirmed stopped'):
        python_fallback(run)
    assert not any('backyard-detector.service' in call.args for call in run.call_args_list)
