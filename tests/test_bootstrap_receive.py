"""Exercise receive queue overflow without capture hardware or multiprocessing."""
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from visual_link.bootstrap import _receive
from visual_link.core.control import Message


class BootstrapReceiveTests(unittest.TestCase):
    def run_receive(self, full=False, failure=False, faults=None):
        hello = Message('a'*32, None, 1, 0, 'HELLO', {})
        frame = SimpleNamespace(metadata=dict(index=0))
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        other = np.ones_like(image)
        control = np.full_like(image, 2)
        # Four probe images, HELLO, calibrated data, repeated pixels, bad data,
        # then repeated control. A full queue must keep processing captures.
        images = [image]*4+[control, image, image, other, control, control]
        def captures(config, metadata, stopped):
            yield from images
            if failure:
                raise OSError('capture disconnected')
        messages = Mock()
        if full:
            messages.put_nowait.side_effect = queue.Full
        with tempfile.TemporaryDirectory() as temporary, \
             patch('visual_link.bootstrap.capture_frames', side_effect=captures), \
             patch('visual_link.bootstrap.decode', side_effect=[hello, ValueError(), ValueError(), ValueError(), hello]) as decoder, \
             patch('visual_link.discovery.codec_timings', return_value=[1, 1, 1, 1]), \
             patch('visual_link.calibration.pattern', return_value=image), \
             patch('visual_link.calibration.measure', return_value={}), \
             patch('visual_link.calibration.receive_wire', return_value=b'wire'), \
             patch('visual_link.core.protocol.decode', side_effect=[frame, ValueError()]), \
             patch('visual_link.backends.image_sequence.write'):
            _receive(dict(timeout=1, _calibrate=True, test_faults=faults or {}),
                     temporary, messages, Mock(), SimpleNamespace(value=0))
            report = json.loads((Path(temporary)/'device.json').read_text())
        messages.cancel_join_thread.assert_called_once()
        messages.close.assert_called_once()
        self.assertEqual(decoder.call_count, 5)
        self.assertEqual(report['receive_stats']['control_cache_hits'], 1)
        return report, messages

    def test_overflow_counts_each_kind_and_continues(self):
        report, messages = self.run_receive(full=True)
        stats = report['receive_stats']
        self.assertEqual(stats['data_queue_dropped'], 1)
        self.assertEqual(stats['control_queue_dropped'], 3)
        self.assertEqual(stats['captured'], 10)
        self.assertEqual(stats['data_decoded'], 1)
        self.assertEqual(stats['control_decoded'], 3)
        self.assertEqual(stats['data_decode_failed'], 1)
        self.assertEqual(stats['duplicate_images'], 1)
        self.assertNotIn('error', report)
        self.assertEqual(messages.put_nowait.call_count, 4)

    def test_successful_enqueue_has_no_drops(self):
        report, messages = self.run_receive()
        self.assertEqual(report['receive_stats']['data_queue_dropped'], 0)
        self.assertEqual(report['receive_stats']['control_queue_dropped'], 0)
        self.assertEqual(messages.put_nowait.call_count, 4)

    def test_capture_failure_preserves_overflow_summary(self):
        report, _ = self.run_receive(full=True, failure=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], 'capture disconnected')
        self.assertEqual(report['receive_stats']['data_queue_dropped'], 1)
        self.assertEqual(report['receive_stats']['control_queue_dropped'], 3)

    def test_injected_drops_are_separate_from_queue_drops(self):
        report, messages = self.run_receive(full=True, faults=dict(drop_data_once=[0]))
        self.assertEqual(report['receive_stats']['data_queue_dropped'], 0)
        self.assertEqual(report['receive_stats']['control_queue_dropped'], 3)
        self.assertEqual(messages.put.call_args.args[0]['fault']['kind'], 'data_drop')

    def test_control_cache_owns_pixels_and_preserves_repeated_delivery(self):
        first = Message('a'*32, None, 1, 0, 'HELLO', {})
        second = Message('b'*32, None, 1, 0, 'HELLO', {})
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        def captures(*args):
            yield image
            yield image
            image[:] = 1  # A driver can refill the same array for the next read.
            yield image
        messages = Mock()
        with tempfile.TemporaryDirectory() as temporary, \
             patch('visual_link.bootstrap.capture_frames', side_effect=captures), \
             patch('visual_link.bootstrap.decode', side_effect=[first, second]) as decoder, \
             patch('visual_link.backends.image_sequence.write'):
            _receive(dict(timeout=1), temporary, messages, Mock(), SimpleNamespace(value=0))
            stats = json.loads((Path(temporary)/'device.json').read_text())['receive_stats']
        self.assertEqual(decoder.call_count, 2)
        self.assertEqual([c.args[0] for c in messages.put_nowait.call_args_list], [first, first, second])
        self.assertEqual(stats['control_cache_hits'], 1)
        self.assertEqual(stats['control_decoded'], 3)

    def test_cached_controls_still_obey_fault_suppression(self):
        report, messages = self.run_receive(full=True, faults=dict(drop_control_once=['HELLO']))
        self.assertEqual(report['receive_stats']['control_queue_dropped'], 0)
        # Suppressing HELLO also prevents calibration and subsequent data decode.
        self.assertEqual(messages.put_nowait.call_count, 0)

    def test_control_cache_can_be_disabled_for_paired_measurement(self):
        message = Message('a'*32, None, 1, 0, 'HELLO', {})
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        messages = Mock()
        with tempfile.TemporaryDirectory() as temporary, \
             patch('visual_link.bootstrap.capture_frames', return_value=iter([image, image])), \
             patch('visual_link.bootstrap.decode', return_value=message) as decoder, \
             patch('visual_link.backends.image_sequence.write'):
            _receive(dict(timeout=1, test_control_cache=False), temporary,
                     messages, Mock(), SimpleNamespace(value=0))
            stats = json.loads((Path(temporary)/'device.json').read_text())['receive_stats']
        self.assertEqual(decoder.call_count, 2)
        self.assertEqual(messages.put_nowait.call_count, 2)
        self.assertEqual(stats['control_cache_hits'], 0)


if __name__ == '__main__': unittest.main()
