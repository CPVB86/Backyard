"""Read only the non-sensitive operational fields of the bat checkpoint."""
import json
from pathlib import Path


def bridge_status(environment, now):
    path = Path(environment.get('BACKYARD_BAT_STATE', 'data/bat-bridge.json'))
    try:
        with path.open(encoding='utf-8') as stream:
            content = stream.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError('Oversized checkpoint')
        state = json.loads(content)
        return {'phase': state.get('phase', 'starting'), 'scan_date': state['date'],
                'last_poll_at': state.get('last_poll_at'),
                'stale': now - state.get('last_poll_at', 0) > 180,
                'completed_on_scan_date': len(state['done']),
                'pending_upload': bool(state.get('pending')),
                'last_error_type': state.get('last_error_type'),
                'geo_active': False, 'plausibility': 'unknown',
                'note': 'Bridge connectivity does not prove microphone capture or inference; check BirdNET-Go.'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'phase': 'unavailable', 'stale': True}
