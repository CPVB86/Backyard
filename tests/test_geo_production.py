"""Production configuration, seasonal signals and unchanged policy decisions."""
from dataclasses import replace
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from detector.birdnet_adapter import Prediction
from detector.monitor import main, parser
from detector.providers import GeoPlausibility, birdnet_week, plausibility_at
from detector.stream import MonitorConfig
from observations.policy import Policy, decision
from operations.status import geo_report, warnings
from test_observation_policy import candidate


def geo_model(monkeypatch, rows):
    session = Mock()
    session.run.side_effect = lambda lat, lon, week: SimpleNamespace(to_structured_array=lambda: rows(week))
    context = MagicMock()
    context.__enter__.return_value = session
    model = Mock()
    model.predict_session.return_value = context
    monkeypatch.setitem(sys.modules, "birdnet", SimpleNamespace(load=Mock(return_value=model)))
    return model, session


def test_local_exotic_and_out_of_season_use_location_period_without_acoustic_filter(monkeypatch):
    def rows(week):
        return [{"species_name": "Parus major_Great Tit", "confidence": .4},
                {"species_name": "Ara macao_Scarlet Macaw", "confidence": 0.0},
                {"species_name": "Hirundo rustica_Barn Swallow", "confidence": .3 if 17 <= week <= 32 else .001}]
    model, session = geo_model(monkeypatch, rows)
    provider = GeoPlausibility(52.0, 5.0)
    model.predict_session.assert_called_once_with(min_confidence=0.0, device="CPU", half_precision=False)
    assert all(call.args == (52.0, 5.0) for call in session.run.call_args_list)
    assert [call.kwargs["week"] for call in session.run.call_args_list] == list(range(1, 49))
    predictions = [Prediction(name, "English", .7, 0, 3) for name in ("Parus major", "Ara macao", "Hirundo rustica")]
    enriched = provider.annotate(predictions)
    assert [p.confidence for p in enriched] == [.7]*3
    signals = [plausibility_at(p.plausibility, "2026-01-01T00:00:00Z", Policy()) for p in enriched]
    assert [s["state"] for s in signals] == ["normal", "unusual", "unusual"]
    assert signals[1]["score"] == 0.0  # zero is known unlikely, not unknown
    assert plausibility_at(enriched[2].plausibility, "2026-06-01T00:00:00Z", Policy())["state"] == "normal"
    assert [decision([candidate(score=.7, state=s["state"])], Policy())["status"] for s in signals] == ["pending_review", "discarded", "discarded"]
    assert decision([candidate(score=.95, state="unusual")], Policy())["status"] == "review_recommended"
    supports = [candidate(score=.86, state="unusual"), replace(candidate(score=.86, state="unusual"), window_id="second")]
    assert decision(supports, Policy())["status"] == "pending_review"
    assert provider.status["active"] and provider.status["status"] == "ready"
    missing = provider.annotate([Prediction("Not in model", "Unknown", .7, 0, 3)])[0]
    assert plausibility_at(missing.plausibility, "2026-01-01T00:00:00Z", Policy())["reason"] == "species_not_in_geo_model"


@pytest.mark.parametrize("date,expected", [
    ("2024-02-29T12:00:00Z", 8), ("2026-03-01T00:30:00+01:00", 8),
    ("2026-10-02T00:00:00Z", 37), ("2026-10-08T00:00:00Z", 38),
    ("2027-01-01T00:00:00Z", 1),
])
def test_period_boundaries(date, expected):
    assert birdnet_week(date) == expected


def test_naive_period_is_not_interpreted_using_host_timezone():
    with pytest.raises(ValueError, match="timezone"):
        birdnet_week("2026-01-01T00:00:00")


@pytest.mark.parametrize("extra", [[], ["--geography"], ["--geography", "--latitude", "nan", "--longitude", "5"]])
def test_missing_or_invalid_production_geo_stops_before_model(monkeypatch, extra):
    for key in ("GEOGRAPHY", "LATITUDE", "LONGITUDE"):
        monkeypatch.delenv("BACKYARD_MONITOR_" + key, raising=False)
    monitor = Mock()
    monkeypatch.setattr("detector.monitor.Monitor", monitor)
    with pytest.raises(SystemExit):
        main(extra)
    monitor.assert_not_called()


def test_coordinates_loaded_from_production_environment(monkeypatch):
    for key, value in {"GEOGRAPHY":"1", "LATITUDE":"52.0", "LONGITUDE":"5.0"}.items():
        monkeypatch.setenv("BACKYARD_MONITOR_"+key, value)
    config = MonitorConfig(**vars(parser().parse_args([])))
    assert config.geography and float(config.latitude) == 52.0
    assert float(config.longitude) == 5.0


@pytest.mark.parametrize("rows", [[], [{"species_name":"Parus major_Great Tit", "confidence":float("nan")} ]])
def test_bad_provider_fails_instead_of_unknown(monkeypatch, rows):
    geo_model(monkeypatch, lambda week: rows)
    with pytest.raises(RuntimeError, match="Geo provider"):
        GeoPlausibility(52, 5)


def test_operations_distinguishes_configured_from_running_geo():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp()
    env = {"BACKYARD_MONITOR_GEOGRAPHY":"1", "BACKYARD_MONITOR_LATITUDE":"52", "BACKYARD_MONITOR_LONGITUDE":"5"}
    services = {"backyard-detector.service":{"InvocationID":"current"}}
    assert geo_report({}, {}, services, now)["status"] == "disabled"
    assert geo_report(env | {"BACKYARD_MONITOR_LATITUDE":""}, {}, services, now)["status"] == "invalid"
    assert geo_report(env, {}, services, now)["status"] == "unavailable"
    runtime = {"active":True,"status":"ready","error":None,"latitude":52.,"longitude":5.,"week":37}
    journal = {"latest":{"timestamp":now, "invocation":"current", "metrics":{"geo":runtime}}}
    assert geo_report(env,journal,services,now)["active"]
    assert geo_report(env | {"BACKYARD_MONITOR_LATITUDE":"53"},journal,services,now)["error"]
    runtime.update(active=False,status="error",error="Model download failed")
    report = geo_report(env,journal,services,now)
    assert report["error"] == "Model download failed"
    assert any("Model download failed" in warning for warning in warnings({"geo":report}))


def test_unusual_moderate_candidate_creates_no_clip_job():
    from test_observation_pipeline import setup_pipeline
    from detector.stream import Window
    import time
    config, incoming, clips, metrics, pipeline = setup_pipeline(overlap=1.5)
    prediction = Prediction("Ara macao", "Scarlet Macaw", .7, 0, 3,
                            {"weekly_scores":[0.0]*48, "provider":"test"})
    incoming.put((Window(0,144000,b"",time.monotonic()),[prediction]))
    pipeline.tick()
    incoming.put((Window(216000,360000,b"",time.monotonic()),[]))
    pipeline.tick()
    assert clips.status()[0] == 0
    assert metrics.snapshot()["discarded_candidates"] == 1
    assert metrics.snapshot().get("observations_planned", 0) == 0


def test_monitor_metrics_include_effective_geo_and_failure():
    from detector.monitor import Monitor
    geo = {"active":True,"status":"ready","error":None,"latitude":52.,"longitude":5.}
    analyzer = SimpleNamespace(geo_status=geo)
    monitor = Monitor(MonitorConfig(geography=True, latitude="52", longitude="5"), analyzer=analyzer)
    monitor.phase = "running"
    snapshot = monitor.status()
    assert snapshot["geo"]["latitude"] == 52 and snapshot["geo"]["active"]
    assert snapshot["geo"]["week"] == birdnet_week(snapshot["recorded_at"])
    monitor.error = "Geo startup failed"
    assert monitor.status()["geo"]["error"] == "Geo startup failed"
    assert not monitor.status()["geo"]["active"]
