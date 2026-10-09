from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.avian_collage_exporter.settings import ExportSettings, write_settings, export_hours
from app.modules.avian_collage_exporter import renderer
from test_collage_exporter import config
import pytest

HEADERS = {"Authorization": "Bearer backyard-test-token"}
URL = "/api/avian-collage/settings"


def test_authenticated_settings_persist_and_validate(config):
    settings, _ = config
    with TestClient(create_app(settings)) as client:
        assert client.get(URL).status_code == 401
        assert client.post(URL, json={"period": "all"}).status_code == 401
        assert client.get(URL, headers=HEADERS).json() == {"period": "24h"}
        for period in ("1h", "12h", "24h", "7d", "all"):
            assert client.post(URL, headers=HEADERS, json={"period": period}).json() == {"period": period}
        for invalid in ({"period": "8h"}, {"period": 24}, {"period": "all", "unknown": 1}):
            assert client.post(URL, headers=HEADERS, json=invalid).status_code == 422
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(URL, headers=HEADERS).json() == {"period": "all"}
    assert export_hours(settings) is None


@pytest.mark.parametrize("period, count", [("1h", 3), ("12h", 4), ("24h", 5), ("7d", 6), ("all", 7)])
def test_export_uses_saved_period_without_wordpress(config, period, count):
    settings, now = config
    with TestClient(create_app(settings)) as client:
        with Session(client.app.state.engine) as session:
            for hours in (2, 13, 25, 200):
                session.add(Observation(source="period-test", event_id=str(hours), ingest_hash="b"*64,
                    domain="bird", scientific_name="Turdus merula", common_name="Blackbird",
                    start_at=now-timedelta(hours=hours), end_at=now-timedelta(hours=hours),
                    best_confidence=.9, status="human_confirmed", decision={}, policy={}, clip={}, evidence_kind="permanent"))
            session.commit()
    write_settings(settings, ExportSettings(period=period))
    value, _ = renderer.snapshot(settings, now)
    assert sum(row["count"] for row in value["species"]) == count
