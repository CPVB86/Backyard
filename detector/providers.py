"""BirdNET-specific taxonomy and optional non-destructive geographical signals."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from observations.policy import domain_from_taxonomy

CATALOG = Path(__file__).with_name("data") / "acoustic_taxonomy.json"


class AcousticTaxonomy:
    def __init__(self):
        document = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.taxa = document["taxa"]
        self.provenance = {"provider": "birdnet-acoustic-label-taxonomy",
                           "sha256": document["sha256"], "source": document["source"]}

    def resolve(self, scientific_name):
        row = self.taxa.get(scientific_name)
        if row is None:
            return "unknown", dict(self.provenance, reason="unresolved_taxonomy")
        taxon = domain_from_taxonomy(*row)
        # Acoustic BirdNET is deliberately only enabled for birds here.
        # A real future bat adapter must validate its own detector/hardware.
        domain = "bird" if taxon == "bird" else "unsupported"
        return domain, dict(self.provenance, taxon_domain=taxon, class_name=row[0],
                            order_name=row[1], reason="supported_bird" if domain == "bird"
                            else "birdnet_bat_pipeline_not_enabled" if taxon == "bat"
                            else "outside_target_domain")


def birdnet_week(timestamp):
    """BirdNET's 48 month-quarter bins, NOT ISO week number; use UTC dates."""
    day = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
    return (day.month - 1) * 4 + min((day.day - 1) // 7 + 1, 4)


def plausibility_at(signal, timestamp, policy):
    if not signal or "weekly_scores" not in signal:
        return signal or {"state": "unknown", "provider": "disabled"}
    week = birdnet_week(timestamp)
    score = signal["weekly_scores"][week - 1]
    if score is None:
        state = "unknown"
    else:
        state = "normal" if score >= policy.geo_normal else "unusual"
    return {key: value for key, value in signal.items() if key != "weekly_scores"} | {
        "state": state, "week": week, "week_convention": "UTC month-quarter 1..48",
        "score": score, "normal_threshold": policy.geo_normal}


class GeoPlausibility:
    """Load a small independent geo model once, score all weeks before capture."""
    def __init__(self, latitude, longitude):
        import birdnet
        self.latitude, self.longitude = float(latitude), float(longitude)
        self.scores = {}
        model = birdnet.load("geo", "3.0", "onnx", precision="fp32")
        with model.predict_session(min_confidence=0.0, device="CPU", half_precision=False) as session:
            for week in range(1, 49):
                rows = session.run(self.latitude, self.longitude, week=week).to_structured_array()
                for row in rows:
                    name = str(row["species_name"]).partition("_")[0]
                    vector = self.scores.setdefault(name, [None] * 48)
                    vector[week - 1] = float(row["confidence"])

    def annotate(self, predictions):
        return [replace(prediction, plausibility={
            "provider": "birdnet-1.1.1-geo-3.0.4-onnx-fp32",
            "latitude": self.latitude, "longitude": self.longitude,
            "weekly_scores": self.scores.get(prediction.scientific_name, [None] * 48),
        }) for prediction in predictions]
