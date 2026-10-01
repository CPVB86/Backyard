from contextlib import closing
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import ROOT, Settings
from app.main import create_app
from detector.debug_observation import run
from detector.monitor import Monitor, main as monitor_main, parser
from detector.stream import MonitorConfig
from observations.policy import Policy
from operations.environment import read_environment
from operations.health import check_health
from operations.inventory import audio_inventory, database_inventory
from operations.journal import JournalSummary, read_journal
from operations.status import collect, warnings, main as status_main
from operations.wait_api import wait
from test_observation_debug import APITransport


def test_production_environment_uses_existing_monitor_parser(monkeypatch):
    values = read_environment(ROOT/"deploy/systemd/backyard.env.example")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = MonitorConfig(**vars(parser().parse_args([])))
    assert (config.device, config.rate, config.channels) == ("plughw:CARD=Device,DEV=0", 48000, 1)
    assert (config.window, config.overlap, config.threshold) == (3, 1.5, .60)
    assert config.api_url == "http://127.0.0.1:" + values["UVICORN_PORT"]
    assert config.status_seconds == 30 and config.inference_timeout == 60
    assert not config.capture_only and not config.geography
    assert config.lock_file == "/home/cpvb86/Backyard/data/monitor.lock"


@pytest.mark.parametrize("line", ["export KEY=value", "KEY=$(whoami)", "KEY=`whoami`", "KEY=one two", "invalid-key=value", "KEY='unfinished"])
def test_environment_reader_rejects_unsupported_syntax(tmp_path, line):
    path = tmp_path/"env"
    path.write_text(line)
    with pytest.raises(ValueError):
        read_environment(path)


def test_environment_reader_quotes_comments_and_empty_values(tmp_path):
    path = tmp_path/"env"
    path.write_text('# comment\nKEY="some value"\nEMPTY=\nVALUE=/path\n')
    assert read_environment(path) == {"KEY":"some value", "EMPTY":"", "VALUE":"/path"}


def test_systemd_execs_are_absolute_no_implicit_migration_or_shell():
    import configparser, shlex
    for name in ("backyard-api", "backyard-detector"):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT/"deploy/systemd"/(name+".service"))
        service = config["Service"]
        assert service["User"] == "cpvb86"
        assert service["WorkingDirectory"] == "/home/cpvb86/Backyard"
        assert service["EnvironmentFile"] == "/etc/backyard/backyard.env"
        assert service["Restart"] == "on-failure"
        assert service["KillMode"] == "mixed" and service["SendSIGKILL"] == "yes"
        assert config["Unit"]["StartLimitIntervalSec"] == "0"
        assert config["Install"]["WantedBy"] == "multi-user.target"
        assert "Requires" not in config["Unit"]  # API outage must not stop live capture.
        for key in ("ExecStart", "ExecStartPre", "ExecStartPost"):
            if key in service:
                args = shlex.split(service[key])
                assert args[0].startswith("/home/cpvb86/Backyard/.venv")
                assert args[1] == "-m"
                assert not any(word in service[key] for word in ("git pull", "migrate", "cleanup", "/bin/sh", "sudo"))
    detector = (ROOT/"deploy/systemd/backyard-detector.service").read_text()
    assert "After=backyard-api.service network-online.target" in detector
    assert "SupplementaryGroups=audio" in detector and "PrivateDevices=yes" not in detector


def test_monitor_sigterm_is_clean_and_restores_handlers(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BACKYARD_MONITOR_LOCK_FILE", str(tmp_path / "monitor.lock"))
    before_term, before_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    instance = Mock()
    instance.status.return_value = {"phase":"stopped"}
    instance.run.side_effect = lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    monkeypatch.setattr("detector.monitor.Monitor", Mock(return_value=instance))
    assert monitor_main([]) == 0
    instance.close.assert_called_once()
    assert signal.getsignal(signal.SIGTERM) == before_term and signal.getsignal(signal.SIGINT) == before_int
    assert "SIGTERM" in capsys.readouterr().out


def test_lock_failure_never_starts_hardware(monkeypatch, capsys):
    instance = Mock()
    monkeypatch.setattr("detector.monitor.Monitor", Mock(return_value=instance))
    monkeypatch.setattr("detector.monitor.exclusive_monitor", Mock(side_effect=RuntimeError("Another Backyard monitor")))
    assert monitor_main([]) == 1
    instance.run.assert_not_called()
    assert "Another Backyard monitor" in capsys.readouterr().out


def test_stalled_inference_triggers_controlled_failure():
    analyzer = Mock()
    monitor = Monitor(MonitorConfig(inference_timeout=5), analyzer=analyzer)
    monitor.capture = Mock(last_read=time.monotonic())
    monitor.metrics.set(inference_started_monotonic=time.monotonic()-6)
    monitor.check_progress()
    assert monitor.stop.is_set()
    assert monitor.status()["inference_timeouts"] == 1
    assert "Inference exceeded" in monitor.error


@pytest.mark.parametrize("value", [0, 301, float("nan")])
def test_invalid_inference_timeout(value):
    with pytest.raises(ValueError):
        MonitorConfig(inference_timeout=value)


def test_readiness_retries_transient_failure_then_succeeds():
    with patch("operations.wait_api.check_health", side_effect=[OSError("not up"), {"status":"ok"}]) as probe, \
         patch("operations.wait_api.time.sleep"):
        assert wait("http://127.0.0.1:8010", 2)
    assert probe.call_count == 2


def test_readiness_deadline_reports_failure(capsys):
    with patch("operations.wait_api.check_health", side_effect=OSError("offline")), \
         patch("operations.wait_api.time.monotonic", side_effect=[0, 0, 0, .5, 2]), \
         patch("operations.wait_api.time.sleep"):
        assert not wait("http://127.0.0.1:8010", 1)
    assert "offline" in capsys.readouterr().out


def test_readiness_checks_actual_policy_fingerprint():
    transport = Mock()
    transport.request.return_value = {"fingerprint": Policy().fingerprint}
    with patch("operations.wait_api.check_health"), patch("operations.wait_api.HTTPTransport", return_value=transport):
        assert wait("http://127.0.0.1:8010", 1, True)
    assert transport.request.call_args.args[1] == "/api/observations/policy"


def test_health_requires_database_and_service_identity():
    with patch("operations.health.HTTPTransport") as transport:
        transport.return_value.request.return_value = {"status":"ok"}
        with pytest.raises(ValueError, match="Unexpected"):
            check_health("http://127.0.0.1:8010")


def row(timestamp, invocation, samples=0, windows=0, uptime=0, **metrics):
    return {"__REALTIME_TIMESTAMP": str(round(timestamp*1_000_000)),
            "_SYSTEMD_UNIT": "backyard-detector.service", "_SYSTEMD_INVOCATION_ID":invocation,
            "MESSAGE":json.dumps({"event":"monitor_status", "phase":"running",
                                  "samples_captured":samples,"windows_processed":windows,
                                  "uptime_seconds":uptime, **metrics})}


def test_journal_sums_restarts_without_hiding_old_errors():
    summary = JournalSummary(100)
    summary.add(row(101,"one"))
    summary.add(row(131,"one", samples=1000, windows=10, uptime=30, capture_gaps=1))
    summary.add(row(150,"two"))
    summary.add(row(180,"two", samples=2000, windows=20, uptime=30))
    report = summary.report()
    assert report["detector_invocations_seen"] == 2
    assert report["observed_counter_deltas"]["windows_processed"] == 30
    assert report["observed_counter_deltas"]["capture_gaps"] == 1
    assert report["latest"]["invocation"] == "two"


def test_preexisting_session_uses_first_sample_as_lower_bound():
    summary = JournalSummary(100)
    summary.add(row(101,"old", windows=1000, uptime=1000))
    summary.add(row(131,"old", windows=1020, uptime=1030))
    assert summary.report()["observed_counter_deltas"]["windows_processed"] == 20
    assert not summary.report()["sessions"][0]["started_within_range"]


def test_journal_tracks_missing_samples_and_invalid_numbers():
    summary = JournalSummary(100)
    summary.add(row(101,"one"))
    summary.add(row(500,"one", windows=5, uptime=399))
    summary.add(row(501,"one", windows=float("nan")))
    assert summary.report()["max_sample_gap_seconds"] == 399
    assert summary.report()["invalid_records"] == 1
    assert summary.report()["observed_counter_deltas"]["windows_processed"] == 5


def test_final_status_prefix_included_without_counting_twice():
    summary = JournalSummary(100)
    summary.add(row(101,"one"))
    final = row(110,"one", windows=5, uptime=9, phase="stopped")
    final["MESSAGE"] = "Monitor gestopt: " + final["MESSAGE"]
    summary.add(final)
    assert summary.report()["observed_counter_deltas"]["windows_processed"] == 5
    assert summary.report()["latest"]["metrics"]["phase"] == "stopped"


def test_journal_reader_is_bounded_and_crosses_boots():
    process = Mock()
    process.stdout = io.BytesIO((json.dumps(row(101,"one"))+"\n").encode())
    process.stderr = io.BytesIO()
    process.wait.return_value = 0
    process.poll.return_value = 0
    with patch("operations.journal.subprocess.Popen", return_value=process) as spawn:
        result = read_journal(100,200)
    command = spawn.call_args.args[0]
    assert "--lines=20000" in command and "--since=@100.000000" in command
    assert "-b" not in command and result["status_samples"] == 1


def test_readonly_inventory_and_status_preserve_records_audio(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3",storage_root=tmp_path/"audio")
    with TestClient(create_app(settings)) as client:
        transport = APITransport(client)
        run("bird-auto", Policy(), transport)
        run("bird-review", Policy(), transport)
        run("chimpanzee", Policy(), transport)
        before_db = settings.resolved_database_path.read_bytes()
        before_audio = {str(p):p.read_bytes() for p in settings.resolved_storage_root.rglob("*.wav")}
        since = time.time()-86400
        db = database_inventory(settings.resolved_database_path,since,True)
        assert db["observations_total"] == db["supports_total"] == 2
        assert db["created_since_by_status"] == {"auto_accepted":1,"pending_review":1}
        assert db["quick_check"] == ["ok"] and db["audio_uploaded_since"] == 2
        audio = audio_inventory(settings.resolved_storage_root,since)
        assert audio["complete"] and audio["groups"]["permanent_bird"]["wav_files"] == 1
        assert audio["groups"]["review_bird"]["wav_files"] == 1
        assert settings.resolved_database_path.read_bytes() == before_db
        assert {str(p):p.read_bytes() for p in settings.resolved_storage_root.rglob("*.wav")} == before_audio


def test_missing_database_is_never_created(tmp_path):
    path=tmp_path/"does-not-exist.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        database_inventory(path,0)
    assert not path.exists()


def test_audio_inventory_reports_partial_counts_at_limit(tmp_path):
    folder=tmp_path/"birds/audio"
    folder.mkdir(parents=True)
    for number in range(3):
        (folder/f"{number}.wav").write_bytes(b"test")
    assert not audio_inventory(tmp_path,0,max_entries=1)["complete"]


def healthy_report():
    summary = JournalSummary(100)
    summary.add(row(101,"active"))
    summary.add(row(130,"active",windows=10,uptime=29))
    return {"timestamp":130, "services": {
        name:{"ActiveState":"active","SubState":"running","UnitFileState":"enabled","User":"cpvb86","InvocationID":"active"}
        for name in ("backyard-api.service","backyard-detector.service")},
        "api_health":{"status":"ok","database":"ok","service":"backyard"},
        "journal":summary.report(),"database":{"quick_check":["ok"]},
        "audio":{"complete":True},"system":{}}


def test_status_healthy_and_stale_current_process_are_distinct():
    report=healthy_report()
    assert warnings(report)==[]
    report["services"]["backyard-detector.service"]["InvocationID"]="new-startup"
    report["timestamp"]=1000
    notes=warnings(report)
    assert any("stale" in n for n in notes)
    assert any("different service invocation" in n for n in notes)


def test_unavailable_journal_cannot_appear_healthy():
    report=healthy_report()
    report["journal"]={"error":"permission denied"}
    assert any("cannot assess" in n for n in warnings(report))


def test_cli_shared_environment_overrides_dotenv_and_emits_json(tmp_path,capsys):
    path=tmp_path/"service.env"
    path.write_text("BACKYARD_DATABASE_PATH=/specific/backyard.sqlite3\n")
    report=healthy_report() | {"at":"now","requested_hours":24,"warnings":[],"health_summary":"CURRENTLY_OK"}
    with patch("operations.status.collect",return_value=report) as collector:
        assert status_main(["--environment-file",str(path),"--json"])==0
    assert str(collector.call_args.args[0].database_path).replace("\\","/") == "/specific/backyard.sqlite3"
    assert json.loads(capsys.readouterr().out)["health_summary"]=="CURRENTLY_OK"


@pytest.mark.skipif(os.name != "posix", reason="Requires real Linux/POSIX flock")
def test_real_lock_excludes_second_process_and_recovers_after_sigkill(tmp_path):
    from detector.instance import exclusive_monitor
    lock=tmp_path/"monitor.lock"
    code="from detector.instance import exclusive_monitor; import time;\nwith exclusive_monitor("+repr(str(lock))+"):\n print('locked',flush=True)\n time.sleep(30)\n"
    process=subprocess.Popen([sys.executable,"-c",code],stdout=subprocess.PIPE,text=True,cwd=ROOT)
    try:
        assert process.stdout.readline().strip()=="locked"
        with pytest.raises(RuntimeError,match="Another Backyard monitor"):
            with exclusive_monitor(lock): pass
        process.kill(); process.wait(timeout=5)
        with exclusive_monitor(lock):
            assert lock.read_text().strip()==str(os.getpid())
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        process.stdout.close()


@pytest.mark.skipif(os.name != "posix", reason="Requires actual POSIX SIGTERM subprocess")
def test_real_sigterm_runs_monitor_cleanup(tmp_path):
    code="""import signal
import detector.monitor as module
class Fake:
    def __init__(self, config): pass
    def run(self):
        print('READY',flush=True)
        signal.pause()
    def close(self): print('CLOSED',flush=True)
    def status(self): return {'phase':'stopped'}
module.Monitor=Fake
raise SystemExit(module.main(['--lock-file',""" + repr(str(tmp_path/"monitor.lock")) + """]))
"""
    process=subprocess.Popen([sys.executable,"-c",code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=ROOT)
    try:
        assert process.stdout.readline().strip()=="READY"
        process.send_signal(signal.SIGTERM)
        out,err=process.communicate(timeout=10)
        assert process.returncode==0 and "CLOSED" in out and "Traceback" not in err
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        process.stdout.close(); process.stderr.close()


def test_short_history_is_explicitly_incomplete():
    report=healthy_report()
    report["journal"]["history_starts_late_by_seconds"]=3600
    assert any("full-duration" in n for n in warnings(report))


def test_wait_policy_mismatch_does_not_start_detector(capsys):
    transport=Mock()
    transport.request.return_value={"fingerprint":"wrong"}
    with patch("operations.wait_api.check_health"), patch("operations.wait_api.HTTPTransport",return_value=transport), \
         patch("operations.wait_api.time.monotonic",side_effect=[0,0,0,.5,2]), patch("operations.wait_api.time.sleep"):
        assert not wait("http://127.0.0.1:8010",1,True)
    assert "policy mismatch" in capsys.readouterr().out
