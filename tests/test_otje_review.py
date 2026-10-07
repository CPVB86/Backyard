from sqlalchemy.orm import Session
import pytest
from app.core.database import create_database
from app.modules.observations.models import Observation
from app.modules.observations.schemas import OTJE_SPECIES, serialize
from observations.policy import Policy, observation_payload
from test_observations_api import client, raw, create, upload


def gallus(client, name="Gallus gallus", recommended=False, domain="bird"):
    candidates = [raw(start=i*12000, score=.96 if recommended else .86, state="unusual",
                      name=name, domain=domain) for i in range(1 if recommended else 2)]
    response = client.post("/api/observations", json=observation_payload(candidates, Policy()))
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("name", sorted(OTJE_SPECIES))
@pytest.mark.parametrize("recommended", [False, True])
def test_otje_confirm_persistent_idempotent_preserves_evidence(client, name, recommended):
    item, audio = upload(client, gallus(client, name, recommended))
    expected = "review_recommended" if recommended else "pending_review"
    assert item["status"] == expected
    assert item["review_capabilities"] == {"identity_overrides": ["otje"]}
    assert client.get("/api/observations/review").json()[0]["review_capabilities"] == item["review_capabilities"]
    path = "/api/observations/" + item["id"]
    payload = dict(expected_status=expected, identity_override="otje")
    response = client.post(path+"/confirm", json=payload)
    assert response.status_code == 200
    confirmed = response.json()
    assert confirmed["status"] == "human_confirmed"
    assert confirmed["review"]["identity_override"] == "otje"
    assert confirmed["review"]["action"] == "confirm"
    assert confirmed["review_capabilities"] == {"identity_overrides": []}
    for key in ("scientific_name", "common_name", "confidence", "best_confidence", "timestamp",
                "start_at", "end_at", "decision", "policy", "candidates", "clip", "audio"):
        assert confirmed[key] == item[key], key
    assert confirmed["evidence_kind"] == "permanent"
    assert client.get(confirmed["audio_url"]).content == audio
    assert client.post(path+"/confirm", json=payload).json() == confirmed
    for changed in ({"expected_status":expected}, {**payload,"note":"different"}):
        assert client.post(path+"/confirm", json=changed).status_code == 409
    assert client.get(path).json() == confirmed
    assert client.get("/api/observations?status=human_confirmed").json()[0]["review"] == confirmed["review"]
    assert client.get("/api/observations/review").json() == []
    # Reopen through a genuinely separate engine/connection, not the identity map.
    engine = create_database(client.app.state.settings)
    with Session(engine) as session:
        row = session.get(Observation,item["id"])
        assert serialize(row)["review"] == confirmed["review"]
        assert row.storage_key.startswith("birds/audio/")
    engine.dispose()


@pytest.mark.parametrize("name,domain", [("Parus major","bird"),("Gallus gallus","bat"),
                                          ("Gallus gallus hybrid","bird")])
def test_other_species_and_domain_refused_without_mutation(client, name, domain):
    item,_ = upload(client,gallus(client,name,domain=domain))
    assert item["review_capabilities"] == {"identity_overrides": []}
    path="/api/observations/"+item["id"]
    assert client.post(path+"/confirm",json=dict(expected_status=item["status"],identity_override="otje")).status_code==422
    assert client.get(path).json()==item


@pytest.mark.parametrize("action,extra,expected", [
    ("reject",{"identity_override":"otje"},422),
    ("confirm",{"identity_override":"other"},422),
    ("confirm",{"expected_status":"auto_accepted","identity_override":"otje"},409),
])
def test_invalid_payloads_do_not_mutate(client,action,extra,expected):
    item,_=upload(client,gallus(client))
    path="/api/observations/"+item["id"]
    assert client.post(path+"/"+action,json={"expected_status":item["status"],**extra}).status_code==expected
    assert client.get(path).json()==item


def test_audio_auth_and_no_automatic_identity(client):
    item=gallus(client)
    path="/api/observations/"+item["id"]
    payload=dict(expected_status=item["status"],identity_override="otje")
    assert item["review"] is None
    for token in ("", "Bearer invalid"):
        assert client.post(path+"/confirm",json=payload,headers={"Authorization":token}).status_code==401
    assert client.post(path+"/confirm",json=payload).status_code==404
    assert client.get(path).json()==item


@pytest.mark.parametrize("action", ["confirm","reject"])
def test_plain_final_review_cannot_gain_override(client,action):
    item,_=upload(client,gallus(client))
    path="/api/observations/"+item["id"]
    payload=dict(expected_status=item["status"])
    final=client.post(path+"/"+action,json=payload).json()
    assert "identity_override" not in final["review"]
    assert client.post(path+"/"+action,json={**payload,"identity_override":None}).json()==final
    assert client.post(path+"/confirm",json={**payload,"identity_override":"otje"}).status_code==409
    assert client.get(path).json()==final


def test_auto_accepted_not_offered_override(client):
    item,_=upload(client,create(client,name="Gallus gallus"))
    # Simulate a pre-existing accepted Gallus; reads must not reclassify it.
    with Session(client.app.state.engine) as session:
        session.get(Observation,item["id"]).status = "auto_accepted"
        session.commit()
    path="/api/observations/"+item["id"]
    item = client.get(path).json()
    assert item["review_capabilities"]=={"identity_overrides":[]}
    assert client.post(path+"/confirm",json=dict(expected_status="auto_accepted",identity_override="otje")).status_code==422
    assert client.get(path).json()==item


def test_new_strong_gallus_requires_review_without_automatic_identity(client):
    item = create(client, name="Gallus gallus", score=.99)
    assert item["status"] == "pending_review"
    assert item["classification"] == "human_review"
    assert item["review"] is None
    row = client.get("/api/observations/review").json()[0]
    assert row["id"] == item["id"]
    assert row["review_capabilities"]["identity_overrides"] == ["otje"]
    other = create(client, stream="other", name="Parus major", score=.99)
    assert other["status"] == "auto_accepted"
    assert client.get("/api/observations/count?review_only=true").json() == {"count":1}


@pytest.mark.parametrize("state", ["normal", "unknown", "unusual"])
def test_new_gallus_gate_does_not_depend_on_geo(state):
    from observations.policy import decision
    support = [raw(name="Gallus gallus", state=state, score=.7)]
    assert decision(support, Policy())["classification"] == "human_review"
    assert decision(support, Policy(), new_observation=False)["classification"] != "human_review"


def test_otje_filtered_view_count_and_confirmation(client):
    from unittest.mock import patch
    item, _ = upload(client, gallus(client))
    other = create(client, score=.96, state="unusual", stream="other-review")
    listing = "/api/observations/review?domain=bird&identity_override=otje&limit=1"
    counter = "/api/observations/count?domain=bird&review_only=true&identity_override=otje"
    assert [r["id"] for r in client.get(listing).json()] == [item["id"]]
    assert client.get(counter).json() == {"count": 1}
    # The filter uses the same capability function, not another species list.
    with patch("app.modules.observations.schemas.identity_overrides", return_value=["otje"]):
        assert client.get(counter).json() == {"count": 2}
    with Session(client.app.state.engine) as session:
        row = session.get(Observation, item["id"])
        with patch("app.modules.observations.router.selected_review_records", return_value=iter([row]*151)):
            assert client.get(counter).json() == {"count": 151}
    response = client.post(f"/api/observations/{item['id']}/confirm",
                           json={"expected_status":item["status"], "identity_override":"otje"})
    assert response.status_code == 200
    assert client.get(listing).json() == []
    assert client.get(counter).json() == {"count": 0}
    assert other["id"] in [r["id"] for r in client.get("/api/observations/review").json()]
    assert client.get("/api/observations/count?identity_override=otje").status_code == 422
