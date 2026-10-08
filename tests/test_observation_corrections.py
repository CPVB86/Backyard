from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import Mock, patch
import pytest
from sqlalchemy.orm import Session
from app.core.database import create_database
from app.modules.observations.models import Observation
from app.modules.observations.service import cleanup
from app.modules.avian_visitors import service as avian
from app.modules.species import service as species
from test_observations_api import client, create, upload
from test_avian_visitors import Generator


def payload(item, action="confirm", override=None):
    return dict(expected_status=item["status"], expected_version=item["review_version"],
                request_id=str(uuid4()), action=action, identity_override=override,
                actor="wordpress:admin-1", note="Manual correction")


def correct(client, item, action="confirm", override=None):
    response = client.post('/api/observations/'+item['id']+'/correct',json=payload(item,action,override))
    assert response.status_code == 200, response.text
    return response.json()


def confirmed(client, stream="first", start=0):
    item, audio = upload(client,create(client,name="Gallus gallus",stream=stream,start=start))
    response=client.post('/api/observations/'+item['id']+'/confirm',json={'expected_status':item['status']})
    assert response.status_code==200
    return response.json(), audio


def test_all_corrections_update_live_counts_atlas_audio_and_audit(client):
    older,_=confirmed(client)
    item,audio=confirmed(client,"later",3600*8000)
    original=item.copy()
    generator=Generator({'Gallus gallus':('perched','flight')})
    client.app.state.generator=generator
    client.app.state.generator_scheduler.accepted=Mock()
    now=datetime(2026,10,1,12,tzinfo=timezone.utc)
    engine=client.app.state.engine
    def verify(current, total, otje):
        for hours in (24,24*30,24*365):
            stats=avian.stats(engine,hours,'nl',now=now)
            assert stats['observation_count']==stats['all_time_observation_count']==total
            assert stats['species_count']==1
            assert sum(x['count'] for x in stats['timeline'])==total
            assert sum(stats['rhythm'])==total
            assert sum(sum(x['counts']) for x in stats['hourly_species'])==total
            assert stats['last_observed_at'].isoformat()==(older if total==1 else original)['start_at']
        life=avian.lifelist(engine,generator,'nl',now=now)
        assert life['observation_count']==total and life['species_count']==1
        assert sum(x['count'] for x in life['species'] if x['identity_id']=='otje')==otje
        if otje:
            row=next(x for x in life['species'] if x['identity_id']=='otje')
            assert row['assets']['perched']['url'].endswith('/otje_perched')
            detail=avian.detail(engine,client.app.state.settings,generator,'Gallus gallus','nl','otje')
            assert detail['observations']['total']==otje
        detail=species.detail(engine,client.app.state.settings,generator,'bird','Gallus gallus',now=now)
        assert detail['observations']['total']==detail['observations']['today']==detail['observations']['last_7_days']==total
        assert client.get('/api/observations/count?status=human_confirmed').json()['count']==total
        for field in ('scientific_name','common_name','confidence','timestamp','start_at','end_at','decision','policy','candidates','clip'):
            assert current[field]==original[field]
        assert client.get(current['audio_url']).content==audio
        assert client.get('/api/observations/count').json()['count']==2
    verify(item,2,0)
    item=correct(client,item,override='otje');verify(item,2,1)
    assert item['effective_identity']['identity_override']=='otje'
    item=correct(client,item);verify(item,2,0)
    item=correct(client,item,action='reject');verify(item,1,0)
    assert cleanup(engine,client.app.state.settings,apply=True,now=now+timedelta(days=1000))==[]
    item=correct(client,item);verify(item,2,0)
    assert len(item['review']['history'])==5
    assert item['review']['history'][-1]['old_status']=='human_rejected'
    assert item['review']['history'][-1]['actor']=='wordpress:admin-1'
    reopened=create_database(client.app.state.settings)
    with Session(reopened) as session:
        assert session.get(Observation,item['id']).review==item['review']
    reopened.dispose()
    client.app.state.generator_scheduler.accepted.assert_not_called()


def test_idempotency_version_and_request_conflicts(client):
    item,_=confirmed(client)
    endpoint='/api/observations/'+item['id']+'/correct'
    request=payload(item,override='otje')
    first=client.post(endpoint,json=request).json()
    assert client.post(endpoint,json=request).json()==first
    assert client.post(endpoint,json={**request,'note':'changed'}).status_code==409
    assert client.post(endpoint,json={**request,'request_id':str(uuid4())}).status_code==409
    second=correct(client,first)
    assert client.post(endpoint,json=request).status_code==409
    assert client.get('/api/observations/'+item['id']).json()==second


def test_auto_accepted_and_legacy_review_correction(client):
    item,_=upload(client,create(client))
    result=correct(client,item,action='reject')
    assert result['status']=='human_rejected'
    result=correct(client,result)
    assert result['status']=='human_confirmed'
    with Session(client.app.state.engine) as session:
        row=session.get(Observation,item['id'])
        row.review={'action':'confirm','note':'legacy','at':'2026-01-01T00:00:00Z','reason':'human_confirmation'}
        session.commit()
    old=client.get('/api/observations/'+item['id']).json()
    assert old['review_version']==0
    new=correct(client,old,action='reject')
    assert new['review']['history'][0]['previous_review']==old['review']


@pytest.mark.parametrize('deleted',[False,True])
def test_restore_old_rejection_requires_intact_audio(client,deleted):
    item,_=upload(client,create(client,score=.7))
    path='/api/observations/'+item['id']
    item=client.post(path+'/reject',json={'expected_status':item['status']}).json()
    if deleted:
        cleanup(client.app.state.engine,client.app.state.settings,apply=True,now=datetime.now(timezone.utc)+timedelta(days=100))
        item=client.get(path).json()
        assert client.post(path+'/correct',json=payload(item)).status_code==404
        assert client.get(path).json()==item
    else:
        result=correct(client,item)
        assert result['evidence_kind']=='permanent' and result['cleanup_after'] is None


def test_validation_auth_and_transaction_rollback(client):
    item,_=confirmed(client)
    path='/api/observations/'+item['id']
    request=payload(item,override='otje')
    assert client.post(path+'/correct',json=request,headers={'Authorization':'Bearer wrong'}).status_code==401
    assert client.post(path+'/correct',json={**request,'action':'reject'}).status_code==422
    assert client.post(path+'/correct',json={**request,'identity_override':'other'}).status_code==422
    assert client.post(path+'/correct',json={**request,'expected_version':True}).status_code==422
    with patch.object(Session,'commit',side_effect=RuntimeError('test failure')):
        with pytest.raises(RuntimeError,match='test failure'):
            client.post(path+'/correct',json=request)
    assert client.get(path).json()==item


def test_concurrent_corrections_only_one_wins(client):
    from concurrent.futures import ThreadPoolExecutor
    item,_=confirmed(client)
    path='/api/observations/'+item['id']+'/correct'
    requests=[payload(item,override='otje'),payload(item,action='reject')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda body: client.post(path,json=body), requests))
    assert sorted(r.status_code for r in results)==[200,409]
    latest=client.get('/api/observations/'+item['id']).json()
    assert latest['review_version']==item['review_version']+1
    assert len(latest['review']['history'])==2


def test_existing_auto_accepted_gallus_can_be_otje(client):
    item,_=upload(client,create(client,name='Gallus gallus'))
    with Session(client.app.state.engine) as session:
        row=session.get(Observation,item['id'])
        row.status='auto_accepted'
        session.commit()
    item=client.get('/api/observations/'+item['id']).json()
    assert correct(client,item,override='otje')['effective_identity']['identity_override']=='otje'


@pytest.mark.parametrize('domain,name', [('bird','Parus major'),('bat','Gallus gallus')])
def test_correction_identity_eligibility(client,domain,name):
    item,_=upload(client,create(client,domain=domain,name=name))
    path='/api/observations/'+item['id']
    assert client.post(path+'/correct',json=payload(item,override='otje')).status_code==422
    assert client.get(path).json()==item
