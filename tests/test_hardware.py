"""Synthetic checks; these do not establish hardware performance."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from visual_link import calibration, hardware
from visual_link.backends import camera, windows, windows_signal
from visual_link.config import load
from visual_link.core.protocol import encode_frame, capacity, digest

ROOT = Path(__file__).resolve().parents[1]


class FakeCamera:
    def __init__(self, fourcc='BGR3', shape=(1080, 1920, 3), opened=True):
        self.fourcc, self.shape, self.opened = fourcc, shape, opened
        self.released = False
        self.reads = 0

    def open(self, device, api):
        return self.opened

    def set(self, prop, value):
        return True

    def getBackendName(self):
        return 'FAKE'

    def get(self, prop):
        return {cv2.CAP_PROP_FOURCC: (0xE436EB7D if self.fourcc == 'BGR3' else cv2.VideoWriter_fourcc(*self.fourcc)),
                cv2.CAP_PROP_FRAME_WIDTH: 1920, cv2.CAP_PROP_FRAME_HEIGHT: 1080}.get(prop, 0)

    def read(self):
        self.reads += 1
        return True, np.zeros(self.shape, dtype=np.uint8)

    def release(self):
        self.released = True


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.config = load(ROOT/'configs/windows.json')

    def test_capture_negotiation_and_release(self):
        for fourcc, shape, opened, succeeds in (
                ('BGR3', (1080, 1920, 3), True, True),
                ('MJPG', (1080, 1920, 3), True, False),
                ('BGR3', (720, 1280, 3), True, False),
                ('BGR3', (1080, 1920, 3), False, False)):
            device = FakeCamera(fourcc, shape, opened)
            metadata = {}
            with patch.object(camera.sys, 'platform', 'win32'), patch.object(camera.cv2, 'VideoCapture', return_value=device):
                if succeeds:
                    image = camera.capture_one(self.config, metadata)
                    self.assertEqual(image.shape, shape)
                    self.assertEqual(device.reads, 6)
                    self.assertFalse(metadata['orientation_applied'])
                else:
                    with self.assertRaises(ValueError):
                        camera.capture_one(self.config, metadata)
            self.assertTrue(device.released)

    def test_windows_signal_restores_on_failure(self):
        import ctypes as ct
        from types import SimpleNamespace
        from unittest.mock import Mock

        class Pair(ct.Structure):
            _fields_ = [('cx', ct.c_uint32), ('cy', ct.c_uint32)]

        class Ratio(ct.Structure):
            _fields_ = [('numerator', ct.c_uint32), ('denominator', ct.c_uint32)]

        class Source(ct.Structure):
            _fields_ = [('width', ct.c_uint32), ('height', ct.c_uint32)]

        class Signal(ct.Structure):
            _fields_ = [('activeSize', Pair), ('totalSize', Pair), ('pixelRate', ct.c_uint64),
                        ('hSyncFreq', Ratio), ('vSyncFreq', Ratio), ('videoStandard', ct.c_uint32),
                        ('scanLineOrdering', ct.c_uint32)]

        class Union(ct.Union):
            _fields_ = [('signal', Signal), ('source', Source)]

        class Mode(ct.Structure):
            _fields_ = [('value', Union)]

        class Info(ct.Structure):
            _fields_ = [('modeInfoIdx', ct.c_uint32), ('scaling', ct.c_uint32),
                        ('refreshRate', Ratio), ('scanLineOrdering', ct.c_uint32)]

        class PathInfo(ct.Structure):
            _fields_ = [('source', Info), ('target', Info)]

        paths, modes = (PathInfo*1)(), (Mode*2)()
        paths[0].source.modeInfoIdx = 0
        paths[0].target.modeInfoIdx = 1
        paths[0].target.scaling = 4
        modes[0].value.source.width, modes[0].value.source.height = 1920, 1080
        modes[1].value.signal.activeSize.cx, modes[1].value.signal.activeSize.cy = 1920, 1200
        api = SimpleNamespace(SetDisplayConfig=Mock(return_value=0))
        after = [dict(device='DISPLAY2', active=[1920, 1080], source=[1920, 1080], scaling=1)]
        with patch.object(windows_signal, '_configuration', return_value=(ct, api, paths, modes, ['DISPLAY2'])), patch.object(windows_signal, 'signals', side_effect=[[], after]):
            with self.assertRaisesRegex(ValueError, 'test failure'):
                with windows_signal.identity_signal('DISPLAY2', allow_change=True):
                    raise ValueError('test failure')
        calls = api.SetDisplayConfig.call_args_list
        self.assertEqual([call.args[4] for call in calls], [0xa0, 0x4a0])
        self.assertEqual(calls[-1].args[3][1].value.signal.activeSize.cy, 1200)
        self.assertEqual(calls[-1].args[1][0].target.scaling, 4)
        self.assertTrue(all(not (call.args[4] & 0x200) for call in calls))

    def test_windows_temporary_mode_restores_on_failure(self):
        import ctypes

        class Mode(ctypes.Structure):
            _fields_ = [('dmSize', ctypes.c_uint32), ('dmPelsWidth', ctypes.c_uint32),
                        ('dmPelsHeight', ctypes.c_uint32), ('dmDisplayFrequency', ctypes.c_uint32),
                        ('dmFields', ctypes.c_uint32)]

        class API:
            def __init__(self):
                self.changes = []

            def EnumDisplaySettingsW(self, device, index, mode):
                mode._obj.dmPelsWidth = 1920
                mode._obj.dmPelsHeight = 1200
                mode._obj.dmDisplayFrequency = 60
                return True

            def ChangeDisplaySettingsExW(self, device, mode, hwnd, flags, data):
                self.changes.append((mode._obj.dmPelsWidth, mode._obj.dmPelsHeight, flags))
                return 0

        api = API()
        original = dict(device='DISPLAY2', width=1920, height=1200, x=1920, y=0)
        current = {**original, 'height': 1080}
        with patch.object(windows, '_api', return_value=(ctypes, None, api, None, Mode)), patch.object(windows, 'monitors', side_effect=[[original], [current]]):
            with self.assertRaisesRegex(ValueError, 'test failure'):
                with windows.desktop_mode(dict(monitor=0, temporary_1080p=True)) as mode:
                    self.assertTrue(mode['mode_changed'])
                    raise ValueError('test failure')
        self.assertEqual(api.changes, [(1920, 1080, 2), (1920, 1080, 4), (1920, 1200, 4)])
        api.changes.clear()
        with patch.object(windows, '_api', return_value=(ctypes, None, api, None, Mode)), patch.object(windows, 'monitors', return_value=[original]):
            with self.assertRaisesRegex(ValueError, 'explicit configuration'):
                with windows.desktop_mode(dict(monitor=0)):
                    pass
        self.assertEqual(api.changes, [])

    def test_linux_native_format(self):
        device = FakeCamera()
        original_get = device.get
        device.get = lambda prop: cv2.VideoWriter_fourcc(*'BGR3') if prop == cv2.CAP_PROP_FOURCC else original_get(prop)
        with patch.object(camera.sys, 'platform', 'linux'), patch.object(camera.cv2, 'VideoCapture', return_value=device):
            metadata = {}
            camera.capture_one({**self.config, 'backend': 'linux'}, metadata)
            self.assertEqual(metadata['reported']['format'], 'BGR3')
        self.assertTrue(device.released)

    def test_multiframe_rejected_without_final_file(self):
        size = capacity(255, 247)
        source, _ = encode_frame(bytes(size), index=0, count=2, total=size+1, file_hash=digest(bytes(size+1)))
        captured = np.zeros_like(source)
        captured[:, :1918] = source[:, 2:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'missing frames'):
                hardware.receive_one(self.config, captured, path)
            report = json.loads((path/'rx.json').read_text())
            self.assertEqual(report['expected'], 2)
            self.assertEqual(report['missing'], 1)
            self.assertFalse((path/'recovered.bin').exists())

    def test_capture_worker_timeout(self):
        original_run = subprocess.run

        def blocking_driver(*args, **kwargs):
            return original_run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch.object(hardware.subprocess, 'run', side_effect=blocking_driver):
            directory = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'timed out'):
                hardware.capture({**self.config, 'timeout': 0.15}, directory)
            report = json.loads((directory/'device.json').read_text())
            self.assertEqual(report['status'], 'timeout')
            self.assertIsNone(report['missing'])
            self.assertFalse((directory/'recovered.bin').exists())

    def test_alignment_and_gray_diagnostics(self):
        source = calibration.pattern(19)
        shifted = np.zeros_like(source)
        shifted[:, :1918] = source[:, 2:]
        for orientation, image in (('normal', shifted), ('flip_vertical', shifted[::-1])):
            report = calibration.compare(source, image, orientation)
            self.assertEqual(report['symbol_errors'], 0)
            self.assertTrue(report['configured_alignment_matches'])
            self.assertEqual(report['best_alignment']['sample_accuracy'], 1)
        wrong = calibration.compare(source, source, 'normal')
        self.assertFalse(wrong['configured_alignment_matches'])
        self.assertEqual(wrong['best_alignment']['dx'], 0)
        with self.assertRaises(ValueError):
            calibration.compare(source, shifted[:100], 'normal')

    def test_single_frame_receive_without_source(self):
        source, _ = encode_frame(b'\x00\xffhardware\xe4\xb8\xad')
        captured = np.zeros_like(source)
        captured[:, :1918] = source[:, 2:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            report = hardware.receive_one({**self.config, 'orientation': 'flip_vertical'}, captured[::-1], path)
            self.assertEqual((path/'recovered.bin').read_bytes(), b'\x00\xffhardware\xe4\xb8\xad')
            self.assertEqual(report['status'], 'complete')
            with self.assertRaises(FileExistsError):
                hardware.receive_one(self.config, captured, path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with self.assertRaises(ValueError):
                hardware.receive_one(self.config, np.zeros_like(source), path)
            self.assertFalse((path/'recovered.bin').exists())
            report = json.loads((path/'rx.json').read_text())
            self.assertIsNone(report['expected'])
            self.assertIsNone(report['missing'])

    def test_config_rejects_unsafe_device_and_timeout(self):
        for field, value in (('device', -1), ('device', 'http://example.test'),
                             ('monitor', -1), ('monitor', True), ('temporary_1080p', 'yes'), ('timeout', float('nan')),
                             ('timeout', '15'), ('fps', float('inf'))):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'config.json'
                path.write_text(json.dumps({**self.config, field: value}))
                with self.assertRaises(ValueError):
                    load(path)

    def test_cli_rejects_rx_source_and_preserves_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'out'
            cmd = [sys.executable, '-m', 'visual_link', 'rx-one', '--config', str(ROOT/'configs/windows.json'),
                   '--input', 'source.bin', '--output', str(path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('do not accept', result.stderr)
            self.assertFalse(path.exists())
            cmd = [sys.executable, '-m', 'visual_link', 'pattern', '--config', str(ROOT/'configs/offline.json'),
                   '--output', str(path)]
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertTrue((path/'pattern.png').exists())


if __name__ == '__main__':
    unittest.main()
