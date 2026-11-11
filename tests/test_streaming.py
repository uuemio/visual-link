"""Synthetic pipeline checks; no device throughput claims."""
import io
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from visual_link.config import load
from visual_link.core.protocol import capacity, digest, encode_frame
from visual_link.hardware import save_json
from visual_link.presentation import Presentation
from visual_link.streaming import receive_stream, _capture

ROOT = Path(__file__).resolve().parents[1]


def synthetic_capture(config, directory, images, stop, done, counts, heartbeat):
    data = b'a'*(capacity()+1)
    # Order includes bad pixels, reversed indices and a consistent duplicate.
    for index in (None, 1, 1, 0):
        if index is None:
            captured = np.zeros((1080, 1920), np.uint8)
        else:
            source, _ = encode_frame(data[index*capacity():(index+1)*capacity()],
                                     index=index, count=2, total=len(data), file_hash=digest(data))
            captured = np.zeros_like(source)
            captured[:, :1918] = source[:, 2:]
        images.put((counts[0], time.monotonic_ns(), captured), timeout=5)
        counts[0] += 1
        counts[1] += 1
        heartbeat.value = time.monotonic()
    save_json(Path(directory)/'device.json', dict(status='stopped'))
    images.close()
    images.join_thread()
    done.set()


def stalled_capture(*args):
    time.sleep(30)


def stalled_decode(*args):
    time.sleep(30)


def empty_capture(config, directory, images, stop, done, counts, heartbeat):
    save_json(Path(directory)/'device.json', dict(status='stopped'))
    done.set()


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.config = load(ROOT/'configs/windows.json')

    def test_separate_pipeline_recovers_multiframe_and_logs_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            report = receive_stream(self.config, directory, 10, 2, capture_target=synthetic_capture)
            self.assertEqual((directory/'recovered.bin').read_bytes(), b'a'*(capacity()+1))
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['received'], 2)
            self.assertEqual(report['duplicates'], 1)
            self.assertEqual(report['rejected'], 1)
            self.assertEqual(report['missing'], 0)
            self.assertFalse(report['stability_pass'])
            self.assertTrue((directory/'first-rejected.png').exists())
            self.assertEqual(len((directory/'frames.jsonl').read_text().splitlines()), 4)

    def test_both_stages_have_killable_watchdogs(self):
        for producer, consumer, message in ((stalled_capture, None, 'capture stalled'),
                                             (empty_capture, stalled_decode, 'decode stalled')):
            with tempfile.TemporaryDirectory() as tmp:
                kwargs = dict(capture_target=producer)
                if consumer:
                    kwargs['decode_target'] = consumer
                started = time.monotonic()
                with self.assertRaisesRegex(ValueError, message):
                    receive_stream({**self.config, 'timeout': 1.5}, Path(tmp), 10, **kwargs)
                self.assertLess(time.monotonic()-started, 6)
                report = json.loads((Path(tmp)/'result.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertFalse((Path(tmp)/'recovered.bin').exists())

    def test_no_header_means_unknown_loss_and_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'missing frames'):
                receive_stream(self.config, Path(tmp), 10, capture_target=empty_capture)
            report = json.loads((Path(tmp)/'result.json').read_text())
            self.assertIsNone(report['expected'])
            self.assertIsNone(report['missing'])
            self.assertFalse((Path(tmp)/'recovered.bin').exists())

    def test_queue_overflow_is_bounded_and_counted(self):
        from types import SimpleNamespace
        import threading
        image = np.zeros((1, 1), np.uint8)
        bounded = queue.Queue(maxsize=1)
        # Provide the feeder lifecycle of a multiprocessing Queue.
        bounded.close = lambda: None
        bounded.join_thread = lambda: None
        counts = [0, 0, 0, 0]
        with tempfile.TemporaryDirectory() as tmp, patch('visual_link.streaming.capture_frames', return_value=iter([image+i for i in range(7)])):
            _capture(self.config, tmp, bounded, threading.Event(), threading.Event(), counts,
                     SimpleNamespace(value=0))
        self.assertEqual(counts, [7, 1, 6, 0])
        self.assertEqual(bounded.qsize(), 1)

    def test_only_exact_repeats_of_enqueued_pixels_are_filtered(self):
        from types import SimpleNamespace
        import threading
        original = np.zeros((2, 2), np.uint8)
        changed = original.copy()
        changed[0, 0] = 1
        bounded = queue.Queue(maxsize=3)
        bounded.close = lambda: None
        bounded.join_thread = lambda: None
        counts = [0, 0, 0, 0]
        with tempfile.TemporaryDirectory() as tmp, patch('visual_link.streaming.capture_frames', return_value=iter([original, original.copy(), changed, original])):
            _capture(self.config, tmp, bounded, threading.Event(), threading.Event(), counts,
                     SimpleNamespace(value=0))
        self.assertEqual(counts, [4, 3, 0, 1])
        self.assertEqual([bounded.get()[0] for _ in range(3)], [0, 2, 3])

    def test_presentation_preserves_order_and_never_skips_on_late_tick(self):
        paths = [Path('one.png'), Path('two.png')]
        with patch('visual_link.presentation.read_image', side_effect=lambda p: str(p)):
            sequence = Presentation(paths, 1, io.StringIO(), 1)
            try:
                self.assertEqual(sequence.first(2), 'one.png')
                with patch('visual_link.presentation.time.monotonic', return_value=10):
                    self.assertIsNone(sequence.advance())
                with patch('visual_link.presentation.time.monotonic', return_value=10.5):
                    self.assertIsNone(sequence.advance())
                time.sleep(0.05)
                with patch('visual_link.presentation.time.monotonic', return_value=15):
                    self.assertEqual(sequence.advance(), 'two.png')
                self.assertEqual(sequence.shown, 2)
                self.assertEqual(sequence.late, 1)
            finally:
                sequence.close()


if __name__ == '__main__':
    unittest.main()
