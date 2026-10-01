"""Wait for real API readiness; failure lets systemd retry after its delay."""
import argparse
import os
import time
from operations.health import check_health
from detector.monitor_http import HTTPTransport
from observations.policy import Policy


def wait(url, timeout=60, check_policy=False):
    deadline = time.monotonic() + timeout
    print(f"Waiting up to {timeout}s for Backyard health at {url}", flush=True)
    last_error = "not yet contacted"
    while time.monotonic() < deadline:
        try:
            check_health(url, min(2, max(.1, deadline - time.monotonic())))
            if check_policy:
                remote = HTTPTransport(url, 2, max_response=4096).request(
                    "GET", "/api/observations/policy", None, "application/json")
                if remote.get("fingerprint") != Policy.load().fingerprint:
                    raise ValueError("API/monitor policy mismatch; share BACKYARD_POLICY_PATH in backyard.env")
            print("Backyard API and database ready.", flush=True)
            return True
        except (OSError, RuntimeError, ValueError) as error:
            last_error = str(error)
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    print(f"Backyard API not ready: {last_error}; service startup will retry.", flush=True)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("BACKYARD_MONITOR_API_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--timeout", type=int, choices=range(1, 301), default=60, metavar="1..300")
    parser.add_argument("--check-policy", action="store_true")
    args = parser.parse_args(argv)
    return 0 if wait(args.url, args.timeout, args.check_policy) else 1


if __name__ == "__main__":
    raise SystemExit(main())
