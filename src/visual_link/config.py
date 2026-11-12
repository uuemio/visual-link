import json
import math
from pathlib import Path
from .core.image import image_parameters

BASE = dict(width=1920,height=1080,top=3,offset=2,levels=16,n=255,k=247,layout='column')
def load(path):
    c = json.loads(Path(path).read_text())
    if not isinstance(c, dict):
        raise ValueError('configuration must be a JSON object')
    if any(c.get(k) != v for k,v in BASE.items() if k not in ('width', 'height')):
        raise ValueError('unsupported wire configuration')
    image_parameters(c, 'tx')
    image_parameters(c, 'rx')
    if c.get('backend') not in ('image_sequence','windows','linux'):
        raise ValueError('unknown backend')
    for field in ('timeout', 'fps'):
        value = c.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{field} must be finite and positive')
    if c.get('orientation') not in ('normal','flip_vertical'):
        raise ValueError('invalid orientation')
    if c.get('pixel_format') != 'BGR3' or type(c.get('monitor')) is not int or c['monitor'] < 0 or type(c.get('device')) not in (int,str):
        raise ValueError('invalid platform configuration')
    if 'temporary_1080p' in c and type(c['temporary_1080p']) is not bool:
        raise ValueError('temporary_1080p must be a boolean')
    if type(c.get('receive_window', 1)) is not int or not 1 <= c.get('receive_window', 1) <= 64:
        raise ValueError('receive_window must be an integer from 1 to 64')
    for field, default in (('control_hold_seconds', 0.25), ('data_hold_seconds', 0.75)):
        value = c.get(field, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{field} must be finite and positive')
    if 'send_fps_limit' in c:
        value = c['send_fps_limit']
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('send_fps_limit must be finite and positive')
    if 'candidate_modes' in c:
        modes = c['candidate_modes']
        if not isinstance(modes, list) or not modes:
            raise ValueError('candidate_modes must be a nonempty list')
        from .core.image import ImageParameters
        for mode in modes:
            if not isinstance(mode, list) or len(mode) != 2:
                raise ValueError('invalid candidate mode')
            ImageParameters(*mode)
    if 'signal_lock_seconds' in c:
        value = c['signal_lock_seconds']
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('signal_lock_seconds must be positive')
    faults = c.get('test_faults', {})
    if (not isinstance(faults, dict) or set(faults)-{'drop_data_once','drop_control_once'}
            or not isinstance(faults.get('drop_data_once', []), list)
            or any(type(i) is not int or i < 0 for i in faults.get('drop_data_once', []))
            or not isinstance(faults.get('drop_control_once', []), list)
            or any(k not in ('ACK','FINISH','PROPOSE','ACCEPT','COMMIT','READY')
                   for k in faults.get('drop_control_once', []))):
        raise ValueError('invalid reproducible test faults')
    device = c['device']
    if (type(device) is int and device < 0) or (isinstance(device, str) and not device):
        raise ValueError('invalid device')
    if c['backend'] == 'linux' and isinstance(device, str) and not device.startswith('/dev/video'):
        raise ValueError('Linux capture requires a /dev/video device path or integer index')
    if c['backend'] == 'windows' and type(device) is not int:
        raise ValueError('Windows DirectShow requires an integer device index')
    return c
