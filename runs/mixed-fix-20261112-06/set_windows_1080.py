"""Set the selected Windows desktop to the user's requested final 1080p mode."""
import ctypes
from visual_link.backends.windows import _api, display_capabilities
from visual_link.config import load
from visual_link.hardware import save_json
config = load('configs/windows-recommended.json')
before = display_capabilities(config)
ct, _, api, _, mode_type = _api()
mode = mode_type(); mode.dmSize = ct.sizeof(mode)
assert api.EnumDisplaySettingsW(before['device'], 0xffffffff, ct.byref(mode))
mode.dmPelsWidth, mode.dmPelsHeight, mode.dmDisplayFrequency = 1920,1080,60
mode.dmFields = 0x80000 | 0x100000 | 0x400000
assert api.ChangeDisplaySettingsExW(before['device'], ct.byref(mode), None, 2, None) == 0
assert api.ChangeDisplaySettingsExW(before['device'], ct.byref(mode), None, 0, None) == 0
save_json('runs/mixed-fix-20261112-06/windows-set-1080.json', display_capabilities(config))
