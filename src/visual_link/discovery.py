"""Selected-device observations and short, explicitly scoped performance probes."""
import time

from .core.protocol import encode_frame, decode
from .core.control import decode as decode_control
from .calibration import receive_wire


def display_info(config):
    if config['backend'] == 'windows':
        from .backends.windows import monitors
        from .backends.windows_signal import signals
        rows = monitors()
        monitor = rows[config['monitor']]
        signal = next(s for s in signals() if s['device'] == monitor['device'])
        if signal['active'] != [1920,1080] or signal['scaling'] != 1:
            raise ValueError('selected display signal must be native 1080p identity')
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
    if (selected['width'],selected['height'],selected['scale']) != (1920,1080,1):
        raise ValueError('selected display must be native 1080p scale1')
    return dict(selected=selected,outputs=rows)


def codec_timings():
    """Time encoding and the full connect receive path on local BGR pixels."""
    started=time.monotonic()
    image, _ = encode_frame(b'visual-link performance probe',seed=0)
    encoded=time.monotonic()
    import numpy as np
    captured=np.repeat(image[:,:,None],3,axis=2)
    try:
        decode_control(captured)
    except ValueError:
        pass
    else:
        raise ValueError('local business probe decoded as control')
    frame=decode(receive_wire(captured,dict(orientation='normal',roi=[3,1080,2,1920],
                                          thresholds=[8.5+17*i for i in range(15)])))
    ended=time.monotonic()
    if frame.data != b'visual-link performance probe':
        raise ValueError('local codec probe failed')
    return [round((encoded-started)*1000,3),round((ended-encoded)*1000,3)]
