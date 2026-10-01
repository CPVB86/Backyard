"""Bounded journal aggregation; never hide counter resets after a restart."""
from datetime import datetime, timezone
import json
import math
import subprocess
from threading import Timer

COUNTERS = (
    "samples_captured", "capture_gaps", "alsa_overruns", "ring_overruns",
    "windows_processed", "windows_dropped", "inference_timeouts",
    "raw_candidates", "policy_candidates", "relevant_domain_candidates", "observations_created", "observations_planned",
    "auto_accepted", "review_observations", "unsupported_domain_candidates",
    "discarded_candidates", "aggregation_count", "permanent_clips", "review_clips",
    "discarded_clips", "http_failures", "uploads_failed", "uploads_dropped",
    "clips_expired", "clips_dropped", "policy_batches_dropped",
    "plausibility_normal", "plausibility_unusual", "plausibility_unknown",
)
GAUGES = ("realtime_ratio_last", "inference_depth", "policy_depth", "clips_depth",
          "outbound_depth", "inference_oldest_seconds", "policy_oldest_seconds",
          "clips_oldest_seconds", "outbound_oldest_seconds")
MAX_RECORDS = 20000


def stamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class JournalSummary:
    def __init__(self, since):
        self.since = since
        self.sessions = {}
        self.api_invocations = set()
        self.records = 0
        self.samples = 0
        self.invalid = 0
        self.maxima = {key: 0 for key in GAUGES}
        self.latest = None
        self.first_timestamp = None
        self.max_sample_gap_seconds = 0

    def add(self, row):
        self.records += 1
        try:
            timestamp = int(row["__REALTIME_TIMESTAMP"]) / 1_000_000
            unit = row.get("_SYSTEMD_UNIT")
            invocation = row.get("_SYSTEMD_INVOCATION_ID")
            if unit == "backyard-api.service" and invocation:
                self.api_invocations.add(invocation)
            if unit != "backyard-detector.service":
                return
            message = row.get("MESSAGE", "")
            if not isinstance(message, str):
                return
            if message.startswith("Monitor gestopt: "):
                message = message[len("Monitor gestopt: "):]
            if not message.startswith("{"):
                return
            metrics = json.loads(message)
            if not isinstance(metrics, dict) or "samples_captured" not in metrics:
                return
            invocation = invocation or metrics.get("stream_id") or (row.get("_BOOT_ID", "") + ":" + row.get("_PID", "unknown"))
            for key in COUNTERS + GAUGES + ("uptime_seconds",):
                value = metrics.get(key, 0)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid numeric metric")
            uptime = float(metrics.get("uptime_seconds", 0))
            session = self.sessions.get(invocation)
            if session is None:
                # If the process predates the query, first sample is a lower-bound
                # baseline; do not present lifetime counters as 24-hour counts.
                began_in_range = timestamp - uptime >= self.since - 1
                baseline = {key: 0 if began_in_range else metrics.get(key, 0) for key in COUNTERS}
                session = self.sessions[invocation] = {
                    "invocation": invocation, "first_at": stamp(timestamp),
                    "last_at": stamp(timestamp), "baseline": baseline, "last": metrics,
                    "started_within_range": began_in_range,
                }
            session["last_at"] = stamp(timestamp)
            session["last"] = metrics
            self.samples += 1
            if self.first_timestamp is None:
                self.first_timestamp = timestamp
            if self.latest is not None:
                self.max_sample_gap_seconds = max(self.max_sample_gap_seconds, timestamp - self.latest["timestamp"])
            self.latest = {"at": stamp(timestamp), "timestamp": timestamp,
                           "invocation": invocation, "metrics": metrics}
            for key in GAUGES:
                self.maxima[key] = max(self.maxima[key], float(metrics.get(key, 0)))
        except (ValueError, TypeError, KeyError, OverflowError):
            self.invalid += 1

    def report(self):
        totals = {key: sum(max(0, s["last"].get(key, 0) - s["baseline"][key])
                           for s in self.sessions.values()) for key in COUNTERS}
        return {
            "since": stamp(self.since), "journal_records": self.records,
            "status_samples": self.samples, "invalid_records": self.invalid,
            "record_limit_reached": self.records >= MAX_RECORDS,
            "detector_invocations_seen": len(self.sessions),
            "api_invocations_seen": len(self.api_invocations),
            "observed_counter_deltas": totals, "max_observed": self.maxima,
            "first_status_at": stamp(self.first_timestamp) if self.first_timestamp is not None else None,
            "history_starts_late_by_seconds": max(0, self.first_timestamp - self.since) if self.first_timestamp is not None else None,
            "sample_span_seconds": self.latest["timestamp"] - self.first_timestamp if self.latest else 0,
            "max_sample_gap_seconds": self.max_sample_gap_seconds,
            "latest": self.latest,
            "sessions": [{"invocation": s["invocation"], "first_at": s["first_at"],
                          "last_at": s["last_at"], "started_within_range": s["started_within_range"]}
                         for s in self.sessions.values()],
            "measurement_note": "Sampled lower bounds, not a durable event ledger. Missing/rotated logs and the last interval before SIGKILL may be absent.",
        }


def read_journal(since, until):
    summary = JournalSummary(since)
    command = [
        "journalctl", "--no-pager", "--quiet", "--output=json",
        "--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,_SYSTEMD_INVOCATION_ID,_BOOT_ID,_PID,MESSAGE",
        "--unit=backyard-api.service", "--unit=backyard-detector.service",
        f"--since=@{since:.6f}", f"--until=@{until:.6f}", f"--lines={MAX_RECORDS}",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timer = Timer(20, process.kill)
    timer.start()
    oversized = 0
    try:
        while True:
            line = process.stdout.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                oversized += 1
                while line and not line.endswith(b"\n"):
                    line = process.stdout.readline(65537)
                continue
            try:
                summary.add(json.loads(line))
            except (ValueError, UnicodeError, TypeError):
                summary.invalid += 1
        error = process.stderr.read(4096).decode(errors="replace")
        code = process.wait(timeout=2)
        if code:
            raise RuntimeError(f"journalctl failed/timed out ({code}): {error.strip()}")
        result = summary.report()
        result["oversized_records"] = oversized
        if error.strip():
            result["journal_notice"] = error.strip()
        return result
    finally:
        timer.cancel()
        timer.join()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
