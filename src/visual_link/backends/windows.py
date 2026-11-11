"""Windows DirectShow capture and pixel-exact single-image desktop display."""
from contextlib import contextmanager
import sys
import time

import cv2
import numpy as np

from .camera import capture_one


def _api():
    if sys.platform != 'win32':
        raise ValueError('Windows display requires a native Windows interactive desktop')
    import ctypes as ct
    from ctypes import wintypes as wt

    class MonitorInfo(ct.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT),
                    ('dwFlags', wt.DWORD), ('szDevice', wt.WCHAR*32)]

    class DevMode(ct.Structure):
        _fields_ = [('dmDeviceName', wt.WCHAR*32), ('dmSpecVersion', wt.WORD),
                    ('dmDriverVersion', wt.WORD), ('dmSize', wt.WORD), ('dmDriverExtra', wt.WORD),
                    ('dmFields', wt.DWORD), ('dmPosition', wt.POINT),
                    ('dmDisplayOrientation', wt.DWORD), ('dmDisplayFixedOutput', wt.DWORD),
                    ('dmColor', wt.SHORT), ('dmDuplex', wt.SHORT), ('dmYResolution', wt.SHORT),
                    ('dmTTOption', wt.SHORT), ('dmCollate', wt.SHORT), ('dmFormName', wt.WCHAR*32),
                    ('dmLogPixels', wt.WORD), ('dmBitsPerPel', wt.DWORD), ('dmPelsWidth', wt.DWORD),
                    ('dmPelsHeight', wt.DWORD), ('dmDisplayFlags', wt.DWORD),
                    ('dmDisplayFrequency', wt.DWORD), ('dmICMMethod', wt.DWORD),
                    ('dmICMIntent', wt.DWORD), ('dmMediaType', wt.DWORD), ('dmDitherType', wt.DWORD),
                    ('dmReserved1', wt.DWORD), ('dmReserved2', wt.DWORD),
                    ('dmPanningWidth', wt.DWORD), ('dmPanningHeight', wt.DWORD)]

    api = ct.WinDLL('user32', use_last_error=True)
    api.SetThreadDpiAwarenessContext.argtypes = [wt.HANDLE]
    api.SetThreadDpiAwarenessContext.restype = wt.HANDLE
    if not api.SetThreadDpiAwarenessContext(ct.c_void_p(-4)):
        raise ct.WinError(ct.get_last_error())
    api.GetMonitorInfoW.argtypes = [wt.HANDLE, ct.POINTER(MonitorInfo)]
    api.GetMonitorInfoW.restype = wt.BOOL
    api.EnumDisplaySettingsW.argtypes = [wt.LPCWSTR, wt.DWORD, ct.POINTER(DevMode)]
    api.EnumDisplaySettingsW.restype = wt.BOOL
    api.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ct.POINTER(DevMode), wt.HWND, wt.DWORD, ct.c_void_p]
    api.ChangeDisplaySettingsExW.restype = wt.LONG
    return ct, wt, api, MonitorInfo, DevMode


def monitors():
    """Enumerate physical rectangles under per-monitor DPI awareness."""
    ct, wt, api, info_type, _ = _api()
    callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)
    result, errors = [], []

    @callback_type
    def visit(handle, dc, rect, data):
        info = info_type()
        info.cbSize = ct.sizeof(info)
        if not api.GetMonitorInfoW(handle, ct.byref(info)):
            errors.append(ct.get_last_error())
            return False
        r = info.rcMonitor
        result.append(dict(device=info.szDevice, x=r.left, y=r.top,
                           width=r.right-r.left, height=r.bottom-r.top, primary=bool(info.dwFlags & 1)))
        return True

    api.EnumDisplayMonitors(None, None, visit, 0)
    if errors or not result:
        raise ValueError(f'cannot enumerate Windows monitors: {errors}')
    return sorted(result, key=lambda row: (not row['primary'], row['x'], row['y'], row['device']))


@contextmanager
def desktop_mode(config):
    """Optionally switch the selected output to 1080p, restoring its original mode."""
    ct, _, api, _, mode_type = _api()
    rows = monitors()
    if config['monitor'] >= len(rows):
        raise ValueError('monitor index out of range')
    monitor = rows[config['monitor']]
    device = monitor['device']
    if device == 'WinDisc':
        raise ValueError('SSH desktop is isolated; launch display in the logged-in interactive session')
    original = mode_type()
    original.dmSize = ct.sizeof(original)
    if not api.EnumDisplaySettingsW(device, 0xffffffff, ct.byref(original)):
        raise ValueError('cannot read original display mode')
    changed = False
    try:
        if (monitor['width'], monitor['height']) != (1920, 1080):
            if not config.get('temporary_1080p', False):
                raise ValueError('monitor must be 1920x1080; temporary_1080p requires explicit configuration')
            requested = mode_type.from_buffer_copy(original)
            requested.dmPelsWidth, requested.dmPelsHeight = 1920, 1080
            requested.dmDisplayFrequency = 60
            requested.dmFields = 0x80000 | 0x100000 | 0x400000  # DM_PELSWIDTH | DM_PELSHEIGHT
            if api.ChangeDisplaySettingsExW(device, ct.byref(requested), None, 2, None) != 0:
                raise ValueError('driver rejected temporary 1920x1080 mode')
            changed = True
            if api.ChangeDisplaySettingsExW(device, ct.byref(requested), None, 4, None) != 0:
                raise ValueError('temporary 1920x1080 mode switch failed')
        current = next(row for row in monitors() if row['device'] == device)
        if (current['width'], current['height']) != (1920, 1080):
            raise ValueError('native 1920x1080 display mode not confirmed')
        yield dict(**current, mode_changed=changed, original_width=original.dmPelsWidth,
                   original_height=original.dmPelsHeight, original_refresh=original.dmDisplayFrequency)
    finally:
        if changed and api.ChangeDisplaySettingsExW(device, ct.byref(original), None, 4, None) != 0:
            raise ValueError('failed to restore original Windows display mode')


def display_one(config, image, seconds, ready=None, advance=None):
    """Hold a borderless native-size PNG on the selected interactive monitor."""
    if image.dtype != np.uint8 or image.shape not in ((1080, 1920), (1080, 1920, 3)):
        raise ValueError('display requires native 1920x1080 uint8 gray or BGR')
    if not np.isfinite(seconds) or seconds <= 0:
        raise ValueError('display duration must be positive and finite')
    from .windows_signal import identity_signal
    with desktop_mode(config) as monitor, identity_signal(monitor['device'], config.get('temporary_1080p', False)) as timing:
        import base64
        import tkinter as tk
        ok, png = cv2.imencode('.png', image)
        if not ok:
            raise ValueError('cannot encode display pixels')
        window = tk.Tk()
        window.withdraw()
        state = dict(start=None, error=None, cancelled=False)
        try:
            window.overrideredirect(True)
            window.attributes('-topmost', True)
            window.configure(cursor='none', background='black')
            window.tk.call('tk', 'scaling', 1.0)
            # SetWindowPos uses physical desktop coordinates, including negative origins.
            window.geometry('1920x1080')
            photo = tk.PhotoImage(data=base64.b64encode(png.tobytes()))
            label = tk.Label(window, image=photo, borderwidth=0, highlightthickness=0, padx=0, pady=0)
            label.pack(fill='both', expand=True)
            ct, wt, api, _, _ = _api()
            api.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT]
            api.SetWindowPos.restype = wt.BOOL
            api.GetParent.argtypes = [wt.HWND]
            api.GetParent.restype = wt.HWND
            window.update_idletasks()
            hwnd = api.GetParent(window.winfo_id()) or window.winfo_id()
            if not api.SetWindowPos(hwnd, ct.c_void_p(-1), monitor['x'], monitor['y'], 1920, 1080, 0x40):
                raise ct.WinError(ct.get_last_error())
            window.deiconify()
            window.lift()
            window.focus_force()
            deadline = time.monotonic() + config['timeout']

            def cancel(*_):
                state['cancelled'] = True
                window.quit()

            window.bind('<Escape>', cancel)
            window.protocol('WM_DELETE_WINDOW', cancel)

            def check():
                geometry = (window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height())
                exact = geometry == (monitor['x'], monitor['y'], 1920, 1080)
                if state['start'] is None:
                    if exact:
                        state['start'] = time.monotonic()
                        if ready is not None:
                            ready(dict(status='ready', **monitor, scale=1, monotonic_ns=time.monotonic_ns()))
                    elif time.monotonic() >= deadline:
                        state['error'] = 'native full-screen geometry not confirmed'
                        window.quit()
                        return
                elif not exact:
                    state['error'] = 'native full-screen geometry lost'
                    window.quit()
                    return
                elif time.monotonic() - state['start'] >= seconds:
                    window.quit()
                    return
                if state['start'] is not None and advance is not None:
                    try:
                        next_image = advance()
                        if next_image is not None:
                            if next_image.dtype != np.uint8 or next_image.shape not in ((1080, 1920), (1080, 1920, 3)):
                                raise ValueError('display requires native 1920x1080 pixels')
                            ok, encoded = cv2.imencode('.png', next_image)
                            if not ok:
                                raise ValueError('cannot encode display pixels')
                            replacement = tk.PhotoImage(data=base64.b64encode(encoded.tobytes()))
                            label.configure(image=replacement)
                            label.image = replacement
                    except Exception as exc:
                        state['error'] = str(exc)
                        window.quit()
                        return
                window.after(5 if advance else 50, check)

            window.after(100, check)
            window.mainloop()
            if state['error'] or state['cancelled'] or state['start'] is None:
                raise ValueError(state['error'] or 'display cancelled')
            report = dict(**monitor, **timing, scale=1, held_seconds=time.monotonic()-state['start'],
                          physical_output_verified=False)
        finally:
            window.destroy()
    report['original_mode_restored'] = True
    return report
