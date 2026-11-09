"""Read-only status; no audio probes, subprocesses or credentials sent upstream."""
import json
from datetime import datetime, timezone
from urllib.request import Request as URLRequest, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/api/system", tags=["system"])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def item(status, reason, last_audio_at=None):
    return {"status": status, "online": {"online": True, "offline": False}.get(status),
            "reason": reason, "last_audio_at": last_audio_at}


def probe(url, rate):
    if not url:
        return item("unknown", "not_configured"), None
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return item("unknown", "invalid_configuration"), None
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(URLRequest(url, headers={"Accept": "application/json"}), timeout=2) as response:
            raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        value = json.loads(raw)
        allowed = {"online": {"audio_transmitting"},
                   "offline": {"capture_failed", "audio_stalled"},
                   "unknown": {"no_active_receiver", "starting"}}
        if (value.get("schema_version") != 1 or value.get("service") != "byremote-audio"
                or value.get("sample_rate") != rate
                or value.get("reason") not in allowed.get(value.get("status"), set())):
            raise ValueError()
        stamp = value.get("last_audio_at")
        if stamp is not None:
            stamp = datetime.fromisoformat(stamp).isoformat()
        return item(value["status"], value["reason"], stamp), True
    except HTTPError:
        return item("unknown", "status_endpoint_unavailable"), True
    except (URLError, TimeoutError, OSError):
        return item("unknown", "remote_unreachable"), False
    except (ValueError, TypeError, AttributeError):
        return item("unknown", "invalid_status_response"), True


@router.get("/status")
def status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        with request.app.state.engine.connect() as db:
            db.execute(text("SELECT 1"))
        backyard = item("online", "api_database_ready")
    except SQLAlchemyError:
        backyard = item("offline", "database_unavailable")
    config = request.app.state.settings
    bird, bird_reachable = probe(config.remote_bird_health_url, 48000)
    bat, bat_reachable = probe(config.remote_bat_health_url, 384000)
    reachable = [bird_reachable, bat_reachable]
    remote = (item("online", "remote_responding") if True in reachable else
              item("offline", "remote_unreachable") if reachable == [False, False] else
              item("unknown", "insufficient_status"))
    return {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
            "components": {"backyard": backyard, "backyard_remote": remote,
                           "bird_mic": bird, "bat_mic": bat}}
