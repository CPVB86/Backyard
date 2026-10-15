"""Explicit bird-source switch with Python fallback. Default is a dry-run."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from detector.bat_bridge import GoClient
from detector.instance import exclusive_monitor
from operations.environment import read_environment

PYTHON = 'backyard-detector.service'
CAPTURE = 'backyard-birdnet-go.service'
BRIDGE = 'backyard-birds.service'


def command(*args):
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=180).stdout.strip()


def python_fallback(run=command):
    # Stop importer first, then release ALSA, then start the original producer.
    run('systemctl', 'disable', '--now', BRIDGE)
    run('systemctl', 'disable', '--now', CAPTURE)
    running = run('docker', 'inspect', '--format', '{{.State.Running}}', 'backyard-birds-go')
    if running != 'false':
        raise RuntimeError('Go container is not confirmed stopped; Python not started to avoid microphone conflict')
    run('systemctl', 'enable', '--now', PYTHON)
    run('systemctl', 'is-active', '--quiet', PYTHON)


def switch_to_go(environment, run=command, wait_ready=None):
    # Refuse a container which can independently restart and bypass systemd conflicts.
    restart = run('docker', 'inspect', '--format', '{{.HostConfig.RestartPolicy.Name}}', 'backyard-birds-go')
    if restart != 'no':
        raise ValueError('Create backyard-birds-go with --restart=no; systemd must own capture')
    run('systemctl', 'cat', CAPTURE, BRIDGE)
    try:
        run('systemctl', 'disable', '--now', PYTHON)
        run('systemctl', 'start', CAPTURE)
        (wait_ready or wait_go_api)(environment)
        run('systemctl', 'start', BRIDGE)
        if wait_ready is None:
            wait_bridge(environment)
        run('systemctl', 'enable', CAPTURE, BRIDGE)
    except (Exception, KeyboardInterrupt):
        # Do not report a successful rollback if any rollback command fails.
        python_fallback(run)
        raise RuntimeError('Go activation failed; Python detector restored. Inspect Go/bridge journal.') from None


def wait_go_api(environment):
    client = GoClient(environment.get('BACKYARD_GO_BIRD_URL', 'http://127.0.0.1:8082'))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            client.get('/api/v2/detections?limit=1')
            return
        except (OSError, ValueError, RuntimeError):
            time.sleep(2)
    raise RuntimeError('Go API startup timeout')


def wait_bridge(environment):
    state = Path(environment.get('BACKYARD_GO_BIRD_STATE', 'data/bird-go-bridge.json'))
    started = time.time()
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            content = json.loads(state.read_text(encoding='utf-8'))
            if (content.get('last_poll_at', 0) >= started and content.get('phase') == 'running'
                    and content.get('geo', {}).get('active')):
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Bird bridge/geo readiness timeout')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', choices=('python', 'go'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--environment-file', type=Path, default=Path('/etc/backyard/backyard.env'))
    args = parser.parse_args(argv)
    if not args.apply:
        print('DRY RUN: ' + ('stop Go bird bridge/capture; enable/start original Python detector'
                           if args.source == 'python' else
                           'stop Python detector; start Go capture/bridge; restore Python on startup failure'))
        print('Bats, API, observations and audio are not modified. Use --apply to switch.')
        return
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise SystemExit('Run --apply on the Pi with sudo')
    environment = read_environment(args.environment_file)
    with exclusive_monitor('/run/lock/backyard-bird-source.lock'):
        if args.source == 'python':
            python_fallback()
        else:
            switch_to_go(environment)
    print('Bird source: ' + args.source + '. Verify microphone/inference and an actual observation in the logs.')


if __name__ == '__main__':
    main()
