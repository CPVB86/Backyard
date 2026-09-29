"""File-in/file-results-out seam. No HTTP client, database or capture imports."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Prediction:
    scientific_name: str
    common_name: str
    confidence: float
    start_seconds: float
    end_seconds: float


def normalize(rows):
    result = []
    for row in rows:
        name = str(row["species_name"])
        scientific, separator, common = name.partition("_")
        score = float(row["confidence"])
        start, end = float(row["start_time"]), float(row["end_time"])
        if (not scientific or not separator or not common or not math.isfinite(score)
                or not 0 <= score <= 1 or not math.isfinite(start)
                or not math.isfinite(end) or not 0 <= start < end):
            raise ValueError("Onverwacht BirdNET-resultaatcontract")
        result.append(Prediction(scientific, common, score, start, end))
    # Preserve every window, including overlapping detections of the same species.
    return sorted(result, key=lambda row: (row.start_seconds, -row.confidence, row.scientific_name))


class BirdNETFileAnalyzer:
    def __init__(self):
        # Lazy: help, capture and repository tests do not need ML dependencies.
        import birdnet
        self.model = birdnet.load("acoustic", "3.0", "onnx", precision="fp32")

    def analyze(self, path, *, top_k=5, overlap=0.0):
        if not 1 <= top_k <= 20 or not math.isfinite(overlap) or not 0 <= overlap < 3:
            raise ValueError("top_k moet 1-20 zijn; overlap moet 0 <= overlap < 3 zijn")
        result = self.model.predict(
            str(path), top_k=top_k, default_confidence_threshold=0.0,
            n_producers=1, n_workers=1, batch_size=1, prefetch_ratio=1,
            overlap_duration_s=overlap, device="CPU", show_stats=None,
        )
        if len(result.unprocessable_inputs):
            raise RuntimeError("BirdNET kon het bestand niet analyseren")
        return normalize(result.to_structured_array())
