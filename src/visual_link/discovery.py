"""Selected-device observations and short, explicitly scoped performance probes."""
from contextlib import contextmanager
import time

from .core.protocol import encode_frame, decode
from .core.control import decode as decode_control
from .calibration import receive_wire
from .core.image import DEFAULT_IMAGE, image_parameters


def display_info(config):
    parameters = image_parameters(config, 'tx')
    width, height = parameters.width, parameters.height
    if config['backend'] == 'windows':
        from .backends.windows import monitors
        from .backends.windows_signal import signals
        rows = monitors()
        monitor = rows[config['monitor']]
        signal = next(s for s in signals() if s['device'] == monitor['device'])
        if signal['active'] != [width,height] or signal['scaling'] != 1:
            raise ValueError('selected display signal must be native configured identity')
        return dict(selected=monitor, outputs=rows, signal=signal)
    if config['backend'] != 'linux':
        raise ValueError('device discovery requires a hardware backend')
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk, Gdk
    if not Gtk.init_check()[0]:
        raise ValueError('device discovery requires the real graphical desktop')
    display = Gdk.Display.get_default()
    rows=[]
    for i in range(display.get_n_monitors()):
        monitor=display.get_monitor(i); rect=monitor.get_geometry()
        rows.append(dict(index=i, width=rect.width, height=rect.height,
                         scale=monitor.get_scale_factor(), refresh_millihz=monitor.get_refresh_rate()))
    selected=rows[config['monitor']]
    if (selected['width'],selected['height'],selected['scale']) != (width,height,1):
        raise ValueError('selected display must be native configured scale1')
    return dict(selected=selected,outputs=rows)


def codec_timings(parameters=DEFAULT_IMAGE):
    """Time encoding and the full connect receive path on local BGR pixels."""
    started=time.monotonic()
    image, _ = encode_frame(b'visual-link performance probe',seed=0,parameters=parameters)
    encoded=time.monotonic()
    import numpy as np
    captured=np.repeat(image[:,:,None],3,axis=2)
    try:
        decode_control(captured)
    except ValueError:
        pass
    else:
        raise ValueError('local business probe decoded as control')
    frame=decode(receive_wire(captured,dict(orientation='normal',roi=list(parameters.tx_roi),
                                          thresholds=[8.5+17*i for i in range(15)]), parameters), parameters)
    ended=time.monotonic()
    if frame.data != b'visual-link performance probe':
        raise ValueError('local codec probe failed')
    return [round((encoded-started)*1000,3),round((ended-encoded)*1000,3)]


def device_capabilities(config):
    """Declared modes and current driver outputs, independently of configured geometry."""
    if config['backend'] == 'windows':
        from .backends.windows import display_capabilities
        from .backends.directshow import capabilities
        display = display_capabilities(config)
        capture = capabilities(config['device'])
    elif config['backend'] == 'linux':
        from .backends.linux_modes import capabilities as display_capabilities
        from .backends.v4l2 import capabilities
        display = display_capabilities(config)
        capture = capabilities(config['device'], config['timeout'])
    else:
        raise ValueError('device discovery requires a hardware backend')
    for row in capture['declared']:
        row['native_format'] = row['format']
        if row['format'] == 'MEDIASUBTYPE_RGB24':
            row['format'] = 'BGR3'
        rates = row.get('fps', [])
        row['fps'] = rates if isinstance(rates, list) else ([] if rates is None else [rates])
        if row['fps']:
            row.setdefault('fps_min', min(row['fps']))
            row.setdefault('fps_max', max(row['fps']))
    return dict(schema=1, display=display, capture=capture,
                observed_monotonic_ns=time.monotonic_ns())


@contextmanager
def apply_display_mode(config, refresh_hz=None):
    """Apply config's tx geometry for this scope and restore on every exit path."""
    import math
    if refresh_hz is not None and (type(refresh_hz) not in (int, float)
                                   or not math.isfinite(refresh_hz) or refresh_hz <= 0):
        raise ValueError('refresh_hz must be finite and positive')
    parameters = image_parameters(config, 'tx')
    if config['backend'] == 'windows':
        from .backends.windows import apply_mode as apply
    elif config['backend'] == 'linux':
        from .backends.linux_modes import apply
    else:
        raise ValueError('mode application requires a hardware backend')
    with apply(config, parameters.width, parameters.height, refresh_hz) as report:
        yield report


def refresh_capture(config, directory, require_match=True):
    """Reopen after signal change, preserve probe, return actual rx config and pixels.

    The previous capture generator/worker must be closed before calling. A negotiated
    candidate must require_match; diagnostic callers may inspect a different actual mode.
    """
    import json
    from .hardware import capture, save_json
    from .core.image import ImageParameters
    requested = image_parameters(config, 'rx')
    image = capture(config, directory, strict=False)
    metadata = json.loads((directory/'device.json').read_text(encoding='utf-8'))
    actual_mode = dict(width=image.shape[1], height=image.shape[0])
    probe = dict(capture=metadata, requested=dict(width=requested.width, height=requested.height),
                 actual=actual_mode, matches=image.shape[:2] == requested.shape)
    save_json(directory/'probe.json', probe)
    actual = ImageParameters(**actual_mode)
    updated = dict(config, rx_mode=actual_mode)
    if require_match and actual != requested:
        raise ValueError('actual capture differs from candidate; see probe.json')
    return updated, image, probe


def startup_capture_modes(observed):
    """Prefer reported HDMI timing; otherwise try supported USB output modes."""
    import re
    capture = observed['capture']
    modes = [(1920, 1080), (1920, 1200)]
    declared = {(r.get('width'), r.get('height')) for r in capture['declared']
                if r.get('format') == 'BGR3'}
    modes = [m for m in modes if m in declared]
    timing = capture.get('input_timing', {})
    if timing.get('returncode') == 0:
        text = timing.get('stdout', '')
        width = re.search(r'Active width:\s*(\d+)', text)
        height = re.search(r'Active height:\s*(\d+)', text)
        if width and height:
            native = (int(width[1]), int(height[1]))
            if native in modes:
                modes.remove(native)
                modes.insert(0, native)
    if not modes:
        raise ValueError('no supported bootstrap capture mode')
    return [dict(width=w, height=h) for w, h in modes]
