from dataclasses import replace
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest
from observations.policy import (Policy, RawCandidate, decision, Aggregator,
                                 observation_payload, validate_event, domain_from_taxonomy)
from detector.providers import AcousticTaxonomy, GeoPlausibility, birdnet_week, plausibility_at
from detector.birdnet_adapter import Prediction

RATE = 48000


def candidate(start=0, score=.94, domain="bird", state="normal", **changes):
    values = dict(candidate_id=f"c-{start}", window_id=f"w-{start}", source="synthetic-test",
                  stream_id="stream-1", domain=domain, scientific_name="Parus major",
                  common_name="Great Tit", confidence=score, sample_rate=RATE,
                  start_sample=start, end_sample=start + 3 * RATE,
                  utc_anchor="2026-10-01T00:00:00+00:00", model_version="synthetic-1",
                  plausibility={"state": state, "provider": "test"})
    values.update(changes)
    return RawCandidate(**values)


@pytest.mark.parametrize("score,state,count,status,evidence", [
    (.94, "normal", 1, "auto_accepted", "permanent"),
    (.68, "normal", 1, "pending_review", "review"),
    (.59, "normal", 1, "discarded", "none"),
    (.96, "unusual", 1, "review_recommended", "permanent"),
    (.68, "unusual", 1, "discarded", "none"),
    (.88, "unusual", 2, "pending_review", "review"),
    (.88, "normal", 2, "auto_accepted", "permanent"),
    (.88, "normal", 1, "pending_review", "review"),
    (.96, "unknown", 1, "pending_review", "review"),
    (.97, "unknown", 1, "auto_accepted", "permanent"),
    (.88, "unknown", 2, "auto_accepted", "permanent"),
    (.60, "normal", 1, "pending_review", "review"),
])
def test_explainable_policy(score, state, count, status, evidence):
    result = decision([candidate(i * 72000, score, state=state) for i in range(count)], Policy())
    assert result["status"] == status and result["evidence"] == evidence
    assert result["reasons"] and result["policy_fingerprint"] == Policy().fingerprint


def test_bat_uses_same_policy_without_birdnet():
    bat = candidate(domain="bat", scientific_name="Pipistrellus pipistrellus", common_name="Common Pipistrelle")
    assert decision([bat], Policy())["status"] == "auto_accepted"
    assert domain_from_taxonomy("Mammalia", "Chiroptera") == "bat"
    assert domain_from_taxonomy("Mammalia", "Primates") == "unsupported"


@pytest.mark.parametrize("name", ["Pan troglodytes", "Canis lupus", "Apis mellifera"])
def test_actual_catalog_excludes_non_targets(name):
    domain, taxonomy = AcousticTaxonomy().resolve(name)
    assert domain in ("unsupported", "unknown")
    item = candidate(score=.68, domain=domain, scientific_name=name)
    outcome = decision([item], Policy())
    assert outcome == {"status": "discarded", "evidence": "none", "reasons": ["outside_target_domain"]}


def test_actual_catalog_bird_and_bat_capability():
    catalog = AcousticTaxonomy()
    assert catalog.resolve("Parus major")[0] == "bird"
    assert catalog.resolve("Eptesicus serotinus")[0] == "unsupported"
    assert catalog.resolve("Eptesicus serotinus")[1]["reason"] == "birdnet_bat_pipeline_not_enabled"


def test_overlap_aggregation_preserves_support_ids_best_confidence_and_time():
    aggregator = Aggregator(Policy())
    rows = [candidate(i * 72000, score) for i, score in enumerate((.71, .91, .84))]
    for index, row in enumerate(rows):
        assert aggregator.add(row, index) == []
    results = aggregator.advance(6 * RATE, "stream-1", 3)
    assert results == [rows]
    payload = observation_payload(rows, Policy())
    assert [c["candidate_id"] for c in payload["candidates"]] == ["c-0", "c-72000", "c-144000"]
    assert payload["clip_start_sample"] == 0 and payload["clip_end_sample"] == 7 * RATE
    assert decision(rows, Policy())["best_confidence"] == .91
    assert payload == observation_payload(rows, Policy())


@pytest.mark.parametrize("offset", [3 * RATE, 60 * RATE])
def test_touching_or_separate_events_never_merge(offset):
    aggregator = Aggregator(Policy())
    first, later = candidate(), candidate(offset)
    aggregator.add(first, 0)
    assert aggregator.add(later, 1) == [[first]]
    assert aggregator.finish() == [[later]]


def test_long_chaining_is_split_at_maximum_span():
    aggregator = Aggregator(Policy(max_event_seconds=6))
    completed = []
    for index in range(20):
        completed += aggregator.add(candidate(index * 72000), index)
    completed += aggregator.finish()
    assert len(completed) > 1
    assert sum(map(len, completed)) == 20
    assert all((max(c.end_sample for c in group) - group[0].start_sample) / RATE <= 6 for group in completed)


def test_cross_stream_or_species_never_merge_and_active_memory_bounded():
    aggregator = Aggregator(Policy(max_active=2))
    done = []
    for index in range(20):
        done += aggregator.add(candidate(stream_id=f"stream-{index}"), index)
        assert len(aggregator.active) <= 2
    assert len(done) == 18
    assert len(aggregator.finish()) == 2


def test_duplicate_candidate_does_not_boost_support():
    aggregator = Aggregator(Policy())
    item = candidate()
    aggregator.add(item, 0)
    aggregator.add(item, 1)
    assert aggregator.finish() == [[item]]


def test_idle_event_closes_without_new_model_candidate():
    aggregator = Aggregator(Policy(idle_seconds=2))
    item = candidate()
    aggregator.add(item, 0)
    assert aggregator.advance(0, "stream-1", 1) == []
    assert aggregator.advance(0, "stream-1", 2) == [[item]]


@pytest.mark.parametrize("changes", [
    {"auto_single": .5}, {"max_event_seconds": 100}, {"required_windows": 1},
    {"target_domains": ["mammal"]}, {"review_days": 0}, {"review_lower": float("nan")},
    {"auto_supported": .95}, {"max_supports": 100}, {"max_active": 1000},
])
def test_invalid_policy(changes):
    with pytest.raises(ValueError):
        Policy(**changes)


@pytest.mark.parametrize("date,week", [
    ("2026-01-01T00:00:00Z", 1), ("2026-01-07T00:00:00Z", 1),
    ("2026-01-08T00:00:00Z", 2), ("2026-01-31T00:00:00Z", 4),
    ("2026-02-01T00:00:00Z", 5), ("2026-12-31T00:00:00Z", 48),
])
def test_geomodel_48_week_bins(date, week):
    assert birdnet_week(date) == week


def test_geo_signal_is_not_a_species_filter(monkeypatch):
    model, context, session = Mock(), MagicMock(), Mock()
    context.__enter__.return_value = session
    model.predict_session.return_value = context
    session.run.return_value.to_structured_array.return_value = [
        {"species_name": "Parus major_Great Tit", "confidence": .01}]
    load = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "birdnet", SimpleNamespace(load=load))
    provider = GeoPlausibility(52, 5)
    load.assert_called_once_with("geo", "3.0", "onnx", precision="fp32")
    assert session.run.call_count == 48
    assert session.run.call_args_list[0].kwargs == {"week": 1}
    assert session.run.call_args_list[-1].kwargs == {"week": 48}
    prediction = Prediction("Parus major", "Great Tit", .96, 0, 3)
    enriched = provider.annotate([prediction])[0]
    assert enriched.confidence == .96
    signal = plausibility_at(enriched.plausibility, "2026-10-01T00:00:00Z", Policy())
    assert signal["state"] == "unusual" and signal["week"] == 37
    assert "weekly_scores" not in signal
    assert decision([candidate(score=.96, state=signal["state"])], Policy())["evidence"] == "permanent"
    absent = provider.annotate([replace(prediction, scientific_name="Unmapped bird")])[0]
    assert plausibility_at(absent.plausibility, "2026-10-01T00:00:00Z", Policy())["state"] == "unknown"


def test_disallowed_target_domain():
    assert decision([candidate(domain="bat")], Policy(target_domains=("bird",)))["status"] == "discarded"
