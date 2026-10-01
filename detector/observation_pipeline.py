"""Bounded policy/aggregation stage, independent of capture, inference and HTTP."""
import time
from observations.policy import RawCandidate, Aggregator, decision, observation_payload
from detector.providers import AcousticTaxonomy, plausibility_at
from detector.stream import Candidate


class ObservationPipeline:
    def __init__(self, config, policy, incoming, clips, metrics, anchor_getter):
        self.config, self.policy, self.incoming, self.clips, self.metrics = config, policy, incoming, clips, metrics
        self.anchor_getter = anchor_getter
        self.taxonomy = AcousticTaxonomy()
        self.aggregator = Aggregator(policy)
        self.stream_id = ""
        self.watermark = 0

    def emit(self, supports):
        outcome = decision(supports, self.policy)
        self.metrics.add("aggregation_count", max(0, len(supports) - 1))
        self.metrics.add("plausibility_" + outcome.get("plausibility", "unknown"))
        if outcome["status"] == "discarded":
            self.metrics.add("discarded_candidates", len(supports))
            self.metrics.set(last_discard_reasons=outcome["reasons"])
            return
        body = observation_payload(supports, self.policy, self.config.pre_roll, self.config.post_roll)
        candidate = Candidate(body, body["clip_start_sample"], body["clip_end_sample"])
        if self.clips.put(candidate) is not None:
            self.metrics.add("clips_dropped")
        self.metrics.add("observations_planned")
        self.metrics.add("auto_accepted" if outcome["status"] == "auto_accepted" else "review_observations")
        self.metrics.add(outcome["evidence"] + "_clips_planned")
        self.metrics.set(last_observation={
            "domain": supports[0].domain, "species": supports[0].scientific_name,
            "confidence": outcome["best_confidence"], "supports": len(supports),
            "status": outcome["status"], "evidence": outcome["evidence"],
            "reasons": outcome["reasons"], "event_id": body["event_id"],
        })

    def tick(self):
        batch = self.incoming.take()
        now = time.monotonic()
        if batch is not None:
            window, predictions = batch
            anchor = self.anchor_getter()
            self.stream_id = anchor.session_id
            for prediction in predictions:
                self.metrics.add("policy_candidates")
                domain, taxonomy = self.taxonomy.resolve(prediction.scientific_name)
                if domain not in self.policy.target_domains:
                    self.metrics.add("unsupported_domain_candidates")
                    self.metrics.add("discarded_candidates")
                    self.metrics.set(last_discard_reasons=["outside_target_domain"], last_excluded_taxon=prediction.scientific_name)
                    continue
                start = window.start + round(prediction.start_seconds * self.config.rate)
                end = window.start + round(prediction.end_seconds * self.config.rate)
                if not window.start <= start < end <= window.end:
                    raise ValueError("Candidate outside window")
                from uuid import uuid5, NAMESPACE_URL
                window_id = f"{anchor.session_id}:{window.start}:{window.end}"
                identity = str(uuid5(NAMESPACE_URL, window_id + "/" + prediction.scientific_name))
                candidate = RawCandidate(
                    identity, window_id, "backyard-birdnet-monitor", anchor.session_id,
                    domain, prediction.scientific_name, prediction.common_name, prediction.confidence,
                    self.config.rate, start, end, anchor.utc.isoformat(),
                    "birdnet-1.1.1-acoustic-3.0-preview3.1-onnx-fp32",
                    plausibility_at(getattr(prediction, "plausibility", None), anchor.timestamp(start), self.policy),
                    {"taxonomy": taxonomy, "device": anchor.device, "monotonic_anchor": anchor.monotonic,
                     "window_start_sample": window.start, "window_end_sample": window.end,
                     "model_start_seconds": prediction.start_seconds, "model_end_seconds": prediction.end_seconds,
                     "hop_samples": self.config.hop_samples, "capture_gaps": 0},
                )
                for supports in self.aggregator.add(candidate, now):
                    self.emit(supports)
            self.watermark = window.start + self.config.hop_samples
        for supports in self.aggregator.advance(self.watermark, self.stream_id, now):
            self.emit(supports)
        self.metrics.set(aggregation_active=len(self.aggregator.active))

    def abandon(self):
        pending = self.aggregator.finish()
        self.metrics.add("aggregation_abandoned_candidates", sum(map(len, pending)))
