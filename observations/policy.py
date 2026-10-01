"""Explainable policy shared by producers and API; no BirdNET/DB dependencies."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

VERSION = "observation-policy-1"


@dataclass(frozen=True)
class Policy:
    target_domains: tuple = ("bird", "bat")
    review_lower: float = 0.60
    auto_single: float = 0.90
    auto_supported: float = 0.85
    unknown_single: float = 0.97
    strong_unusual: float = 0.90
    required_windows: int = 2
    max_event_seconds: float = 9.0
    max_supports: int = 12
    max_active: int = 64
    idle_seconds: float = 5.0
    geo_normal: float = 0.03
    review_days: int = 14

    def __post_init__(self):
        object.__setattr__(self, "target_domains", tuple(self.target_domains))
        if not self.target_domains or set(self.target_domains) - {"bird", "bat"}:
            raise ValueError("target_domains must contain bird and/or bat")
        for name in ("review_lower", "auto_single", "auto_supported", "unknown_single",
                     "strong_unusual", "geo_normal", "max_event_seconds", "idle_seconds"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"Nonfinite {name}")
        if not 0 <= self.review_lower <= self.auto_supported <= self.auto_single <= self.unknown_single <= 1:
            raise ValueError("Require review_lower <= auto_supported <= auto_single <= unknown_single")
        if not self.auto_supported <= self.strong_unusual <= 1 or not 0 <= self.geo_normal <= 1:
            raise ValueError("Invalid unusual/geographical threshold")
        for name, low, high in (("required_windows", 2, 12), ("max_supports", 2, 32),
                                ("max_active", 1, 128), ("review_days", 1, 365)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be integer {low}..{high}")
        if self.required_windows > self.max_supports or not 3 <= self.max_event_seconds <= 30:
            raise ValueError("Invalid support/span limits")
        if not 1 <= self.idle_seconds <= 15:
            raise ValueError("idle_seconds must be 1..15")

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path=None):
        provided = path or os.environ.get("BACKYARD_POLICY_PATH")
        root = Path(__file__).resolve().parents[1]
        path = Path(provided) if provided else root / "policy.json"
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            if provided:
                raise ValueError(f"Policy file not found: {path}")
            return cls()
        return cls(**json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RawCandidate:
    candidate_id: str
    window_id: str
    source: str
    stream_id: str
    domain: str
    scientific_name: str
    common_name: str
    confidence: float
    sample_rate: int
    start_sample: int
    end_sample: int
    utc_anchor: str
    model_version: str
    plausibility: dict = field(default_factory=lambda: {"state": "unknown", "provider": "disabled"})
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.domain not in ("bird", "bat", "unsupported", "unknown"):
            raise ValueError("Invalid candidate domain")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid confidence")
        if any(type(value) is not int for value in (self.sample_rate, self.start_sample, self.end_sample)):
            raise ValueError("Sample coordinates must be integers")
        if not 8000 <= self.sample_rate <= 192000 or not 0 <= self.start_sample < self.end_sample:
            raise ValueError("Invalid sample range")
        if self.end_sample - self.start_sample > 30 * self.sample_rate:
            raise ValueError("Candidate window exceeds 30s")
        if self.plausibility.get("state") not in ("normal", "unusual", "unknown"):
            raise ValueError("Invalid plausibility")
        anchor = datetime.fromisoformat(self.utc_anchor)
        if anchor.tzinfo is None:
            raise ValueError("UTC anchor needs timezone")
        try:
            self.timestamp(self.end_sample)
        except (OverflowError, ValueError):
            raise ValueError("Sample timestamp outside supported datetime range") from None
        for name in ("candidate_id", "window_id", "source", "stream_id",
                     "scientific_name", "common_name", "model_version"):
            if not isinstance(getattr(self, name), str) or not 1 <= len(getattr(self, name)) <= 255:
                raise ValueError(f"Invalid {name}")

    @property
    def duration(self):
        return (self.end_sample - self.start_sample) / self.sample_rate

    def timestamp(self, sample):
        anchor = datetime.fromisoformat(self.utc_anchor).astimezone(timezone.utc)
        return (anchor + timedelta(seconds=sample / self.sample_rate)).isoformat()

    @property
    def grouping_key(self):
        return (self.source, self.stream_id, self.domain, self.scientific_name,
                self.model_version, self.sample_rate, self.utc_anchor)


def domain_from_taxonomy(class_name, order_name):
    if class_name.lower() == "aves":
        return "bird"
    if class_name.lower() == "mammalia" and order_name.lower() == "chiroptera":
        return "bat"
    return "unsupported"


def decision(supports, policy):
    if not supports:
        raise ValueError("No candidates")
    if any(c.domain not in policy.target_domains for c in supports):
        return {"status": "discarded", "evidence": "none", "reasons": ["outside_target_domain"]}
    best = max(c.confidence for c in supports)
    states = {c.plausibility["state"] for c in supports}
    state = "unusual" if "unusual" in states else "unknown" if "unknown" in states else "normal"
    windows = len({c.window_id for c in supports if c.confidence >= policy.review_lower})
    reasons = ["target_domain", f"plausibility_{state}"]
    if windows >= policy.required_windows:
        reasons.append("overlapping_window_support")
    if best < policy.review_lower:
        status, evidence = "discarded", "none"
        reasons.append("below_review_lower")
    elif state == "unusual":
        if best >= policy.strong_unusual:
            status, evidence = "review_recommended", "permanent"
            reasons.append("strong_unusual_preserve")
        elif windows >= policy.required_windows and best >= policy.auto_supported:
            status, evidence = "pending_review", "review"
            reasons.append("unusual_supported_review")
        else:
            status, evidence = "discarded", "none"
            reasons.append("unusual_insufficient_support")
    elif ((state == "normal" and best >= policy.auto_single)
          or (state == "unknown" and best >= policy.unknown_single)
          or (windows >= policy.required_windows and best >= policy.auto_supported)):
        status, evidence = "auto_accepted", "permanent"
        reasons.append("strong_single" if windows < policy.required_windows else "supported_confidence")
    else:
        status, evidence = "pending_review", "review"
        reasons.append("gray_zone_preserve_for_review")
    return {"status": status, "evidence": evidence, "reasons": reasons,
            "plausibility": state, "best_confidence": best, "supporting_windows": windows,
            "policy_version": VERSION, "policy_fingerprint": policy.fingerprint}


def validate_event(supports, policy):
    if not 1 <= len(supports) <= policy.max_supports:
        raise ValueError("Invalid supporting candidate count")
    first = supports[0]
    end = first.end_sample
    seen_ids, seen_windows, seen_ranges = set(), set(), set()
    previous_start = first.start_sample
    for index, item in enumerate(supports):
        if item.grouping_key != first.grouping_key:
            raise ValueError("Mixed observation identity")
        if (item.candidate_id in seen_ids or item.window_id in seen_windows
                or (item.start_sample, item.end_sample) in seen_ranges):
            raise ValueError("Duplicate candidate/window support")
        if index and not previous_start <= item.start_sample < end:
            raise ValueError("Supports must be ordered and genuinely overlap")
        seen_ids.add(item.candidate_id)
        seen_windows.add(item.window_id)
        seen_ranges.add((item.start_sample, item.end_sample))
        previous_start = item.start_sample
        end = max(end, item.end_sample)
    if (end - first.start_sample) / first.sample_rate > policy.max_event_seconds:
        raise ValueError("Observation exceeds maximum event span")
    return first.start_sample, end


def observation_payload(supports, policy, pre_roll=1.0, post_roll=1.0):
    start, end = validate_event(supports, policy)
    first = supports[0]
    return {
        "event_id": str(uuid5(NAMESPACE_URL, first.source + "/" + first.candidate_id)),
        "policy_fingerprint": policy.fingerprint,
        "candidates": [asdict(candidate) for candidate in supports],
        "clip_start_sample": max(0, start - round(pre_roll * first.sample_rate)),
        "clip_end_sample": end + round(post_roll * first.sample_rate),
    }


class Aggregator:
    """Only actual sample overlap, same stream/label/model, bounded span/support."""
    def __init__(self, policy):
        self.policy = policy
        self.active = {}  # key -> (supports, last monotonic arrival)

    def add(self, candidate, now):
        completed = []
        if candidate.domain not in self.policy.target_domains or candidate.confidence < self.policy.review_lower:
            return [[candidate]]
        key = candidate.grouping_key
        if key in self.active:
            support, _ = self.active[key]
            if any(c.candidate_id == candidate.candidate_id for c in support):
                return []
            try:
                validate_event(support + [candidate], self.policy)
            except ValueError:
                completed.append(support)
                del self.active[key]
        if key not in self.active:
            if len(self.active) >= self.policy.max_active:
                oldest = min(self.active, key=lambda k: self.active[k][1])
                completed.append(self.active.pop(oldest)[0])
            self.active[key] = ([candidate], now)
        else:
            supports, _ = self.active[key]
            self.active[key] = (supports + [candidate], now)
        return completed

    def advance(self, next_start, stream_id, now):
        done = []
        for key, (supports, last) in list(self.active.items()):
            if ((supports[0].stream_id == stream_id and next_start >= max(c.end_sample for c in supports))
                    or now - last >= self.policy.idle_seconds):
                done.append(self.active.pop(key)[0])
        return done

    def finish(self):
        result = [supports for supports, _ in self.active.values()]
        self.active.clear()
        return result
