"""Windows source/target timing inspection and reversible native HDMI configuration."""
import ctypes as c
from ctypes import wintypes as w


def _configuration():
    class Luid(c.Structure):
        _fields_ = [('low', w.DWORD), ('high', w.LONG)]
    class Rational(c.Structure):
        _fields_ = [('numerator', w.UINT), ('denominator', w.UINT)]
    class Region(c.Structure):
        _fields_ = [('cx', w.UINT), ('cy', w.UINT)]
    class Signal(c.Structure):
        _fields_ = [('pixelRate', c.c_uint64), ('hSyncFreq', Rational), ('vSyncFreq', Rational),
                    ('activeSize', Region), ('totalSize', Region), ('videoStandard', w.UINT), ('scanLineOrdering', w.UINT)]
    class SourceMode(c.Structure):
        _fields_ = [('width', w.UINT), ('height', w.UINT), ('pixelFormat', w.UINT), ('position', w.POINT)]
    class ModeUnion(c.Union):
        _fields_ = [('signal', Signal), ('source', SourceMode), ('raw', c.c_ubyte*48)]
    class Mode(c.Structure):
        _fields_ = [('infoType', w.UINT), ('id', w.UINT), ('adapterId', Luid), ('value', ModeUnion)]
    class SourceInfo(c.Structure):
        _fields_ = [('adapterId', Luid), ('id', w.UINT), ('modeInfoIdx', w.UINT), ('statusFlags', w.UINT)]
    class TargetInfo(c.Structure):
        _fields_ = [('adapterId', Luid), ('id', w.UINT), ('modeInfoIdx', w.UINT), ('outputTechnology', w.UINT),
                    ('rotation', w.UINT), ('scaling', w.UINT), ('refreshRate', Rational),
                    ('scanLineOrdering', w.UINT), ('targetAvailable', w.BOOL), ('statusFlags', w.UINT)]
    class Path(c.Structure):
        _fields_ = [('source', SourceInfo), ('target', TargetInfo), ('flags', w.UINT)]
    class Header(c.Structure):
        _fields_ = [('type', w.UINT), ('size', w.UINT), ('adapterId', Luid), ('id', w.UINT)]
    class SourceName(c.Structure):
        _fields_ = [('header', Header), ('name', w.WCHAR*32)]
    api = c.WinDLL('user32', use_last_error=True)
    paths_count, modes_count = w.UINT(), w.UINT()
    rc = api.GetDisplayConfigBufferSizes(2, c.byref(paths_count), c.byref(modes_count))
    if rc: raise OSError(rc, 'GetDisplayConfigBufferSizes')
    paths, modes = (Path*paths_count.value)(), (Mode*modes_count.value)()
    rc = api.QueryDisplayConfig(2, c.byref(paths_count), paths, c.byref(modes_count), modes, None)
    if rc: raise OSError(rc, 'QueryDisplayConfig')
    names = []
    for path in paths[:paths_count.value]:
        name = SourceName(); name.header.type = 1; name.header.size = c.sizeof(name)
        name.header.adapterId, name.header.id = path.source.adapterId, path.source.id
        rc = api.DisplayConfigGetDeviceInfo(c.byref(name))
        if rc: raise OSError(rc, 'DisplayConfigGetDeviceInfo')
        names.append(name.name)
    return c, api, paths, modes, names


def signals():
    _, _, paths, modes, names = _configuration()
    result = []
    for path, name in zip(paths, names):
        source = modes[path.source.modeInfoIdx].value.source
        signal = modes[path.target.modeInfoIdx].value.signal
        result.append(dict(device=name, source=[source.width, source.height],
                           active=[signal.activeSize.cx, signal.activeSize.cy],
                           total=[signal.totalSize.cx, signal.totalSize.cy], pixel_rate=signal.pixelRate,
                           refresh=[signal.vSyncFreq.numerator, signal.vSyncFreq.denominator],
                           scaling=path.target.scaling))
    return result


from contextlib import contextmanager


@contextmanager
def identity_signal(device, allow_change=False):
    """Enforce an actual 1080p HDMI timing, restoring the complete active configuration."""
    ct, api, paths, modes, names = _configuration()
    index = names.index(device)
    path = paths[index]
    signal = modes[path.target.modeInfoIdx].value.signal
    changed = (signal.activeSize.cx, signal.activeSize.cy) != (1920, 1080) or path.target.scaling != 1
    before = signals()
    if changed and not allow_change:
        raise ValueError('actual HDMI signal is scaled or not 1080p; temporary_1080p requires explicit configuration')
    saved_paths = type(paths).from_buffer_copy(paths)
    saved_modes = type(modes).from_buffer_copy(modes)
    api.SetDisplayConfig.argtypes = [w.UINT, ct.c_void_p, w.UINT, ct.c_void_p, w.UINT]
    api.SetDisplayConfig.restype = w.LONG
    try:
        if changed:
            source = modes[path.source.modeInfoIdx].value.source
            source.width, source.height = 1920, 1080
            signal.activeSize.cx, signal.activeSize.cy = 1920, 1080
            signal.totalSize.cx, signal.totalSize.cy = 2200, 1125
            signal.pixelRate = 148500000
            signal.hSyncFreq.numerator, signal.hSyncFreq.denominator = 67500, 1
            signal.vSyncFreq.numerator, signal.vSyncFreq.denominator = 60, 1
            signal.videoStandard, signal.scanLineOrdering = 255, 1
            path.target.scaling = 1
            path.target.refreshRate.numerator, path.target.refreshRate.denominator = 60, 1
            path.target.scanLineOrdering = 1
            rc = api.SetDisplayConfig(len(paths), paths, len(modes), modes, 0xa0)
            if rc:
                raise OSError(rc, 'cannot set an unscaled actual 1080p HDMI signal')
        after = signals()
        active = next(row for row in after if row['device'] == device)
        if active['active'] != [1920, 1080] or active['source'] != [1920, 1080] or active['scaling'] != 1:
            raise ValueError('actual HDMI signal remains scaled; refusing display')
        yield dict(signal_before=before, signal_active=active, signal_changed=changed)
    finally:
        if changed:
            rc = api.SetDisplayConfig(len(saved_paths), saved_paths, len(saved_modes), saved_modes, 0x4a0)
            if rc:
                raise OSError(rc, 'failed to restore original active HDMI configuration')
