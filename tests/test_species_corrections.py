from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from app.modules.observations.models import Observation
from app.modules.species.models import Species
from app.modules.species import service as species
from app.modules.avian_visitors import service as avian
from test_observation_corrections import confirmed, payload, correct
from test_observations_api import client
from test_avian_visitors import Generator


def catalog(client):
    with Session(client.app.state.engine) as session:
        for i,name in enumerate(('Gallus gallus','Parus major')):
            session.add(Species(domain='bird',scientific_name=name,common_name_nl=name,
                                source='test',source_species_id=i+1,source_metadata={},imported_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        session.commit()


@pytest.mark.parametrize('initial_status',['auto_accepted','human_confirmed'])
def test_species_transfer_and_reject_restore_counts(client, initial_status):
    catalog(client)
    item,audio=confirmed(client)
    with Session(client.app.state.engine) as session:
        session.get(Observation,item['id']).status=initial_status
        session.commit()
    item=client.get('/api/observations/'+item['id']).json()
    original=item.copy()
    generator=Generator({'Gallus gallus':('perched','flight'),'Parus major':('perched','flight')})
    now=datetime(2026,10,1,12,tzinfo=timezone.utc)
    engine=client.app.state.engine
    def verify(item, name, total):
        for hours in (24,720,8760):
            stats=avian.stats(engine,hours,'nl',now=now)
            assert stats['observation_count']==stats['all_time_observation_count']==total
            assert sum(x['count'] for x in stats['timeline'])==total
            assert {x['scientific_name'] for x in stats['species']}==({name} if total else set())
        life=avian.lifelist(engine,generator,'nl',now=now)
        assert life['observation_count']==total
        for target in ('Gallus gallus','Parus major'):
            detail=species.detail(engine,client.app.state.settings,generator,'bird',target,now=now)
            assert detail['observations']['total']==(total if target==name else 0)
            assert detail['observations']['today']==(total if target==name else 0)
        matches=avian.search(engine,name)
        assert next(x for x in matches if x['scientific_name']==name)['observation_count']==total
        assert client.get('/api/observations/count').json()['count']==1
        assert client.get(item['audio_url']).content==audio
        for field in ('id','event_id','scientific_name','common_name','confidence','timestamp','policy','decision','candidates'):
            assert item[field]==original[field]
    verify(item,'Gallus gallus',1)
    item=correct(client,item,override='otje')
    endpoint='/api/observations/'+item['id']+'/correct'
    request={**payload(item), 'scientific_name_override':'Parus major'}
    response=client.post(endpoint,json=request)
    assert response.status_code==200,response.text
    item=response.json()
    assert item['effective_identity']==dict(domain='bird',scientific_name='Parus major',identity_override=None)
    assert item['review']['history'][-1]['old_scientific_name']=='Gallus gallus'
    assert item['review']['history'][-1]['new_scientific_name']=='Parus major'
    assert client.post(endpoint,json=request).json()==item
    assert client.post(endpoint,json={**request,'request_id':str(uuid4())}).status_code==409
    verify(item,'Parus major',1)
    item=correct(client,item,action='reject');verify(item,'Parus major',0)
    item=correct(client,item);verify(item,'Parus major',1)
    restored=client.post(endpoint,json={**payload(item),'scientific_name_override':None}).json()
    assert restored['effective_identity']['scientific_name']=='Gallus gallus'
    verify(restored,'Gallus gallus',1)


def test_unknown_target_and_bat_target_rejected(client):
    item,_=confirmed(client)
    with Session(client.app.state.engine) as session:
        session.add(Species(domain='bat',scientific_name='Test bat',source='test',source_species_id=99,source_metadata={},imported_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        session.commit()
    path='/api/observations/'+item['id']
    for name in ('Absent bird','Test bat'):
        assert client.post(path+'/correct',json={**payload(item),'scientific_name_override':name}).status_code==422
        assert client.get(path).json()==item
