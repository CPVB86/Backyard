"""Read only non-sensitive bridge status; bat remains the default domain."""
import json
from pathlib import Path


def bridge_status(environment, now, *, domain="bat"):
    key, default = ('BACKYARD_BAT_STATE', 'data/bat-bridge.json') if domain == 'bat' else ('BACKYARD_GO_BIRD_STATE', 'data/bird-go-bridge.json')
    path = Path(environment.get(key, default))
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
                **({'geo_active': False, 'plausibility': 'unknown'} if domain == 'bat'
                   else {'geo': state.get('geo', {}), 'supports_per_event': 1}),
                'note': 'Bridge connectivity does not prove microphone capture or inference; check BirdNET-Go.'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'phase': 'unavailable', 'stale': True}
