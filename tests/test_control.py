import unittest
from dataclasses import asdict

import cv2
import numpy as np

from visual_link.core.control import Message, encode, decode
from visual_link.session import Capabilities, Session, State


class ControlTests(unittest.TestCase):
    def test_image_discovery_without_data_roi(self):
        message = Message.from_bytes(Message('a'*32, 'b'*32, 2, 2, 'CAPS', asdict(Capabilities())).to_bytes())
        for width, height in ((1920, 1080), (1280, 720), (800, 600)):
            image = encode(message, width, height)
            for rotation in range(4):
                for flipped in (False, True):
                    view = np.rot90(image, rotation)
                    if flipped:
                        view = view[::-1]
                    view = cv2.copyMakeBorder(view, 11, 19, 23, 7, cv2.BORDER_CONSTANT, value=255)
                    self.assertEqual(decode(view), message)

    def test_rs_damage_and_invalid_images(self):
        message = Message('a'*32, None, 1, 0, 'HELLO', {})
        image = encode(message)
        # Damage several adjacent binary cells while preserving the border.
        image[100:115, 150:210] ^= 255
        self.assertEqual(decode(image), message)
        for image in (np.zeros((1080, 1920), np.uint8), np.full((720, 1280), 255, np.uint8)):
            with self.assertRaises(ValueError):
                decode(image)

    def test_input_validation(self):
        for payload in (b'{}', b'[1,2,3,4,5,6]', b'null'):
            with self.assertRaises(ValueError):
                Message.from_bytes(payload)
        with self.assertRaises(ValueError):
            encode(Message('a'*32, None, 1, 0, 'HELLO', {'x': 'x'*500}))
        with self.assertRaises(ValueError):
            Capabilities.parse({'versions': [1]})

    def exchange(self, a, b, ticks=6, optical=False):
        for tick in range(ticks):
            for source, target in ((a, b), (b, a)):
                for message in source.outgoing():
                    # Deterministically lose the first CAPS, duplicate the others.
                    if tick == 0 and message.kind == 'CAPS':
                        continue
                    received = decode(encode(message)) if optical else message
                    target.receive(received)
                    target.receive(received)

    def test_loss_duplicates_and_restart(self):
        a, b = Session(local_id='a'*32), Session(local_id='b'*32)
        self.exchange(a, b, optical=True)
        self.assertEqual((a.state, b.state), (State.CAPABILITIES,)*2)
        self.assertEqual(a.session_id, b.session_id)
        self.assertTrue(a.coordinator)
        self.assertFalse(b.coordinator)
        old_messages = b.outgoing()
        b = Session(local_id='c'*32)
        self.exchange(a, b)
        for message in old_messages:
            a.receive(message)
        self.assertEqual(a.peer_id, b.local_id)
        self.assertEqual(a.session_id, b.session_id)
        self.assertEqual((a.state, b.state), (State.CAPABILITIES,)*2)

    def test_late_start_incompatible_and_wrong_target(self):
        a, b = Session(), Session(Capabilities(profiles=('future',)))
        for _ in range(5):
            a.outgoing()
        self.exchange(a, b)
        self.assertEqual((a.state, b.state), (State.INCOMPATIBLE,)*2)
        before = a.peer_id
        a.receive(Message('f'*32, 'e'*32, 1, 0, 'HELLO', {}))
        self.assertEqual(a.peer_id, before)


def bootstrap_peer(config, directory, messages, stop, heartbeat):
    import time
    from pathlib import Path
    from visual_link.hardware import save_json
    messages.put(Message('b'*32, None, 1, 0, 'HELLO', {}))
    messages.put(Message('b'*32, 'a'*32, 2, 2, 'CAPS', asdict(Capabilities())))
    while not stop.wait(0.01):
        heartbeat.value = time.monotonic()
    messages.cancel_join_thread()
    save_json(Path(directory)/'device.json', {'status': 'stopped'})


def bootstrap_stalled(config, directory, messages, stop, heartbeat):
    import time
    time.sleep(60)


class BootstrapLifecycleTests(unittest.TestCase):
    def test_spawn_exchange_and_cleanup(self):
        import json
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch
        from visual_link.bootstrap import exchange

        def display(config, image, seconds, ready, advance):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                advance()
                time.sleep(0.01)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch('visual_link.bootstrap.Session', return_value=Session(local_id='a'*32)):
                result = exchange({'timeout': 5}, directory, 1, display=display, receive_target=bootstrap_peer)
            self.assertEqual(result['state'], 'capabilities')
            self.assertEqual(json.loads((directory/'device.json').read_text())['status'], 'stopped')
            self.assertTrue((directory/'control.jsonl').read_text())

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'stalled'):
                exchange({'timeout': 0.2}, directory, 2, display=display, receive_target=bootstrap_stalled)
            self.assertEqual(json.loads((directory/'result.json').read_text())['status'], 'failed')


class WindowsBootstrapJobTests(unittest.TestCase):
    def test_job_preserves_mode_and_optional_input(self):
        import importlib.util
        import json
        import tempfile
        from contextlib import nullcontext
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        script = Path(__file__).resolve().parents[1]/'scripts/windows_display_job.py'
        spec = importlib.util.spec_from_file_location('windows_display_job_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for mode in ('display', 'display-stream', 'bootstrap', 'connect'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                job = dict(config='config.json', mode=mode, input=None if mode in ('bootstrap','connect') else 'image.png', output='out', seconds=1,
                           log=str(root/'job.log'), result=str(root/'result.json'))
                path = root/'job.json'
                path.write_text(json.dumps(job))
                with patch.object(module.sys, 'argv', ['job', str(path)]), \
                     patch.object(module, 'load', return_value={'timeout': 2}), \
                     patch.object(module, 'monitors', return_value=[]), \
                     patch.object(module, 'signals', return_value=[]), \
                     patch.object(module, 'desktop_mode', return_value=nullcontext({'device': 'screen'})), \
                     patch.object(module, 'identity_signal', return_value=nullcontext({})), \
                     patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                    self.assertEqual(module.main(), 0)
                    command = run.call_args.args[0]
                    self.assertEqual(command[3], mode)
                    self.assertEqual('--input' in command, mode not in ('bootstrap', 'connect'))
                self.assertTrue(json.loads((root/'result.json').read_text())['original_mode_restored'])

    def test_refused_signal_preserves_observation(self):
        import importlib.util
        import json
        import tempfile
        from contextlib import nullcontext
        from pathlib import Path
        from unittest.mock import patch

        script = Path(__file__).resolve().parents[1]/'scripts/windows_display_job.py'
        spec = importlib.util.spec_from_file_location('windows_refused_job_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = dict(device='screen', source=[1920, 1080], active=[1920, 1200], scaling=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = dict(config='config.json', mode='connect', output='out', seconds=1,
                       log=str(root/'job.log'), result=str(root/'result.json'))
            path = root/'job.json'
            path.write_text(json.dumps(job))
            with patch.object(module.sys, 'argv', ['job', str(path)]), \
                 patch.object(module, 'load', return_value={'timeout': 2, 'temporary_1080p': False}), \
                 patch.object(module, 'monitors', return_value=[{'width': 1920, 'height': 1080}]), \
                 patch.object(module, 'signals', return_value=[observed]), \
                 patch.object(module, 'desktop_mode', return_value=nullcontext({'device': 'screen'})), \
                 patch.object(module, 'identity_signal', side_effect=ValueError('signal mismatch')) as guard, \
                 patch.object(module.subprocess, 'run') as run:
                self.assertEqual(module.main(), 2)
                guard.assert_called_once_with('screen', False)
                run.assert_not_called()
            report = json.loads((root/'result.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['observed_display']['signals'], [observed])
            self.assertNotIn('original_mode_restored', report)


if __name__ == '__main__':
    unittest.main()
