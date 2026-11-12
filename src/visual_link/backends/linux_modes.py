"""Query and temporarily apply modes through the active desktop's output manager."""
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager


def _run(arguments):
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=10).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(f'{arguments[0]} failed: {exc.stderr.strip()}') from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'{arguments[0]} exceeded display query/apply timeout') from exc


def parse_xrandr(text):
    rows, output = [], None
    for line in text.splitlines():
        match = re.match(r'^(\S+) connected(?: primary)?(?: (\d+)x(\d+)([+-]\d+)([+-]\d+))?', line)
        if match:
            output = dict(device=match[1], modes=[], scale=1, current_output=None)
            if match[2]:
                output['position'] = dict(x=int(match[4]), y=int(match[5]))
            rows.append(output)
        elif line and not line[0].isspace():
            output = None
        elif output is not None:
            match = re.match(r'^\s+(\S+)\s+(.+)', line)
            if not match:
                continue
            size = re.match(r'^(\d+)x(\d+)', match[1])
            if not size:
                continue
            for rate in match[2].split():
                number = re.match(r'^(\d+(?:\.\d+)?)', rate)
                if number:
                    mode = dict(width=int(size[1]), height=int(size[2]), refresh_hz=float(number[1]),
                                name=match[1], current='*' in rate)
                    output['modes'].append(mode)
                    if mode['current']:
                        output['current_output'] = mode
    return rows


def outputs():
    if os.environ.get('WAYLAND_DISPLAY'):
        rows = json.loads(_run(['wlr-randr', '--json']))
        result = []
        for row in rows:
            if not row['enabled']:
                continue
            modes = [dict(width=m['width'], height=m['height'], refresh_hz=m['refresh'],
                          current=m['current']) for m in row['modes']]
            result.append(dict(device=row['name'], modes=modes,
                               current_output=next((m for m in modes if m['current']), None),
                               position=row['position'], scale=row['scale'], transform=row['transform']))
        return 'wlr-randr', result
    rows = parse_xrandr(_run(['xrandr', '--query']))
    verbose = _run(['xrandr', '--verbose'])
    for row in rows:
        block = re.search(r'^' + re.escape(row['device']) + r' connected.*?(?=^\S|\Z)',
                          verbose, re.MULTILINE | re.DOTALL)
        matrix = re.search(r'Transform:\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+'
                           r'([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+'
                           r'([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)', block[0] if block else '')
        row['transform'] = ('normal' if matrix and list(map(float, matrix.groups())) ==
                            [1.,0.,0.,0.,1.,0.,0.,0.,1.] else 'unverified')
    return 'xrandr', rows


def capabilities(config):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk
    if not Gtk.init_check()[0]:
        raise ValueError('mode discovery requires the real graphical desktop')
    display = Gdk.Display.get_default()
    if config['monitor'] >= display.get_n_monitors():
        raise ValueError('monitor index out of range')
    monitor = display.get_monitor(config['monitor'])
    rect = monitor.get_geometry()
    api, rows = outputs()
    selected = [r for r in rows if r.get('position') == dict(x=rect.x, y=rect.y)]
    if len(selected) != 1:
        raise ValueError('cannot uniquely map selected GTK monitor to desktop output')
    row = selected[0]
    return dict(api=api, device=row['device'], monitor=config['monitor'], declared=row['modes'],
                current_output=dict(row['current_output'] or {}, scale=max(row['scale'], monitor.get_scale_factor()),
                                    transform=row.get('transform', 'normal')), outputs=rows)


def _set(api, device, mode):
    if api == 'wlr-randr':
        arguments = [api, '--output', device, '--mode',
                     f"{mode['width']}x{mode['height']}@{mode['refresh_hz']:.6f}Hz"]
    else:
        arguments = [api, '--output', device, '--mode', mode['name'], '--rate', str(mode['refresh_hz'])]
    _run(arguments)


def _wait_geometry(config, width, height):
    from gi.repository import Gtk, Gdk
    deadline = time.monotonic() + config.get('timeout', 10)
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        display = Gdk.Display.get_default()
        display.sync()
        monitor = display.get_monitor(config['monitor'])
        rect = monitor.get_geometry()
        if (rect.width, rect.height, monitor.get_scale_factor()) == (width, height, 1):
            return
        time.sleep(0.02)
    raise ValueError('GTK monitor geometry did not follow desktop mode change')


@contextmanager
def apply(config, width, height, refresh_hz=None):
    before = capabilities(config)
    matches = [m for m in before['declared'] if (m['width'], m['height']) == (width, height)
               and (refresh_hz is None or abs(m['refresh_hz'] - refresh_hz) < 0.01)]
    if not matches:
        raise ValueError('selected output does not declare requested mode')
    current = before['current_output']
    if current.get('scale') != 1 or current.get('transform') != 'normal':
        raise ValueError('selected output requires scale 1 and normal transform')
    chosen = max(matches, key=lambda m: m['refresh_hz'])
    changed = any(current.get(k) != chosen[k] for k in ('width', 'height', 'refresh_hz'))
    try:
        if changed:
            _set(before['api'], before['device'], chosen)
            _wait_geometry(config, width, height)
        _, rows = outputs()
        active = next(r for r in rows if r['device'] == before['device'])
        mode = active['current_output']
        if ((mode['width'], mode['height']) != (width, height)
                or abs(mode['refresh_hz'] - chosen['refresh_hz']) > 0.01
                or active['scale'] != 1 or active.get('transform', 'normal') != 'normal'):
            raise ValueError('desktop did not apply requested mode')
        yield dict(api=before['api'], device=before['device'], before=current,
                   current_output=dict(mode, scale=active['scale']), changed=changed)
    finally:
        if changed:
            _set(before['api'], before['device'], current)
            _wait_geometry(config, current['width'], current['height'])
            _, rows = outputs()
            restored = next(r['current_output'] for r in rows if r['device'] == before['device'])
            if any(restored[k] != current[k] for k in ('width', 'height', 'refresh_hz')):
                raise ValueError('failed to restore original Linux display mode')
