"""Page status must follow startup, business progress and delivery confirmation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from visual_link.config import load
from visual_link.web import Transfer, read_json


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transfer = Transfer(self.root, 'pi')
        self.transfer.directory = self.root / 'run'
        self.transfer.directory.mkdir()
        self.transfer.process = Mock(returncode=0)
        self.transfer.process.poll.return_value = None
        self.transfer.log = self.root / 'web.log'
        self.transfer.log.write_text('')

    def write(self, name, value):
        path = self.transfer.directory / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value))

    def test_startup_and_business_status(self):
        startup = dict(state='mode_calibration', round=2,
                       tx_mode=dict(width=1920, height=1200),
                       rx_mode=dict(width=1920, height=1080),
                       local_status=[1, 0])
        self.write('startup/progress.json', startup)
        value = self.transfer.status()
        self.assertEqual(value['tx_mode']['height'], 1200)
        self.assertEqual(value['rx_mode']['height'], 1080)
        self.assertEqual(value['status'], 'running')
        self.write('progress.json', dict(phase='ready', connection_ready=True,
                                        modes=dict(tx_mode=startup['tx_mode'], rx_mode=startup['rx_mode']),
                                        send_count=3, transport=dict(acknowledged=2)))
        value = self.transfer.status()
        self.assertTrue(value['connection_ready'])
        self.assertEqual(value['transport']['acknowledged'], 2)
        self.assertEqual(value['startup'], startup)

    def test_exit_requires_both_delivery_receipts(self):
        self.transfer.sending = True
        self.transfer.process.poll.return_value = 0
        result = dict(status='complete', receive_complete=True,
                      transport=dict(sent_confirmed=True, receive_finish_confirmed=False))
        self.write('result.json', result)
        self.assertEqual(self.transfer.status()['status'], 'failed')
        result['transport']['receive_finish_confirmed'] = True
        self.write('result.json', result)
        self.assertEqual(self.transfer.status()['status'], 'success')

    def test_failed_spawn_and_partial_report(self):
        self.transfer.directory = None
        self.transfer.process = None
        with patch('visual_link.web.subprocess.Popen', side_effect=OSError('unavailable')):
            self.assertEqual(self.transfer.start(dict(output='new'))['status'], 'failed')
        path = self.root / 'partial.json'
        path.write_text('{')
        self.assertEqual(read_json(path), {})

    def test_invalid_requests_do_not_start(self):
        self.transfer.process = None
        for values in (dict(seconds=0), dict(seconds=float('nan')), dict(input='missing'), dict(output='run')):
            with self.assertRaises(ValueError):
                self.transfer.start(values)

    def test_recommended_configs_only_store_device_and_candidates(self):
        root = Path(__file__).resolve().parents[1]
        for side in ('pi', 'windows'):
            path = root / f'configs/{side}-recommended.json'
            raw = json.loads(path.read_text())
            self.assertEqual(set(raw), {'backend', 'device', 'monitor', 'candidate_modes'})
            config = load(path)
            self.assertEqual(config['width'], 1920)
            self.assertNotIn('send_fps_limit', config)
            self.assertNotIn('tx_mode', config)
