"""Explicit synthetic API diagnostic. Never invoked by the production monitor."""
import argparse
from datetime import datetime, timezone
import io
import json
import sys
from uuid import uuid4
import wave

from detector.monitor_http import HTTPTransport, HTTPFailure
from detector.providers import AcousticTaxonomy
from detector.stream import MonitorConfig
from observations.policy import Policy, RawCandidate, decision, observation_payload

CASES = {
    "bird-auto": ("bird", "Parus major", "Great Tit", .94, "normal"),
    "bird-review": ("bird", "Parus major", "Great Tit", .68, "normal"),
    "bird-unusual": ("bird", "Parus major", "Great Tit", .96, "unusual"),
    "bird-overlap": ("bird", "Parus major", "Great Tit", .71, "normal"),
    "bat": ("bat", "Pipistrellus pipistrellus", "Common Pipistrelle", .94, "normal"),
    "chimpanzee": ("unsupported", "Pan troglodytes", "Chimpanzee", .68, "unknown"),
}


def build(case, policy):
    domain, scientific, common, confidence, state = CASES[case]
    metadata = {"synthetic": True, "diagnostic_case": case, "audio": "synthetic silence; not animal evidence"}
    if case == "chimpanzee":
        domain, metadata["taxonomy"] = AcousticTaxonomy().resolve(scientific)
    stream = str(uuid4())
    anchor = datetime.now(timezone.utc).isoformat()
    scores = (.71, .91, .84) if case == "bird-overlap" else (confidence,)
    supports = [
        RawCandidate(f"{stream}:{index}", f"{stream}:window:{index}", "synthetic-observation-debug",
                     stream, domain, scientific, common, score, 8000,
                     8000 + index * 12000, 32000 + index * 12000, anchor, "synthetic-debug-1",
                     {"state": state, "provider": "synthetic-debug"}, metadata)
        for index, score in enumerate(scores)
    ]
    return observation_payload(supports, policy), decision(supports, policy)


def silence(payload):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(payload["candidates"][0]["sample_rate"])
        output.writeframes(bytes(2 * (payload["clip_end_sample"] - payload["clip_start_sample"])))
    return stream.getvalue()


def run(case, policy, transport=None):
    payload, outcome = build(case, policy)
    if transport is None:
        return {"sent": False, "synthetic": True, "decision": outcome, "payload": payload}
    item = transport.request("POST", "/api/observations",
                             json.dumps(payload, allow_nan=False).encode(), "application/json")
    if item["status"] != "discarded":
        item = transport.request("PUT", f"/api/observations/{item['id']}/audio",
                                 silence(payload), "audio/wav")
    return {"sent": True, "synthetic": True, **item}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--send", action="store_true", help="Explicitly create synthetic test records via HTTP")
    parser.add_argument("--api-url", default="http://127.0.0.1:8010")
    args = parser.parse_args(argv)
    try:
        config = MonitorConfig(api_url=args.api_url)
        transport = HTTPTransport(config.api_url, 5, max_response=512 * 1024) if args.send else None
        # Use the server's active settings; no accidental policy mismatch.
        policy = Policy(**transport.request("GET", "/api/observations/policy", None, "application/json")["values"]) if transport else Policy.load()
        print(json.dumps(run(args.case, policy, transport), ensure_ascii=True))
    except (OSError, ValueError, TypeError, HTTPFailure, KeyError) as error:
        print(f"Debug observation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
