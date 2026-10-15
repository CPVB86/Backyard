"""Read-only 24-hour operations report: services, health, journal, DB and audio."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from app.core.config import Settings
from detector.providers import geo_configuration, birdnet_week
from operations.environment import read_environment
from operations.health import check_health
from operations.inventory import database_inventory, audio_inventory
from operations.journal import read_journal

UNITS = ("backyard-api.service", "backyard-detector.service")
PROPERTIES = ("Id", "ActiveState", "SubState", "UnitFileState", "User", "MainPID",
              "NRestarts", "InvocationID", "ActiveEnterTimestamp", "ExecMainStartTimestamp",
              "MemoryCurrent", "MemoryPeak", "CPUUsageNSec", "TasksCurrent",
              "ControlGroup", "Result", "ExecMainStatus")


def service_status(unit):
    result = subprocess.run(["systemctl", "show", unit, "--no-pager",
                             "--property=" + ",".join(PROPERTIES)],
                            capture_output=True, text=True, timeout=10, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def system_inventory(storage_root):
    result = {}
    try:
        result["disk"] = dict(zip(("total", "used", "free"), shutil.disk_usage(storage_root)))
    except OSError as error:
        result["disk_error"] = str(error)
    memory = Path("/proc/meminfo")
    if memory.exists():
        values = dict(line.split(":", 1) for line in memory.read_text().splitlines())
        result["ram_kib"] = {key: int(values[key].split()[0]) for key in ("MemTotal", "MemAvailable")}
    if hasattr(os, "getloadavg"):
        result["load_average_1_5_15"] = os.getloadavg()
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if thermal.exists():
            result["temperature_c"] = float(thermal.read_text().strip()) / 1000
    except (OSError, ValueError):
        result["temperature_c"] = None
    return result


def warnings(report, stale_seconds=90):
    notes = []
    geo = report.get("geo", {})
    if not geo.get("active") or geo.get("status") != "ready" or geo.get("error"):
        notes.append("Geo plausibility unavailable: " + str(geo.get("error") or geo.get("status", "missing status")))
    for unit in (("backyard-api.service", "backyard-birdnet-go.service", "backyard-birds.service")
                 if report.get("bird_source") == "go" else UNITS):
        state = report.get("services", {}).get(unit, {})
        if state.get("ActiveState") != "active" or state.get("SubState") != "running":
            notes.append(unit + " is not active/running")
        if state.get("UnitFileState") != "enabled":
            notes.append(unit + " is not enabled for boot")
        if unit != "backyard-birdnet-go.service" and state.get("User") != "cpvb86":
            notes.append(unit + " is not configured as cpvb86")
    if "error" in report.get("api_health", {}):
        notes.append("API health unavailable")
    journal = report.get("journal", {})
    latest = journal.get("latest")
    if report.get("bird_source") != "go":
        if "error" in journal:
            notes.append("Journal unavailable: cannot assess the previous night")
        if not latest:
            notes.append("No detector status samples: startup, permissions or missing history")
        else:
            if report["timestamp"] - latest["timestamp"] > stale_seconds:
                notes.append("Latest detector metrics are stale")
            current = report.get("services", {}).get("backyard-detector.service", {}).get("InvocationID")
            if current != latest["invocation"]:
                notes.append("Latest metrics belong to a different service invocation")
            if latest["metrics"].get("phase") != "running":
                notes.append("Latest detector status is not running")
            if latest["metrics"].get("windows_processed", 0) == 0:
                notes.append("No inference windows processed in latest detector status")
            if latest["metrics"].get("realtime_ratio", 0) >= 1:
                notes.append("Average inference is not keeping up with incoming audio")
        if (journal.get("history_starts_late_by_seconds") or 0) > stale_seconds:
            notes.append("Status history starts after requested interval; full-duration coverage is not proven")
        if journal.get("max_sample_gap_seconds", 0) > stale_seconds:
            notes.append("Historical gap between status samples; uninterrupted monitoring is not proven")
        totals = journal.get("observed_counter_deltas", {})
        for key in ("capture_gaps", "alsa_overruns", "ring_overruns", "windows_dropped",
                    "inference_timeouts", "policy_batches_dropped", "clips_dropped",
                    "clips_expired", "uploads_failed", "uploads_dropped", "http_failures"):
            if totals.get(key, 0):
                notes.append(f"Observed {key}={totals[key]} in requested interval")
        if journal.get("detector_invocations_seen", 0) > 1 or journal.get("api_invocations_seen", 0) > 1:
            notes.append("Multiple service invocations observed; inspect restarts and downtime")
        if any(journal.get(k) for k in ("record_limit_reached", "invalid_records", "oversized_records", "journal_notice")):
            notes.append("Journal report may be incomplete; inspect limits/invalid records/notices")
    else:
        bird = report.get("birds", {})
        if bird.get("phase") != "running" or bird.get("stale", True):
            notes.append("Bird Go bridge is unavailable, retrying or stale")
        notes.append("Go bridge status does not prove capture/inference; inspect BirdNET-Go pipeline stats")
    if "error" in report.get("database", {}):
        notes.append("Database inventory unavailable")
    check = report.get("database", {}).get("quick_check")
    if check is not None and check != ["ok"]:
        notes.append("SQLite quick_check did not return ok")
    if not report.get("audio", {}).get("complete", False):
        notes.append("Audio inventory incomplete")
    disk = report.get("system", {}).get("disk")
    if disk and disk["free"] < max(1024**3, disk["total"] * .05):
        notes.append("Storage has less than 1 GiB or 5% free")
    if "error" in report.get("system", {}):
        notes.append("System resource information unavailable")
    if report.get("system", {}).get("disk_error"):
        notes.append("Storage filesystem unavailable")
    return notes


def geo_report(environment, journal, services, now):
    configured = geo_configuration(
        environment.get("BACKYARD_MONITOR_GEOGRAPHY", "0").lower() in ("1", "true", "yes"),
        environment.get("BACKYARD_MONITOR_LATITUDE"), environment.get("BACKYARD_MONITOR_LONGITUDE"))
    current_week = birdnet_week(datetime.fromtimestamp(now, timezone.utc).isoformat())
    result = configured | {"week": current_week, "week_convention": "UTC month-quarter 1..48"}
    latest = journal.get("latest") or {}
    runtime = latest.get("metrics", {}).get("geo")
    if configured["error"]:
        return result
    if (not isinstance(runtime, dict) or now - latest.get("timestamp", 0) > 90
            or latest.get("invocation") != services.get("backyard-detector.service", {}).get("InvocationID")):
        return result | {"status": "unavailable", "error": "No fresh geo provider status from current detector"}
    result.update(runtime)
    result.update(configured_latitude=configured["latitude"], configured_longitude=configured["longitude"])
    if (runtime.get("latitude"), runtime.get("longitude")) != (configured["latitude"], configured["longitude"]):
        result.update(active=False, error="Runtime location differs from backyard.env; restart detector")
    if runtime.get("week") != current_week:
        result.update(active=False, error="Runtime seasonal period is stale; inspect detector clock/status")
    return result


def collect(settings, environment, hours=24, check_db=False, now=None):
    now = time.time() if now is None else now
    since = now - hours * 3600
    report = {"at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "timestamp": now, "requested_hours": hours, "services": {}}
    def attempt(action):
        try:
            return action()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return {"error": str(error)[:500]}
    for unit in UNITS + (("backyard-bats.service",) if environment.get("BACKYARD_BAT_INSTANCE") else ()):
        report["services"][unit] = attempt(lambda unit=unit: service_status(unit))
    report["api_health"] = attempt(lambda: check_health(
        environment.get("BACKYARD_MONITOR_API_URL", "http://127.0.0.1:8010"),
        api_token=settings.api_token.get_secret_value()))
    report["journal"] = attempt(lambda: read_journal(since, now))
    # SQLite errors are RuntimeError-independent, handled without modifying DB.
    import sqlite3
    try:
        report["database"] = database_inventory(settings.resolved_database_path, since, check_db)
    except (OSError, ValueError, sqlite3.Error) as error:
        report["database"] = {"error": str(error)[:500]}
    report["audio"] = attempt(lambda: audio_inventory(settings.resolved_storage_root, since))
    report["system"] = attempt(lambda: system_inventory(settings.resolved_storage_root))
    report["geo"] = geo_report(environment, report["journal"], report["services"], now)
    report["bird_source"] = "python"
    if environment.get("BACKYARD_GO_BIRD_INSTANCE"):
        from operations.bats import bridge_status
        for unit in ("backyard-birdnet-go.service", "backyard-birds.service"):
            report["services"][unit] = attempt(lambda unit=unit: service_status(unit))
        report["birds"] = bridge_status(environment, now, domain="bird")
        if report["services"]["backyard-birdnet-go.service"].get("ActiveState") == "active":
            report["bird_source"] = "go"
            report["geo"] = dict(report["birds"].get("geo", {}))
            expected = geo_configuration(True, environment.get("BACKYARD_MONITOR_LATITUDE"),
                                         environment.get("BACKYARD_MONITOR_LONGITUDE"))
            if (expected["error"] or
                    (report["geo"].get("latitude"), report["geo"].get("longitude")) !=
                    (expected["latitude"], expected["longitude"])):
                report["geo"].update(active=False, error="Go bridge location differs from environment; restart bridge")
            if report["geo"].get("week") != birdnet_week(report["at"]):
                report["geo"].update(active=False, error="Go bridge seasonal period is stale")
            if report["birds"].get("stale", True):
                report["geo"].update(active=False, error="Go bird bridge status is stale")

    interval = float(environment.get("BACKYARD_MONITOR_STATUS_SECONDS", "30"))
    report["warnings"] = warnings(report, max(90, 3 * interval))
    if environment.get("BACKYARD_BAT_INSTANCE"):
        from operations.bats import bridge_status
        report["bats"] = bridge_status(environment, now)
        state = report["services"].get("backyard-bats.service", {})
        if state.get("ActiveState") != "active" or state.get("SubState") != "running":
            report["warnings"].append("backyard-bats.service is not active/running")
        if report["bats"].get("phase") != "running" or report["bats"].get("stale", True):
            report["warnings"].append("Bat bridge is unavailable, retrying or stale; inspect its journal")
    report["health_summary"] = "ATTENTION" if report["warnings"] else "CURRENTLY_OK"
    report["note"] = "CURRENTLY_OK is not proof of 24h coverage. Check journal sessions, timestamps, restarts, sampled losses and disk growth."
    return report


def print_report(report):
    print(f"Backyard: {report['health_summary']} | {report['at']} | last {report['requested_hours']}h")
    for unit, state in report["services"].items():
        print(f"{unit}: {state.get('ActiveState','unknown')}/{state.get('SubState','unknown')} "
              f"boot={state.get('UnitFileState','unknown')} user={state.get('User','unknown')} "
              f"pid={state.get('MainPID','?')} restarts={state.get('NRestarts','?')}")
        print("  RAM/peak bytes:", state.get("MemoryCurrent","?"), state.get("MemoryPeak","?"),
              "| CPU ns:", state.get("CPUUsageNSec","?"), "| tasks:", state.get("TasksCurrent","?"))
        if "error" in state:
            print(" ", state["error"])
    print("Bird source:", report.get("bird_source", "python"))
    if "birds" in report:
        print("Bird Go bridge:", json.dumps(report["birds"]))
    print("API health:", json.dumps(report["api_health"]))
    print("Geo/provider:", json.dumps(report.get("geo", {})))
    if "bats" in report:
        print("Bat bridge:", json.dumps(report["bats"]))
    journal = report["journal"]
    latest = journal.get("latest")
    print("Latest detector metrics:", latest["at"] if latest else "UNAVAILABLE")
    groups = {
        "capture": ("uptime_seconds", "samples_captured", "capture_gaps", "alsa_overruns", "ring_overruns"),
        "inference": ("windows_processed", "windows_dropped", "inference_timeouts", "realtime_ratio", "realtime_ratio_last"),
        "observations": ("raw_candidates", "relevant_domain_candidates", "observations_created", "auto_accepted", "review_observations", "aggregation_count"),
        "filter": ("unsupported_domain_candidates", "discarded_candidates", "plausibility_normal", "plausibility_unusual", "plausibility_unknown"),
        "evidence/http": ("permanent_clips", "review_clips", "http_failures", "uploads_failed", "uploads_dropped", "clips_expired", "clips_dropped"),
        "queues": ("inference_depth", "policy_depth", "clips_depth", "outbound_depth", "policy_batches_dropped"),
    }
    if latest:
        for name, keys in groups.items():
            print("  " + name + ": " + " ".join(f"{key}={latest['metrics'].get(key,0)}" for key in keys))
    print("Journal coverage:", journal.get("first_status_at"), "->", latest["at"] if latest else None,
          "| samples:", journal.get("status_samples",0), "| max gap s:", journal.get("max_sample_gap_seconds",0))
    print("Invocations observed API/detector:", journal.get("api_invocations_seen",0), journal.get("detector_invocations_seen",0))
    print("Sampled counter deltas in requested interval:")
    totals = journal.get("observed_counter_deltas", {})
    for name, keys in groups.items():
        print("  " + name + ": " + " ".join(f"{key}={totals[key]}" for key in keys if key in totals))
    print("Maximum observed ratios/backlog:", json.dumps(journal.get("max_observed",{})))
    for name in ("database", "audio", "system"):
        print(name + ": " + json.dumps(report[name]))
    for warning in report["warnings"]:
        print("ATTENTION:", warning)
    for name in ("error", "journal_notice"):
        if journal.get(name):
            print("Journal:", journal[name])
    print(report["note"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, choices=range(1, 169), default=24, metavar="1..168")
    parser.add_argument("--environment-file", type=Path, default=Path("/etc/backyard/backyard.env"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check-db", action="store_true", help="Also run read-only SQLite quick_check")
    args = parser.parse_args(argv)
    try:
        environment = read_environment(args.environment_file)
        # Match systemd EnvironmentFile precedence, keeping existing API .env.
        config = {key: environment["BACKYARD_" + key.upper()] for key in Settings.model_fields
                  if "BACKYARD_" + key.upper() in environment}
        settings = Settings(**config)
        report = collect(settings, environment, args.hours, args.check_db)
    except (OSError, ValueError) as error:
        parser.exit(2, f"Status configuration error: {error}\n")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 1 if report["warnings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
