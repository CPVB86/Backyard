from datetime import datetime, timezone
from unittest.mock import Mock
from sqlalchemy.orm import Session
from app.modules.species.models import Species
from test_observations_api import client, create, upload
from test_observation_corrections import payload


def test_bat_review_correction_audio_and_effective_totals(client):
    item, audio = upload(client, create(client, domain='bat', state='unknown', name='Pipistrellus pipistrellus'))
    original = item.copy()
    # Unknown bat plausibility must remain unchanged: WP lists open statuses.
    assert item['classification'] == 'unknown'
    assert client.get('/api/observations/review?domain=bat').json() == []
    assert client.get('/api/observations?domain=bat&status='+item['status']).json()[0]['id'] == item['id']
    assert client.get('/api/observations/count?domain=bat&status='+item['status']).json()['count'] == 1
    assert item['correction_capabilities']['actions'] == ['confirm', 'reject']
    assert item['correction_capabilities']['identity_overrides'] == []
    item = client.post('/api/observations/'+item['id']+'/confirm', json={'expected_status':item['status']}).json()
    assert item['status'] == 'human_confirmed'
    def total():
        return client.get('/api/presentation/bats').json()['stats']['total_observations']
    assert total() == 1
    with Session(client.app.state.engine) as session:
        session.add(Species(domain='bat', scientific_name='Myotis daubentonii', common_name_nl='Watervleermuis',
            source='test', source_species_id=90001, source_metadata={}, imported_at=datetime.now(timezone.utc)))
        session.add(Species(domain='bird', scientific_name='Parus major', common_name_nl='Koolmees',
            source='test', source_species_id=90000, source_metadata={}, imported_at=datetime.now(timezone.utc)))
        session.commit()
    client.app.state.generator_scheduler.accepted = Mock()
    for action, name, expected in [('confirm','Myotis daubentonii',1), ('reject','Myotis daubentonii',0), ('confirm',None,1)]:
        request = payload(item, action)
        request['scientific_name_override'] = name
        response = client.post('/api/observations/'+item['id']+'/correct',json=request)
        assert response.status_code == 200, response.text
        item = response.json()
        assert total() == expected
        if expected:
            assert client.get('/api/presentation/bats').json()['species'][0]['scientific_name'] == (name or original['scientific_name'])
        assert item['effective_identity']['scientific_name'] == (name or original['scientific_name'])
        for field in ('scientific_name','confidence','timestamp','candidates','clip','decision'):
            assert item[field] == original[field]
        assert client.post('/api/observations/'+item['id']+'/correct',json=request).json() == item
        assert client.get(item['audio_url']).content == audio
        partial = client.get(item['audio_url'], headers={'Range':'bytes=0-11'})
        assert partial.status_code == 206 and partial.content == audio[:12]
    client.app.state.generator_scheduler.accepted.assert_not_called()
    stale = payload(item); stale['expected_version'] -= 1
    assert client.post('/api/observations/'+item['id']+'/correct', json=stale).status_code == 409
    forbidden = payload(item, override='otje')
    assert client.post('/api/observations/'+item['id']+'/correct', json=forbidden).status_code == 422
    wrong_species = payload(item); wrong_species['scientific_name_override'] = 'Parus major'
    assert client.post('/api/observations/'+item['id']+'/correct',json=wrong_species).status_code == 422


def test_catalog_search_auth_and_domain_isolation(client):
    with Session(client.app.state.engine) as session:
        for domain, name, number in [('bat','Myotis daubentonii',90002),('bird','Parus major',90003)]:
            session.add(Species(domain=domain, scientific_name=name, common_name_nl='Testsoort',
                source='test',source_species_id=number,source_metadata={},imported_at=datetime.now(timezone.utc)))
        session.commit()
    url='/api/species/search?domain=bat&q=Testsoort'
    assert client.get(url,headers={'Authorization':'Bearer wrong'}).status_code == 401
    rows=client.get(url).json()['results']
    assert [r['scientific_name'] for r in rows] == ['Myotis daubentonii']
    assert client.get('/api/species/search?domain=bat&q=Myotis%20daubentonii').json()['results'] == rows
    assert client.get('/api/species/search?domain=fish&q=test').status_code == 422
