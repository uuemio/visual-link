"""Device declarations stay distinct from observed pixels; scoped changes restore."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from visual_link.backends.v4l2 import parse_formats
from visual_link.backends.linux_modes import parse_xrandr, apply
from visual_link.discovery import refresh_capture, device_capabilities


class DeviceModeTests(unittest.TestCase):
    def test_windows_declarations_share_format_and_rate_fields(self):
        capture=dict(declared=[dict(format='MEDIASUBTYPE_RGB24',width=1920,height=1200,
                                   fps=60.,fps_min=25.,fps_max=60.)],
                     current_output=dict(width=1920,height=1080,fps=30.))
        with patch('visual_link.backends.windows.display_capabilities',return_value={}), \
             patch('visual_link.backends.directshow.capabilities',return_value=capture):
            result=device_capabilities(dict(backend='windows',device=0))
        row=result['capture']['declared'][0]
        self.assertEqual(row['format'],'BGR3')
        self.assertEqual(row['native_format'],'MEDIASUBTYPE_RGB24')
        self.assertEqual(row['fps'],[60.])
        self.assertEqual(row['fps_min'],25.)
        self.assertEqual(result['capture']['current_output']['height'],1080)

    def test_v4l2_format_specific_sizes_and_rates(self):
        rows = parse_formats("""[0]: 'YUYV' (YUYV 4:2:2)
 Size: Discrete 1920x1080
 Interval: Discrete 0.017s (60.000 fps)
[1]: 'BGR3' (24-bit BGR)
 Size: Discrete 1920x1200
 Interval: Discrete 0.033s (30.000 fps)
 Interval: Discrete 0.017s (60.000 fps)
 Size: Stepwise 32x32 - 4096x2160 with step 2/2
""")
        self.assertEqual(rows[0]['height'], 1080)
        self.assertEqual(rows[1], dict(format='BGR3', width=1920, height=1200, fps=[30., 60.]))
        self.assertNotIn('width', rows[2])

    def test_xrandr_selected_output_current_and_negative_position(self):
        rows = parse_xrandr("""HDMI-1 connected primary 1920x1200-1920+0 (normal)
   1920x1200 59.95*+
   1920x1080 60.00 50.00
DP-1 disconnected (normal)
   1920x1080 60.00
""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['position'], dict(x=-1920, y=0))
        self.assertEqual(rows[0]['current_output']['height'], 1200)
        self.assertEqual(len(rows[0]['modes']), 3)

    def test_linux_restores_when_body_fails(self):
        old=dict(width=1920,height=1080,refresh_hz=60.,scale=1,transform='normal')
        new=dict(width=1920,height=1200,refresh_hz=59.95)
        caps=dict(api='wlr-randr',device='HDMI-A-1',current_output=old,declared=[new])
        with patch('visual_link.backends.linux_modes.capabilities',return_value=caps), \
             patch('visual_link.backends.linux_modes._set') as setter, \
             patch('visual_link.backends.linux_modes._wait_geometry'), \
             patch('visual_link.backends.linux_modes.outputs', side_effect=[
                 ('wlr-randr',[dict(device='HDMI-A-1',current_output=new,scale=1)]),
                 ('wlr-randr',[dict(device='HDMI-A-1',current_output=old,scale=1)])]):
            with self.assertRaisesRegex(RuntimeError,'body failure'):
                with apply({},1920,1200):
                    raise RuntimeError('body failure')
            self.assertEqual(setter.call_count,2)
            self.assertEqual(setter.call_args.args[-1],old)

    def test_linux_rejects_undeclared_before_mutation(self):
        with patch('visual_link.backends.linux_modes.capabilities',return_value=dict(declared=[])), \
             patch('visual_link.backends.linux_modes._set') as setter:
            with self.assertRaisesRegex(ValueError,'does not declare'):
                with apply({},1920,1200):
                    self.fail('entered unsupported mode')
            setter.assert_not_called()

    def test_actual_capture_rebinds_rx_only_and_keeps_mismatch_evidence(self):
        config=dict(width=1920,height=1200,tx_mode=dict(width=1920,height=1200))
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            (directory/'device.json').write_text(json.dumps(dict(reported=dict(width=1920,height=1080))))
            with patch('visual_link.hardware.capture',return_value=np.zeros((1080,1920,3),np.uint8)):
                with self.assertRaisesRegex(ValueError,'differs from candidate'):
                    refresh_capture(config,directory)
                probe=json.loads((directory/'probe.json').read_text())
                self.assertFalse(probe['matches'])
                updated,_,_=refresh_capture(config,directory,require_match=False)
                self.assertEqual(updated['rx_mode']['height'],1080)
                self.assertEqual(updated['tx_mode']['height'],1200)
                self.assertNotIn('rx_mode',config)

    def test_unsupported_actual_geometry_preserves_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            (directory/'device.json').write_text('{}')
            with patch('visual_link.hardware.capture',return_value=np.zeros((720,1280,3),np.uint8)):
                with self.assertRaises(ValueError):
                    refresh_capture(dict(width=1920,height=1080),directory)
            self.assertEqual(json.loads((directory/'probe.json').read_text())['actual']['height'],720)


if __name__ == '__main__':
    unittest.main()
