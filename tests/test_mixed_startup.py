import unittest
from pathlib import Path
import cv2
from visual_link.core.control import Message, encode, decode
from visual_link.discovery import startup_capture_modes


class MixedStartupTests(unittest.TestCase):
    def test_native_and_scaled_controls(self):
        message = Message('1'*32, None, 1, 0, 'HELLO', {})
        for height in (1080, 1200):
            image = encode(message, 1920, height)
            for received_height in (1080, 1200):
                with self.subTest(height=height, received_height=received_height):
                    scaled = cv2.resize(image, (1920, received_height))
                    self.assertEqual(decode(scaled), message)

    def test_real_pi_failure_frame(self):
        path = Path(__file__).with_name('fixtures') / 'pi-mixed-1200-to-1080.png'
        self.assertTrue(path.exists())
        self.assertIn(decode(cv2.imread(str(path))).kind, ('HELLO', 'CAPS'))

    def test_capture_modes_ignore_local_display_and_prefer_signal(self):
        observed = dict(display=dict(current_output=dict(width=1920, height=1200)),
                        capture=dict(declared=[dict(width=1920, height=h, format='BGR3')
                                               for h in (1080,1200)]))
        self.assertEqual([m['height'] for m in startup_capture_modes(observed)], [1080,1200])
        observed['capture']['input_timing'] = dict(returncode=0, stdout='Active width: 1920\nActive height: 1200')
        self.assertEqual([m['height'] for m in startup_capture_modes(observed)], [1200,1080])
        observed['capture']['declared'] = []
        with self.assertRaises(ValueError):
            startup_capture_modes(observed)

    def test_unreadable_initial_mode_reopens_and_locks_valid_control(self):
        import threading
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import numpy as np
        from visual_link.automatic import _receive
        message = Message('2'*32, None, 1, 0, 'HELLO', {})
        stop = threading.Event()
        seen = []
        class Messages:
            def put(self, value, timeout):
                self.value = value
                stop.set()
            def cancel_join_thread(self):
                pass
        messages = Messages()
        def frames(config, metadata, stopped, strict):
            seen.append(config['rx_mode']['height'])
            yield np.zeros((1080,1920,3), np.uint8) if len(seen) == 1 else encode(message)
        with TemporaryDirectory() as directory:
            with patch('visual_link.automatic.capture_frames', side_effect=frames), \
                 patch('visual_link.automatic.time.monotonic', side_effect=[0,5,6]), \
                 patch('visual_link.backends.image_sequence.write'):
                _receive(dict(rx_mode=dict(width=1920,height=1080),
                              _startup_rx_modes=[dict(width=1920,height=h) for h in (1080,1200)]),
                         directory, messages, stop)
        self.assertEqual(seen, [1080,1200])
        self.assertEqual(messages.value, message)
