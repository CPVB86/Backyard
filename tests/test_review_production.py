import json
import sqlite3
from dataclasses import asdict
import pytest
from sqlalchemy.orm import Session
from app.modules.observations.models import Observation
from observations.policy import Policy, observation_payload, decision, VERSION
from operations.review_migration import migrate
from operations.review_analysis import simulate
from test_observations_api import client, create, raw, upload


@pytest.mark.parametrize("score,windows,state,category", [
    (.85,1,"normal","accepted"),(.75,2,"normal","accepted"),(.65,3,"normal","accepted"),
    (.74,2,"normal","low_evidence"),(.99,3,"unknown","unknown"),
    (.85,2,"unusual","human_review"),(.90,1,"unusual","human_review")])
def test_one_policy_for_simulation_and_production(score, windows, state, category):
    support = [raw(start=i*12000, score=score, state=state) for i in range(windows)]
    actual = decision(support, Policy())
    assert actual["classification"] == category
    assert simulate(dict(confidence=score,supports=windows,plausibility=state))[1] == category


def legacy(client, **kwargs):
    item = create(client, **kwargs)
    with Session(client.app.state.engine) as session:
        row = session.get(Observation, item["id"])
        row.status = "review_recommended" if kwargs.get("state") == "unusual" else "pending_review"
        row.decision = {**row.decision, "policy_version":"observation-policy-1"}
        row.evidence_kind = "review"
        session.commit()
    return item


def test_review_list_count_filter_legacy_and_new_before_limit(client):
    wanted = create(client, state="unusual", score=.96, stream="review")
    for i, state in enumerate(("normal","unknown")):
        create(client, state=state, score=.7, stream=str(i))
    legacy(client, state="unknown", score=.99, stream="legacy")
    rows = client.get("/api/observations/review?domain=bird&limit=1").json()
    assert [r["id"] for r in rows] == [wanted["id"]]
    assert rows[0]["classification"] == "human_review"
    assert client.get("/api/observations/count?domain=bird&review_only=true").json() == {"count":1}
    assert client.get("/api/observations/count?review_only=true&status=pending_review").status_code == 422


def test_migration_readonly_apply_idempotence_audio_and_human_decisions(client):
    accepted = legacy(client, score=.86, stream="accepted")
    accepted, audio = upload(client, accepted)
    legacy(client, score=.7, stream="low")
    legacy(client, score=.99, state="unknown", stream="unknown")
    legacy(client, score=.96, state="unusual", stream="review")
    human = create(client, stream="human")
    with Session(client.app.state.engine) as session:
        row = session.get(Observation,human["id"])
        row.status = "human_confirmed"
        session.commit()
    path = client.app.state.settings.resolved_database_path
    before = path.read_bytes()
    report = migrate(path)
    assert path.read_bytes() == before
    assert report["classifications"] == dict(accepted=1,low_evidence=1,unknown=1,human_review=1)
    # Apply is exercised ONLY against the fixture's temporary test database.
    assert migrate(path,apply=True)["changed"] == 4
    assert migrate(path,apply=True)["changed"] == 0
    migrated = client.get('/api/observations/'+accepted['id']).json()
    assert migrated['status'] == 'auto_accepted' and migrated['evidence_kind'] == 'permanent'
    with Session(client.app.state.engine) as session:
        record = session.get(Observation, accepted['id'])
        assert record.storage_key.startswith('review/')
    assert client.get(migrated['audio_url']).content == audio
    assert client.get('/api/observations/'+human['id']).json()['status'] == 'human_confirmed'


def test_migration_rolls_back_all_updates_on_failure(client):
    for i in range(2): legacy(client, score=.86, stream=str(i))
    path = client.app.state.settings.resolved_database_path
    with sqlite3.connect(path) as db:
        last = db.execute('SELECT id FROM observations ORDER BY id DESC LIMIT 1').fetchone()[0]
        db.execute('DELETE FROM observation_candidates WHERE observation_id=?',(last,))
    before = path.read_bytes()
    with pytest.raises(ValueError,match='Missing candidate'):
        migrate(path,apply=True)
    assert path.read_bytes() == before
