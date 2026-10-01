"""Bounded local API health requests shared by startup and status."""
import http.client
from detector.monitor_http import HTTPTransport
from detector.stream import MonitorConfig


def check_health(url, timeout=2):
    MonitorConfig(api_url=url)  # Validate an origin, without credentials or redirects.
    try:
        health = HTTPTransport(url, timeout, max_response=4096).request(
            "GET", "/api/health", None, "application/json")
    except http.client.HTTPException as error:
        raise RuntimeError(f"Invalid HTTP health response: {error}") from error
    if health != {"status": "ok", "service": "backyard", "database": "ok"}:
        raise ValueError("Unexpected Backyard health response")
    return health
