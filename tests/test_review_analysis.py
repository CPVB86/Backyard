from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.modules.observations.service import ingest
from app.modules.observations.schemas import ObservationInput
from observations.policy import Policy, observation_payload
from operations.review_analysis import simulate, clusters, analyze
from test_observations_api import raw


@pytest.mark.parametrize("confidence,supports,plausibility,expected",[
    (.85,1,"normal","accepted"),(.75,2,"normal","accepted"),(.65,3,"normal","accepted"),
    (.849,1,"normal","low_evidence"),(.749,2,"normal","low_evidence"),
    (.649,3,"normal","low_evidence"),(.99,5,"unknown","unknown"),
    (.6,1,"unknown","unknown"),(.90,1,"unusual","human_review"),
    (.85,2,"unusual","human_review")])
def test_four_outcomes(confidence,supports,plausibility,expected):
    # Supports are overlapping windows, never independent calls or detectors.
    r=dict(confidence=confidence,supports=supports,plausibility=plausibility,
           rarity="zeer zeldzaam",original_status="pending_review")
    assert simulate(r)[1] == expected


def test_existing_recommended_unusual_stays_review():
    assert simulate(dict(confidence=.91,supports=1,plausibility="unusual",
                         original_status="review_recommended"))[1] == "human_review"


def test_unusual_below_threshold_requires_explicit_policy_decision():
    with pytest.raises(ValueError, match="cannot classify safely"):
        simulate(dict(confidence=.7,supports=1,plausibility="unusual"))


def test_calendar_day_amsterdam_and_dst():
    from operations.review_analysis import local_day
    assert local_day("2026-10-03T22:30:00") == "2026-10-04"
    assert local_day("2026-12-03T22:30:00") == "2026-12-03"


def test_anchored_clusters_species_and_time_boundaries():
    def row(name,seconds):
        at=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat()
        return dict(id=str(seconds)+name,domain="bird",scientific_name=name,start_at=at,end_at=at,
                    confidence=.7,supports=1,proposed_reasons=["low_support"],audio_available=True)
    result=clusters([row("Corvus corone",0),row("Corvus corone",299),row("Corvus corone",301),row("Other bird",1)])
    assert len(result)==3 and result[0]["detections"]==2


def test_analysis_is_readonly_and_does_not_include_human_states(tmp_path):
    config=Settings(_env_file=None,database_path=tmp_path/"db.sqlite3")
    engine=create_database(config); initialize_database(engine)
    policy=Policy()
    for i in range(3):
        payload=ObservationInput(**observation_payload([raw(score=.7,stream=str(i))],policy))
        ingest(engine,policy,payload)
    from app.modules.observations.models import Observation
    with Session(engine) as session:
        rows=session.query(Observation).order_by(Observation.id).all()
        rows[1].status="human_confirmed";rows[2].status="human_rejected";session.commit()
    before=config.resolved_database_path.read_bytes()
    result=analyze(config.resolved_database_path)
    assert result["total_analyzed"]==1
    assert result["totals"]=={"accepted":0,"low_evidence":1,"human_review":0,"unknown":0}
    assert result["by_category"]["low_evidence"]["supports"]=={"1":1}
    assert config.resolved_database_path.read_bytes()==before
    engine.dispose()


def test_full_report_preserves_unknown_and_recommended_without_writes(tmp_path):
    from app.modules.observations.models import Observation
    config = Settings(_env_file=None, database_path=tmp_path / "snapshot.sqlite3")
    engine = create_database(config)
    initialize_database(engine)
    policy = Policy()
    for i, score in enumerate((.8, .7, .91, .92, .7)):
        ingest(engine, policy, ObservationInput(**observation_payload([raw(score=score, stream=str(i))], policy)))
    with Session(engine) as session:
        rows = session.query(Observation).order_by(Observation.best_confidence).all()
        for index, row in enumerate(rows):
            state = ("normal", "unknown", "normal", "unusual", "unknown")[index]
            row.status = "review_recommended" if state == "unusual" else "pending_review"
            row.decision = {**row.decision, "plausibility": state}
            candidate = row.supports[0]
            candidate.raw = {**candidate.raw, "plausibility": {"state": state, "provider": "disabled" if index == 1 else "test-geo"}}
        session.commit()
    before = config.resolved_database_path.read_bytes()
    report = analyze(config.resolved_database_path)
    assert report["totals"] == dict(accepted=0, low_evidence=2, human_review=1, unknown=2)
    assert report["strong_unknown"]["count"] == 1
    assert report["unknown_causes"] == {"disabled": 1, "provider_present_no_usable_result": 1}
    assert report["review_recommended_remaining_human_review"] == 1
    assert report["pending_review_no_longer_human_review"] == 4
    assert sum(d["detections"] for d in report["daily"]) == 5
    assert report["human_review_clusters"] == 1
    assert config.resolved_database_path.read_bytes() == before
    engine.dispose()
