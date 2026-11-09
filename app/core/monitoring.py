"""Explicit deployment start date, separate from first species activity."""
import json

def monitoring_started_at(settings):
    path = settings.resolved_storage_root / "monitoring-start.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["started_at"]
