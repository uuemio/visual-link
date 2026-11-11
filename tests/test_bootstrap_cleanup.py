"""A disk-close failure must not strand the capture process or lose result.json."""
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from visual_link.bootstrap import exchange
from visual_link.session import CalibrationSession
from test_discovery import calibration_peer


class BootstrapCleanupTests(unittest.TestCase):
    def test_disk_close_failure_still_stops_capture_and_records_failure(self):
        before = {child.pid for child in mp.active_children()}
        def display(config, image, seconds, ready, advance):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                advance()
                time.sleep(0.01)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch('visual_link.discovery.display_info', return_value={}), \
                 patch('visual_link.bootstrap.CalibrationSession', return_value=CalibrationSession(local_id='a'*32)), \
                 patch('visual_link.core.storage.DiskReassembler.close', side_effect=OSError('injected disk close error')):
                with self.assertRaisesRegex(OSError, 'injected disk close error'):
                    exchange({'timeout': 5}, directory, 1, display=display,
                             receive_target=calibration_peer, calibrate=True)
            leaked = [child for child in mp.active_children() if child.pid not in before]
            try:
                self.assertFalse(leaked, 'capture process survived disk cleanup failure')
                result = json.loads((directory/'result.json').read_text())
                self.assertEqual(result['status'], 'failed')
                self.assertIn('injected disk close error', result['error'])
            finally:
                for child in leaked:
                    child.terminate()
                    child.join(3)
                    if child.is_alive(): child.kill(); child.join()


if __name__ == '__main__': unittest.main()
